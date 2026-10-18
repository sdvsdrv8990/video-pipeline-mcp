"""core/paths.py — слой путей рабочей области: одна дверь, настоящий путь, закрытые каталоги, запись без половин.

Дверь одна на весь сервер, а не проверка в каждом хендлере: путь судится настоящим (ссылки раскрыты, `..` убраны) и
по частям пути, а не по написанному. Запрет закрытых каталогов стоит в той же двери — иначе его пришлось бы помнить в
каждом хендлере, и первый забывший открыл бы дверь к ключам. Отказ — код реестра и факт `path`, текст даёт реестр.
Запись идёт через остаток с именем владельца: под настоящим именем лежит либо старый файл, либо новый целиком.
"""

import codecs
import errno
import json
import os
import re
import shutil
import stat
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock

from core import owner_only
from core.contracts import ContractError
from core.write_policy import WritePolicy

# Встроенный минимум: даже без конфига каталог секретов закрыт. Конфиг может ДОБАВИТЬ имена
# (`firewall.yaml → secret_paths`), но не может опустошить этот набор — иначе правка одной
# строки YAML тихо открывала бы ключи всем инструментам.
BUILTIN_SECRET_DIRS = frozenset({".secrets"})
_secret_dirs: set[str] = set(BUILTIN_SECRET_DIRS)

# Признак остатка `<файл>.<владелец>.part` — формат файлов на диске, а не настройка: сменённый признак оставил бы
# прежние остатки неузнанными, и листинг выдал бы их за целые файлы.
REMNANT = "part"
NOTE = "json"  # заметка остатка `<файл>.<владелец>.part.json` — часть того же остатка
_REMNANT_NAME = re.compile(rf"^.+\.[^./]+\.{REMNANT}(\.{NOTE})?$")
_UMASK = os.umask(0)
os.umask(_UMASK)


class PathRefused(ContractError):
    """Отказ двери: код реестра и факт `path` — путь, как его назвал вызывающий (относительно корня)."""

    CODE = ""

    def __init__(self, path: str | Path = ""):
        super().__init__(self.CODE, facts={"path": str(path)})


class PathEscapeError(PathRefused):
    """Настоящий путь выходит за корень: `..`, абсолютный путь, ссылка наружу."""

    CODE = "PATH_ESCAPE"


class SecretAccessError(PathRefused):
    """Путь ведёт в закрытый каталог секретов: у этой причины свой код и своя подсказка, а не «выход за область»."""

    CODE = "SECRET_ACCESS_DENIED"


class PlaceUnwritable(ContractError):
    """Диск отказал действию двери (FS-13): код реестра с операцией, путём от корня и причиной ОС — сырой `OSError` слой
    не выпускает. Путь и причина — `Files.unread`, тот же перевод, что у непрочитанного."""

    def __init__(self, operation: str, path: str, reason: str):
        super().__init__("PLACE_UNWRITABLE", facts={"operation": operation, "path": path, "reason": reason})


def configure_secret_dirs(names) -> set[str]:
    """Объявить закрытые каталоги (вызывается один раз при сборке контекста из firewall.yaml).

    Встроенный минимум остаётся всегда: конфиг только расширяет запрет.
    """
    global _secret_dirs
    extra = {str(n).strip() for n in (names or []) if str(n).strip()}
    _secret_dirs = set(BUILTIN_SECRET_DIRS) | extra
    return set(_secret_dirs)


def is_secret_path(path) -> bool:
    """Путь ведёт в закрытый каталог? Проверяется КАЖДЫЙ сегмент, а не только последний."""
    parts = Path(str(path)).parts
    return any(part in _secret_dirs for part in parts)


