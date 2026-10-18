#!/usr/bin/env python3
"""tests/quick/test_door_platform.py — перенос через дверь слоя на семантике Windows, мерится на Linux.

На Windows `os.rename` при занятом имени всегда бросает FileExistsError (CPython `Doc/library/os.rst`, `rename`), на Unix
пустой каталог назначения заменяется молча. Здесь семантика Windows подставлена на время вызова: `sys.platform` —
"win32", `os.rename` — тот же перенос, но занятое имя — FileExistsError. Законная пара — платформа не Windows, тоже
подставленная: на раннере Windows настоящая платформа её не даёт. Отказ ОС на двери (FS-13) — подставленный вызов ОС
бросает то, что бросает Windows, когда файл держат открытым. Что подстановка не меряет (NTFS, жёсткие ссылки, права) —
мерит CI на Windows.
"""
import errno
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.contracts import ContractError
from core.engine import Engine
from core.paths import Files
from core.reactions import Reactions

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
    подмене ссылкой (`shutil.rst`: avoids_symlink_attacks истинно только при функциях по дескрипторам каталогов)."""
    real_rename, real_platform, real_safe = os.rename, sys.platform, shutil.rmtree.avoids_symlink_attacks

    def rename(src, dst, **kw):
        if os.path.lexists(dst):
            raise FileExistsError(183, "Cannot create a file when that file already exists", str(src), None, str(dst))
        real_rename(src, dst, **kw)

    os.rename, sys.platform, shutil.rmtree.avoids_symlink_attacks = rename, "win32", False
    try:
        yield
    finally:
        os.rename, sys.platform, shutil.rmtree.avoids_symlink_attacks = real_rename, real_platform, real_safe


@contextmanager
def unix(safe: bool):
    """Платформа не Windows; `safe` — устойчив ли rmtree к подмене ссылкой (функции по дескрипторам каталогов)."""
    real_platform, real_safe = sys.platform, shutil.rmtree.avoids_symlink_attacks
    sys.platform, shutil.rmtree.avoids_symlink_attacks = "linux", safe
    try:
        yield
    finally:
        sys.platform, shutil.rmtree.avoids_symlink_attacks = real_platform, real_safe


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

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
