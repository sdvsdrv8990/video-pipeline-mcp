#!/usr/bin/env python3
"""tests/quick/test_door_platform.py — перенос через дверь слоя на семантике Windows, мерится на Linux.

На Windows `os.rename` при занятом имени всегда бросает FileExistsError (CPython `Doc/library/os.rst`, `rename`), на Unix
пустой каталог назначения заменяется молча. Здесь семантика Windows подставлена на время вызова: `sys.platform` —
"win32", `os.rename` — тот же перенос, но занятое имя — FileExistsError. Законная пара — платформа не Windows, тоже
подставленная: на раннере Windows настоящая платформа её не даёт. Отказ ОС на двери (FS-13) — подставленный вызов ОС
бросает то, что бросает Windows, когда файл держат открытым. Чтение (раздел 7) — подставной kernel32: с какими флагами
совместного доступа дверь открывает файл и проходит ли замена, пока он открыт. Что подстановка не меряет (NTFS, жёсткие
ссылки, права, настоящий CreateFileW) — мерит CI на Windows.
"""
import ctypes
import errno
import os
import shutil
import sys
import tempfile
import types
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import auth, owner_only, paths
from core.contracts import ContractError
from core.declaration import contents
from core.engine import Engine
from core.ids.link_registry import LinkRegistry
from core.jobs import Tasks
from core.paths import Files
from core.reactions import Reactions
from core.runner.supervisor import RunnerSupervisor
from core.search.lines import numbered
from core.secrets import ChannelSecrets
from core.state.state_manager import StateManager

_checks = 0
_fails: list[str] = []


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


@contextmanager
def windows():
    """Платформа, перенос и удаление — как на Windows: занятое имя назначения rename не заменяет, rmtree не устойчив к
    подмене ссылкой (`shutil.rst`: avoids_symlink_attacks истинно только при функциях по дескрипторам каталогов),
    жёсткой ссылки на ссылку-каталог нет: os.symlink на Windows делает ссылку типа цели, ссылка на каталог — каталог
    (`os.rst`, symlink), а его жёсткой ссылкой не связать — на раннере CI 098ddb9 перенос ссылки на файл прошёл, на
    каталог — нет."""
    real_rename, real_link, real_platform, real_safe = os.rename, os.link, sys.platform, shutil.rmtree.avoids_symlink_attacks

    def rename(src, dst, **kw):
        if os.path.lexists(dst):
            raise FileExistsError(183, "Cannot create a file when that file already exists", str(src), None, str(dst))
        real_rename(src, dst, **kw)

    def link(src, dst, **kw):
        if os.path.islink(src) and os.path.isdir(src):
            raise PermissionError(errno.EACCES, "Access is denied", str(src), None, str(dst))
        real_link(src, dst, **kw)

    os.rename, os.link, sys.platform, shutil.rmtree.avoids_symlink_attacks = rename, link, "win32", False
    try:
        yield
    finally:
        os.rename, os.link, sys.platform, shutil.rmtree.avoids_symlink_attacks = real_rename, real_link, real_platform, \
            real_safe


@contextmanager
def unix(safe: bool):
    """Платформа не Windows; `safe` — устойчив ли rmtree к подмене ссылкой (функции по дескрипторам каталогов). На
    раннере Windows сброс каталога на диск подставлен тоже: каталог там дескриптором не открыть (EACCES, CI 098ddb9), а
    мерится решение двери об удалении, не сброс."""
    real_platform, real_safe, real_sync = sys.platform, shutil.rmtree.avoids_symlink_attacks, paths._sync_dir
    sys.platform, shutil.rmtree.avoids_symlink_attacks = "linux", safe
    if real_platform == "win32":
        paths._sync_dir = lambda folder: None
    try:
        yield
    finally:
        sys.platform, shutil.rmtree.avoids_symlink_attacks, paths._sync_dir = real_platform, real_safe, real_sync