def _sync_dir(folder: Path) -> None:
    """Сброс каталога на диск: без него имя, появившееся через `os.replace`/`os.link`, может не пережить сбой питания."""
    if sys.platform == "win32":
        return  # каталог на Windows дескриптором не открыть (EACCES); так же atomicwrites `__init__.py:38-52`
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class Files:
    """Дверь к диску одного корня: всё, что сервер делает с файлом этого корня, начинается с `place`."""

    root: Path

    def place(self, path: str | Path, *, secrets: bool = False) -> Path:
        """Настоящий путь внутри корня. Закрытые каталоги — только с `secrets=True`: его передаёт одна `core/secrets.py`.

        Raises:
            PathEscapeError: настоящий путь вне корня
            SecretAccessError: путь ведёт в закрытый каталог
        """
        root = Path(self.root).resolve()
        # realpath, а не Path.resolve: на 3.11–3.12 петля ссылок у resolve — RuntimeError, с 3.13 resolve — тот же realpath.
        target = Path(os.path.realpath(root / path))
        if target != root and not target.is_relative_to(root):
            raise PathEscapeError(self.name(path))
        if not secrets and is_secret_path(target.relative_to(root) if target != root else ""):
            raise SecretAccessError(self.name(path))
        return target

    def name(self, path: str | Path) -> str:
        """Путь, как его называют клиенту: от корня ("" — сам корень), чтобы ответ не нёс место сервера, и через `/` на
        любой ОС. Сверка с корнем — сначала по написанному (ссылка внутри корня остаётся своим именем), затем по
        настоящему пути (другой псевдоним того же корня). Абсолютный путь вне корня приходит от клиента и остаётся его
        же текстом."""
        named = Path(path)
        if named.is_absolute():
            real = Path(self.root).resolve()
            for base, held in ((Path(self.root), named), (real, named), (real, Path(os.path.realpath(named)))):
                if held.is_relative_to(base):
                    rel = held.relative_to(base).as_posix()
                    return "" if rel == "." else rel
        return named.as_posix()

    def unread(self, path: str | Path, err: OSError) -> dict:
        """Непрочитанное фактами: путь от корня и код ОС (`EACCES`); текст ОС клиенту не уходит. Ошибка без кода ОС
        (`errno` пуст) — имя её класса."""
        code = errno.errorcode.get(err.errno) if err.errno is not None else None
        return {"path": self.name(path), "reason": code or type(err).__name__}

    def walk(self, path: str | Path = "") -> "Walk":
        """Обход каталога через дверь (см. `Walk`)."""
        return Walk(self, path)

    def remnant(self, path: str | Path, owner: str, *, secrets: bool = False) -> Path:
        """Остаток записи под именем владельца (FS-08): `<файл>.<владелец>.part` рядом с итоговым. Владелец — задание или
        установка (без точек и `/`); что делать с остатком — докачать, выбросить — решает вызывающий."""
        if not owner or "." in owner or "/" in owner:
            raise ValueError(f"владелец остатка — одно слово без точек и '/': {owner!r}")
        target = self.place(path, secrets=secrets)
        # Имя рядом с итоговым, прошедшим дверь, — без второго раскрытия: ссылку на месте остатка снимают как ссылку.
        return target.with_name(f"{target.name}.{owner}.{REMNANT}")

    def publish(self, path: str | Path, owner: str, *, secrets: bool = False) -> Path:
        """Остаток владельца становится файлом (FS-06): сброс на диск, `os.replace` на место, заметка остатка снимается,
        сброс каталога."""
        target, part = self.place(path, secrets=secrets), self.remnant(path, owner, secrets=secrets)
        with open(part, "r+b") as f:  # на Windows fsync = FlushFileBuffers: дескриптор только на чтение — EBADF
            os.fsync(f.fileno())
        os.replace(part, target)
        self._note(path, owner, secrets).unlink(missing_ok=True)
        _sync_dir(target.parent)
        return target

    def replace(self, path: str | Path, data: bytes | str, *, mode: int | None = None, secrets: bool = False,
                policy: WritePolicy | None = None) -> Path:
        """Заменить файл целиком (FS-06): содержимое и права готовы в остатке рядом до `os.replace`, затем сброс каталога.
        Читатель видит старый файл или новый; обрыв оставляет остаток, а не половину под настоящим именем. `policy` —
        путь назвал клиент: тип, размер, содержимое и место на диске судятся до первого байта (FS-12/15)."""
        target = self.place(path, secrets=secrets)
        if policy is not None:
            policy.admit(self.name(target), data, at=target)
        part = self._fresh(target, data, self._mode(target) if mode is None else mode, secrets)
        try:
            os.replace(part, target)
        except OSError as e:  # на Windows файл, открытый без FILE_SHARE_DELETE, не заменить: под именем — прежний
            part.unlink(missing_ok=True)
            raise PlaceUnwritable("замена", **self.unread(path, e)) from e
        _sync_dir(target.parent)
        return target

    def create(self, path: str | Path, data: bytes | str, *, mode: int | None = None, secrets: bool = False,
               policy: WritePolicy | None = None) -> bool:
        """Создать файл, только если его нет (FS-07): содержимое и права готовы до появления имени, а появляется оно
        `os.link`, который не затирает. False — файл уже был, его содержимое не тронуто. `policy` — как у `replace`."""
        target = self.place(path, secrets=secrets)
        if policy is not None:
            policy.admit(self.name(target), data, at=target)
        part = self._fresh(target, data, self._mode(target) if mode is None else mode, secrets)
        try:
            os.link(part, target)
        except FileExistsError:
            return False
        finally:
            part.unlink()
        _sync_dir(target.parent)
        return True

    def _fresh(self, target: Path, data: bytes | str, mode: int, secrets: bool) -> Path:
        """Остаток с содержимым на диске: создан исключительно (`O_EXCL`), права выставлены до первого байта."""
        part = self.remnant(target, uuid.uuid4().hex[:12], secrets=secrets)
        part.parent.mkdir(parents=True, exist_ok=True)
        fd = owner_only.create(part, mode)
        try:
            with os.fdopen(fd, "wb") as f:
                # chmod по дескриптору — где он поддержан (на Windows — с 3.13), иначе по пути: файл уже создан и пуст.
                os.chmod(f.fileno() if os.chmod in os.supports_fd else part, mode)
                f.write(data.encode("utf-8") if isinstance(data, str) else data)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            part.unlink(missing_ok=True)  # файл уже закрыт выходом из with: Windows не удаляет открытый
            raise
        return part

    @staticmethod
    def _mode(target: Path) -> int:
        """Права заменяемого файла сохраняются; новый — как у `open()`: 0o666 без маски процесса."""
        return stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o666 & ~_UMASK

    def note(self, path: str | Path, owner: str, facts: dict, *, secrets: bool = False) -> Path:
        """Заметка остатка (FS-08): `<файл>.<владелец>.part.json` рядом с ним — то, что вызывающему нужно для продолжения
        (валидатор докачки, хост). Пишется заменой; снимается вместе с остатком в `publish` и `drop`."""
        return self.replace(self._note(path, owner, secrets), json.dumps(facts, ensure_ascii=False, sort_keys=True),
                            secrets=secrets)

    def noted(self, path: str | Path, owner: str, *, secrets: bool = False) -> dict | None:
        """Заметка остатка или None — её нет или она нечитаема; что делать без неё, решает вызывающий."""
        try:
            facts = json.loads(self._note(path, owner, secrets).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return facts if isinstance(facts, dict) else None

    def drop(self, path: str | Path, owner: str, *, secrets: bool = False) -> None:
        """Убрать свой остаток и его заметку; чужие не трогаются — имя строится от владельца, ссылка снимается как ссылка."""
        self.remnant(path, owner, secrets=secrets).unlink(missing_ok=True)
        self._note(path, owner, secrets).unlink(missing_ok=True)

    def _note(self, path: str | Path, owner: str, secrets: bool) -> Path:
        part = self.remnant(path, owner, secrets=secrets)
        return part.with_name(f"{part.name}.{NOTE}")

    def lock(self, path: str | Path, *, secrets: bool = False) -> FileLock:
        """Замок ОС на «прочитать — изменить — записать» общего для процессов файла (FS-09) — на отдельном `<файл>.lock`:
        данные меняет `os.replace`, и замок на них ушёл бы со старым inode. Умершего держателя снимает ядро. Мягкого
        замка нет: где замки ОС не работают — отказ, а не тихая подмена; файл замка остаётся — это не владение."""
        target = self.place(path, secrets=secrets)
        return FileLock(target.with_name(f"{target.name}.lock"), fallback_to_soft=False, preserve_lock_file=True)

    def named(self, path: str | Path, *, secrets: bool = False) -> Path:
        """Путь, как его назвали, без раскрытия последнего звена (FS-14): каталог проходит дверь, само имя — нет, поэтому
        удаление и перенос действуют на названную ссылку, а не сквозь неё. Корень и `..` раскрываются дверью целиком."""
        written = Path(path)
        if written.name in ("", ".", ".."):
            return self.place(written, secrets=secrets)
        if written == Path(self.root).resolve():  # ответ двери на корень: его родитель вне корня, судить его — ложный выход
            return written
        if not secrets and is_secret_path(written.name):
            raise SecretAccessError(self.name(path))
        return self.place(written.parent, secrets=secrets) / written.name

    def remove(self, path: str | Path, *, secrets: bool = False) -> str:
        """Удалить названное (FS-14): ссылку — как ссылку, файл — как файл, `rmtree` — только по настоящему каталогу и там,
        где он не ходит по ссылкам внутри; на Windows — и без этого: в junction он не заходит, гонка подмены принята
        (FS-14). Корень не удаляется. Возвращает, что снято: link | file | dir."""
        target = self.named(path, secrets=secrets)
        if target == Path(self.root).resolve():
            raise ContractError("VALIDATION_ERROR", errors=[{"field": "path", "rule": "not_root"}])
        mode = self._mode_of(target, path)
        try:
            if stat.S_ISDIR(mode):
                if not shutil.rmtree.avoids_symlink_attacks and sys.platform != "win32":
                    raise OSError(errno.ENOTSUP, "rmtree на этой платформе идёт по ссылкам внутри дерева")
                shutil.rmtree(target)
                kind = "dir"
            else:
                target.unlink()
                kind = "link" if stat.S_ISLNK(mode) else "file"
        except OSError as e:  # прерванный rmtree оставляет часть дерева: отказ называет путь, снятое не восстановить
            raise PlaceUnwritable("удаление", **self.unread(path, e)) from e
        _sync_dir(target.parent)
        return kind

    def _mode_of(self, real: Path, path: str | Path) -> int:
        """Режим названного без перехода по ссылке; названного нет — FILE_NOT_FOUND с путём от корня: «есть ли источник»
        судит дверь, а не вызывающий заранее (проверка до действия — гонка)."""
        try:
            return os.lstat(real).st_mode
        except FileNotFoundError:
            raise ContractError("FILE_NOT_FOUND", facts={"path": self.name(path)}) from None

    def move(self, src: str | Path, dst: str | Path, *, secrets: bool = False, policy: WritePolicy | None = None) -> Path:
        """Перенести названное без затирания (FS-14): файл и ссылка — `os.link` (он не затирает) и снятие старого имени;
        каталог — исключительно занять имя назначения пустым каталогом и встать на него `os.rename` (на Windows `os.rename`
        сам не затирает). Назначение занято —
        FILE_EXISTS, чужое не тронуто; корень и каталог внутрь самого себя — отказ ввода до первого действия. `policy` —
        у файла судится тип нового имени: перенос в `run.sh` не обходит список типов."""
        source, target = self.named(src, secrets=secrets), self.named(dst, secrets=secrets)
        if source == Path(self.root).resolve():
            raise ContractError("VALIDATION_ERROR", errors=[{"field": "source", "rule": "not_root"}])
        directory = stat.S_ISDIR(self._mode_of(source, src))
        if directory and source in target.parents:
            raise ContractError("VALIDATION_ERROR", errors=[{"field": "destination", "rule": "inside_source"}])
        if policy is not None and source.is_file():
            policy.admit(self.name(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        # На Windows rename не затирает занятое имя (FileExistsError), занимать его заранее нельзя: заглушку он не заменит.
        held = directory and sys.platform != "win32"
        try:
            if held:
                os.mkdir(target)
            elif directory:
                os.rename(source, target)
            else:
                os.link(source, target, follow_symlinks=False)
        except FileExistsError:
            raise ContractError("FILE_EXISTS", facts={"path": self.name(dst)}) from None
        except OSError as e:
            raise PlaceUnwritable("перенос", **self.unread(src, e)) from e
        if held:
            try:
                os.rename(source, target)
            except BaseException as e:
                os.rmdir(target)
                if isinstance(e, OSError):
                    raise PlaceUnwritable("перенос", **self.unread(src, e)) from e
                raise
        elif not directory:
            try:
                os.unlink(source)
            except OSError as e:  # старое имя не снялось (на Windows его держат открытым): новое снимается — половины нет
                os.unlink(target)
                raise PlaceUnwritable("перенос", **self.unread(src, e)) from e
        _sync_dir(target.parent)
        _sync_dir(source.parent)
        return target

    def read(self, path: str | Path, limit: int, *, whole: bool = False, errors: str = "strict") -> "Text":
        """Текст файла не длиннее `limit` байт (FS-12/13): голова и признак обрезания, хвост не читается. `whole` — файл
        нужен целиком (память, поиск): больше предела — FILE_TOO_LARGE. Каталог — VALIDATION_ERROR not_file, не UTF-8 —
        FILE_NOT_TEXT; путь в отказе — от корня."""
        target = self.place(path)
        if target.is_dir():
            raise ContractError("VALIDATION_ERROR", errors=[{"field": "path", "rule": "not_file"}])
        with open(target, "rb") as f:
            head = f.read(limit + 1)
            size = os.fstat(f.fileno()).st_size
        truncated = len(head) > limit
        if truncated and whole:
            raise ContractError("FILE_TOO_LARGE", facts={"path": self.name(path), "size": size, "limit": limit})
        try:
            # Без `final` декодер держит разрезанный срезом символ недочитанным, а не зовёт его порчей.
            text = codecs.getincrementaldecoder("utf-8")(errors).decode(head[:limit], final=not truncated)
        except UnicodeDecodeError:
            raise ContractError("FILE_NOT_TEXT", facts={"path": self.name(path)}) from None
        return Text(text, size, truncated)


@dataclass(frozen=True)
class Text:
    """Прочитанное `Files.read`: `text` — не длиннее предела, `file_bytes` — размер файла на диске, `truncated` — хвост
    не прочитан."""

    text: str
    file_bytes: int
    truncated: bool


@dataclass(frozen=True)
class Entry:
    """Элемент обхода: `path` — имя для клиента (от корня), `real` — откуда брать размер, время и содержимое."""

    path: str
    real: Path
    kind: str  # file | dir | link | remnant — ссылка на каталог не раскрывается, остаток не выдаётся за целый файл


class Walk:
    """Обход через дверь: каждый элемент судится `place` до того, как отдан хоть один его факт; в ссылку на каталог не
    спускается; остаток записи помечен; нечитаемый каталог назван в `unreadable` (путь от корня и код ОС)."""

    def __init__(self, files: Files, path: str | Path):
        self.files = files
        self.start = files.place(path)
        self.path = files.name(self.start)
        self.unreadable: list[dict] = []

    def __iter__(self) -> Iterator[Entry]:
        return self._below(self.start)

    def _below(self, folder: Path) -> Iterator[Entry]:
        try:
            names = sorted(os.listdir(folder))
        except OSError as e:
            self.unreadable.append(self.files.unread(folder, e))
            return
        for name in names:
            written = folder / name
            try:
                real = self.files.place(written)
            except PathRefused:
                continue
            if real.is_dir():
                kind = "link" if written.is_symlink() else "dir"
            elif real.is_file():
                kind = "remnant" if _REMNANT_NAME.match(name) else "file"
            else:
                continue
            yield Entry(self.files.name(written), real, kind)
            if kind == "dir":
                yield from self._below(real)