@contextmanager
def held(name: str, *paths: Path):
    """Вызов ОС `os.<name>` по любому из `paths` отказывает, как Windows по файлу, открытому без FILE_SHARE_DELETE."""
    owner = os if hasattr(os, name) else shutil
    real = getattr(owner, name)
    held_paths = {os.path.realpath(p) for p in paths}

    def refused(*args, **kw):
        if any(os.path.realpath(str(a)) in held_paths for a in args[:2]):
            raise PermissionError(errno.EACCES, "Access is denied", str(args[0]))
        return real(*args, **kw)

    refused.avoids_symlink_attacks = getattr(real, "avoids_symlink_attacks", False)  # type: ignore[attr-defined]
    setattr(owner, name, refused)
    try:
        yield
    finally:
        setattr(owner, name, real)


def moved(files: Files, src: str, dst: str):
    """Итог переноса: путь от корня или код отказа; чужое исключение — его класс (на двери это INTERNAL_ERROR)."""
    try:
        return files.name(files.move(src, dst))
    except ContractError as e:
        return e.code, e.facts
    except Exception as e:
        return type(e).__name__


ws = Path(tempfile.mkdtemp(prefix="vpm-door-platform-"))
files = Files(ws)

print("== 1. Перенос каталога на Windows: имя свободно — переезжает целиком ==")
(ws / "videos" / "clip").mkdir(parents=True)
(ws / "videos" / "clip" / "a.txt").write_text("кадр", encoding="utf-8")
with windows():
    got = moved(files, "videos/clip", "videos/clip_v2")
check("каталог переехал, отказа нет", got == "videos/clip_v2", got)
check("содержимое на новом месте, старого имени нет",
      (ws / "videos/clip_v2/a.txt").is_file() and (ws / "videos/clip_v2/a.txt").read_text(encoding="utf-8") == "кадр"
      and not (ws / "videos/clip").exists())
if not (ws / "videos/clip_v2").is_dir():   # перенос не лёг — дальше переносить нечего, следующие проверки краснеют сами
    (ws / "videos" / "clip").rename(ws / "videos" / "clip_v2")

print("== 2. Перенос каталога на Windows: имя занято — FILE_EXISTS, обе стороны не тронуты ==")
(ws / "videos" / "busy").mkdir()
(ws / "videos" / "busy" / "b.txt").write_text("чужое", encoding="utf-8")
with windows():
    got = moved(files, "videos/clip_v2", "videos/busy")
check("занятое имя — FILE_EXISTS с путём от корня", got == ("FILE_EXISTS", {"path": "videos/busy"}), got)
check("источник и назначение как были",
      (ws / "videos/clip_v2/a.txt").is_file() and (ws / "videos/busy/b.txt").read_text(encoding="utf-8") == "чужое"
      and sorted(p.name for p in (ws / "videos/busy").iterdir()) == ["b.txt"])
(ws / "videos" / "empty").mkdir()
with windows():
    got = moved(files, "videos/clip_v2", "videos/empty")
check("пустой каталог на месте назначения — тоже FILE_EXISTS: на Windows rename его не заменяет",
      got == ("FILE_EXISTS", {"path": "videos/empty"}) and (ws / "videos/clip_v2/a.txt").is_file(), got)

print("== 3. Законная пара на Unix: тот же перенос своей семантикой ==")
check("Unix: каталог переехал", moved(files, "videos/clip_v2", "videos/clip_v3") == "videos/clip_v3")
check("Unix: пустой каталог на месте назначения — FILE_EXISTS, а не тихая замена",
      moved(files, "videos/clip_v3", "videos/empty") == ("FILE_EXISTS", {"path": "videos/empty"})
      and (ws / "videos/clip_v3/a.txt").is_file())

print("== 3b. Перенос ссылки на Windows (CI 098ddb9): переезжает сама ссылка, цель на месте, занятое не затирается ==")
os.symlink(ws / "videos" / "clip_v3", ws / "videos" / "ldir", target_is_directory=True)
(ws / "videos" / "f.txt").write_text("файл", encoding="utf-8")
os.symlink(ws / "videos" / "f.txt", ws / "videos" / "lfile")
for kind, name in (("каталог", "ldir"), ("файл", "lfile")):
    with windows():
        got = moved(files, f"videos/{name}", f"videos/{name}_moved")
    check(f"Windows: ссылка на {kind} переехала ссылкой, цель на месте, старого имени нет",
          got == f"videos/{name}_moved" and (ws / f"videos/{name}_moved").is_symlink()
          and not os.path.lexists(ws / f"videos/{name}") and (ws / "videos/clip_v3/a.txt").is_file()
          and (ws / "videos/f.txt").read_text(encoding="utf-8") == "файл", got)
with windows():
    got = moved(files, "videos/ldir_moved", "videos/busy")
check("Windows: ссылка на занятое имя — FILE_EXISTS, ни ссылка, ни занятое не тронуты",
      got == ("FILE_EXISTS", {"path": "videos/busy"}) and (ws / "videos/ldir_moved").is_symlink()
      and (ws / "videos/busy/b.txt").read_text(encoding="utf-8") == "чужое", got)

print("== 4. Удаление каталога на Windows: непустой снимается целиком, ссылка внутри — как ссылка ==")


def removed(path: str):
    try:
        return files.remove(path)
    except ContractError as e:
        return e.code, e.facts
    except Exception as e:
        return type(e).__name__


_outside = Path(tempfile.mkdtemp(prefix="vpm-door-outside-"))
(_outside / "keep.txt").write_text("чужое", encoding="utf-8")
(ws / "videos" / "gone" / "sub").mkdir(parents=True)
(ws / "videos" / "gone" / "sub" / "c.txt").write_text("кадр", encoding="utf-8")
os.symlink(_outside, ws / "videos" / "gone" / "link", target_is_directory=True)
with windows():
    got = removed("videos/gone")
check("Windows: непустой каталог снят (dir), а не OSError ENOTSUP → INTERNAL_ERROR",
      got == "dir" and not (ws / "videos/gone").exists(), got)
check("Windows: каталог за ссылкой внутри не тронут — снята ссылка, а не то, на что она указывает",
      (_outside / "keep.txt").read_text(encoding="utf-8") == "чужое")
(ws / "videos" / "kept" / "sub").mkdir(parents=True)
with unix(safe=False):
    got = removed("videos/kept")
check("не Windows и rmtree не устойчив — отказ кодом слоя: исключение Windows не открывает другие платформы",
      got == ("PLACE_UNWRITABLE", {"operation": "удаление", "path": "videos/kept", "reason": "ENOTSUP"})
      and (ws / "videos/kept/sub").is_dir(), got)
with unix(safe=True):
    got = removed("videos/kept")
check("Unix: устойчивый rmtree снимает каталог", got == "dir" and not (ws / "videos/kept").exists(), got)

print("== 5. Диск отказал действию двери (D08/D09, FS-13): код слоя с путём от корня, половин нет ==")
engine = Engine(reactions=Reactions(ROOT / "config" / "server_reactions.yaml"))
(ws / "notes").mkdir()
(ws / "notes" / "held.md").write_text("СТАРОЕ", encoding="utf-8")
refusal: ContractError | None = None
with held("replace", ws / "notes" / "held.md"):
    try:
        files.replace("notes/held.md", "НОВОЕ")
        escaped = "отказа нет"
    except ContractError as e:
        refusal, escaped = e, ""
    except Exception as e:  # сырое исключение мимо слоя — улика проверки, а не обрыв набора
        escaped = type(e).__name__
check("замена держимого файла — PLACE_UNWRITABLE: операция, путь от корня, причина ОС",
      refusal is not None and (refusal.code, refusal.facts)
      == ("PLACE_UNWRITABLE", {"operation": "замена", "path": "notes/held.md", "reason": "EACCES"}),
      (refusal and (refusal.code, refusal.facts)) or escaped)
check("под именем прежнее содержимое, остатка записи нет",
      (ws / "notes" / "held.md").read_text(encoding="utf-8") == "СТАРОЕ"
      and sorted(p.name for p in (ws / "notes").iterdir()) == ["held.md"], sorted(p.name for p in (ws / "notes").iterdir()))
said = engine.refusal(refusal).error if refusal is not None else None
check("на двери движка: текст шаблона с операцией и путём от корня, пути хоста нет",
      said is not None and "замена «notes/held.md»" in said.message and str(ws) not in said.message,
      said and said.message)
(ws / "notes" / "move.md").write_text("ПЕРЕНОС", encoding="utf-8")
with held("unlink", ws / "notes" / "move.md"):
    got = moved(files, "notes/move.md", "notes/moved.md")
check("перенос, старое имя которого не снять, — PLACE_UNWRITABLE с путём источника",
      got == ("PLACE_UNWRITABLE", {"operation": "перенос", "path": "notes/move.md", "reason": "EACCES"}), got)
check("половины нет: новое имя снято, старое на месте с содержимым",
      not (ws / "notes" / "moved.md").exists() and (ws / "notes" / "move.md").read_text(encoding="utf-8") == "ПЕРЕНОС")
(ws / "notes" / "tree" / "sub").mkdir(parents=True)
with held("rmtree", ws / "notes" / "tree"):
    got = removed("notes/tree")
check("удаление, которому отказал диск, — PLACE_UNWRITABLE, а не сырой PermissionError → INTERNAL_ERROR",
      got == ("PLACE_UNWRITABLE", {"operation": "удаление", "path": "notes/tree", "reason": "EACCES"}), got)

print("== 6. Названного нет (D10): «есть ли источник» судит дверь — FILE_NOT_FOUND с путём от корня, а не сырой OSError ==")
check("перенос несуществующего — FILE_NOT_FOUND с путём источника",
      moved(files, "notes/нет", "notes/куда") == ("FILE_NOT_FOUND", {"path": "notes/нет"}))
check("удаление несуществующего — FILE_NOT_FOUND с путём", removed("notes/нет") == ("FILE_NOT_FOUND", {"path": "notes/нет"}))

print("== 7. Читатель не держит замену (FS-06; CI dcb0c41 — EACCES замены записи задания на Windows) ==")
_GONE = ctypes.c_void_p(-1).value          # INVALID_HANDLE_VALUE при restype c_void_p
_READ, _WRITE, _DELETE = 0x1, 0x2, 0x4     # FILE_SHARE_*


@contextmanager
def win32_open(calls: list):
    """Открытие и замена — как на Windows, без настоящей ОС под ними: подставной kernel32.CreateFileW пишет свои
    аргументы в `calls` и отдаёт fd копии файла; os.replace отказывает EACCES, пока имя держит открытие без
    FILE_SHARE_DELETE (Win32 CreateFileW, dwShareMode); нет файла — ERROR_FILE_NOT_FOUND. Отдаёт `crt(path)` — открытие
    `open()` CPython на Windows (_wsopen_s, _SH_DENYNO: READ|WRITE без DELETE)."""
    opened: list[tuple[int, tuple[int, int], int, str]] = []   # fd копии, её (dev, ino), флаги, настоящий путь имени
    copies: list[str] = []
    last = [0]

    def hold(name, share: int) -> int:
        fd, copy = tempfile.mkstemp(dir=scratch)
        copies.append(copy)
        os.write(fd, Path(name).read_bytes())
        os.lseek(fd, 0, os.SEEK_SET)
        st = os.fstat(fd)
        opened.append((fd, (st.st_dev, st.st_ino), share, os.path.realpath(name)))
        return fd

    def create(name, access, share, security, disposition, attributes, template):
        calls.append((os.path.realpath(name), access, share, disposition))
        if not os.path.isfile(name):
            last[0] = 2
            return _GONE
        return hold(name, share)

    def replace(src, dst, **kw):
        for fd, ident, share, name in opened:
            try:
                alive = (lambda st: (st.st_dev, st.st_ino))(os.fstat(fd)) == ident
            except OSError:
                alive = False
            if alive and name == os.path.realpath(dst) and not share & _DELETE:
                raise PermissionError(errno.EACCES, "Access is denied", str(src), None, str(dst))
        real_replace(src, dst, **kw)

    @contextmanager
    def crt(path):
        fd = hold(path, _READ | _WRITE)
        try:
            yield
        finally:
            os.close(fd)

    saved = {n: getattr(ctypes, n, None) for n in ("WinDLL", "WinError", "get_last_error")}
    real_replace, real_platform, real_msvcrt = os.replace, sys.platform, sys.modules.get("msvcrt")
    ctypes.WinDLL = lambda name, use_last_error=False: types.SimpleNamespace(CreateFileW=create)  # type: ignore[misc]
    ctypes.WinError = lambda code=None, descr=None: OSError(errno.ENOENT if code == 2 else errno.EACCES, descr)
    ctypes.get_last_error = lambda: last[0]
    sys.modules["msvcrt"] = types.SimpleNamespace(open_osfhandle=lambda handle, flags: handle)  # type: ignore[assignment]
    os.replace, sys.platform = replace, "win32"
    try:
        yield crt
    finally:
        os.replace, sys.platform = real_replace, real_platform
        for n, v in saved.items():
            if v is not None:
                setattr(ctypes, n, v)
            elif hasattr(ctypes, n):
                delattr(ctypes, n)
        if real_msvcrt is None:
            sys.modules.pop("msvcrt", None)
        else:
            sys.modules["msvcrt"] = real_msvcrt
        for copy in copies:
            Path(copy).unlink(missing_ok=True)


def replaced(path: str, data: str):
    """"" — замена прошла; иначе (код, факты) отказа слоя или имя сырого исключения."""
    try:
        files.replace(path, data)
        return ""
    except ContractError as e:
        return e.code, e.facts
    except Exception as e:  # сырое исключение мимо слоя — улика проверки, а не обрыв набора
        return type(e).__name__


scratch = tempfile.mkdtemp(prefix="door-win32-")
(ws / "tasks").mkdir()
record = ws / "tasks" / "t1.json"
record.write_text('{"v": 1}', encoding="utf-8")
calls: list = []
with win32_open(calls) as crt:
    with crt(record):
        sick = replaced("tasks/t1.json", '{"v": 2}')
    with paths.open_shared(record) as reader:
        healthy = replaced("tasks/t1.json", '{"v": 3}')
        seen = reader.read()
    got_bytes = files.read_bytes("tasks/t1.json")
    try:
        files.read_bytes("tasks/нет.json")
        missing = "отказа нет"
    except FileNotFoundError:
        missing = "FileNotFoundError"
    except Exception as e:
        missing = type(e).__name__
check("модель Windows жива: читатель open() без FILE_SHARE_DELETE держит замену — PLACE_UNWRITABLE, как красил CI",
      sick == ("PLACE_UNWRITABLE", {"operation": "замена", "path": "tasks/t1.json", "reason": "EACCES"}), sick)
check("читатель двери открыт — замена проходит, под именем новое, читатель дочитывает прежнее",
      healthy == "" and record.read_text(encoding="utf-8") == '{"v": 3}' and seen == b'{"v": 1}', (healthy, seen))
check("дверь просит у ОС чтение существующего со всеми флагами совместного доступа (READ|WRITE|DELETE)",
      calls[-2] == (os.path.realpath(record), 0x80000000, _READ | _WRITE | _DELETE, 3) and got_bytes == b'{"v": 3}',
      calls[-2:])
check("нет файла — FileNotFoundError, как ждут читатели записей (ERROR_FILE_NOT_FOUND → ENOENT)",
      missing == "FileNotFoundError", missing)

# Читатели файлов, которые сервер пишет заменой: каждый — через дверь (тот же вызов ОС) и с тем же прочитанным.
(ws / "ch").mkdir()
(ws / "ch" / "read.json").write_text('{"s": 1}', encoding="utf-8")
(ws / "ch" / "write.json").write_text('[{"op": 1}]', encoding="utf-8")
(ws / "_id_registry.json").write_text('{"entities": {}}', encoding="utf-8")
(ws / "runner").mkdir()
(ws / "runner" / "state.json").write_text('{"token": "t"}', encoding="utf-8")
# Ожидания ниже — байты с «\n»: без newline Windows пишет «\r\n», и краснела бы запись, а не дверь (CI c9249e0 §7).
(ws / "installed.yaml").write_text("models: []\n", encoding="utf-8", newline="\n")
(ws / "notes" / "lines.md").write_text("раз\nдва\n", encoding="utf-8", newline="\n")
(ws / ".env").write_text("A=1\n", encoding="utf-8")
keys = ChannelSecrets(ws, ws / "home")
(ws / "home").mkdir()
keys.set("ch", "p", "ключ")
tasks = Tasks.__new__(Tasks)
tasks.files = files
state = StateManager(ws)
runner = RunnerSupervisor(types.SimpleNamespace(need=lambda *keys, kind: "runner/state.json", config={}), ws)
readers = {
    "Tasks._load": (lambda: tasks._load("tasks/t1.json"), "tasks/t1.json", ValueError),
    "StateManager.read_snapshot": (lambda: state.read_snapshot("ch"), "ch/read.json", {"s": 1}),
    "StateManager.read_queue": (lambda: state.read_queue("ch"), "ch/write.json", [{"op": 1}]),
    "StateManager.push_to_queue": (lambda: state.push_to_queue("ch", {"op": 2}), "ch/write.json", True),
    "StateManager.clear_queue": (lambda: state.clear_queue("ch"), "ch/write.json", 2),
    "StateManager.execute_queue": (lambda: state.execute_queue("ch"), "ch/write.json", []),
    "LinkRegistry._load": (lambda: LinkRegistry(ws)._load(), "_id_registry.json", {"entities": {}}),
    "RunnerSupervisor._read": (lambda: runner._read(), "runner/state.json", {"token": "t"}),
    "declaration.contents": (lambda: contents(ws / "installed.yaml"), "installed.yaml", b"models: []\n"),
    "search.lines.numbered": (lambda: list(numbered(ws / "notes" / "lines.md", 100, name="lines.md", errors="strict")),
                              "notes/lines.md", [(1, "раз"), (2, "два")]),
    "auth._read_env": (lambda: auth._read_env(ws / ".env"), ".env", {"A": "1"}),
    "ChannelSecrets.get": (lambda: keys.get("ch", "p"), "ch/.secrets/provider_keys.enc", "ключ"),
    "Files.read": (lambda: files.read("notes/lines.md", 100).text, "notes/lines.md", "раз\nдва\n"),
}
real_restrict = owner_only.restrict
owner_only.restrict = lambda path, mode: None   # права ключа на Windows — DACL ОС, их подстановка не мерит
try:
    for label, (call, rel, want) in readers.items():
        calls.clear()
        with win32_open(calls):
            try:
                got = call()
            except Exception as e:
                got = type(e)
        shares = {share for name, _, share, _ in calls if name == os.path.realpath(ws / rel)}
        check(f"{label}: читает через дверь со всеми флагами совместного доступа и то же, что на диске",
              shares == {_READ | _WRITE | _DELETE} and got == want, (sorted(shares), got))
finally:
    owner_only.restrict = real_restrict
shutil.rmtree(scratch, ignore_errors=True)

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
