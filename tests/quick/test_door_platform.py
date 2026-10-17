#!/usr/bin/env python3
"""tests/quick/test_door_platform.py — перенос через дверь слоя на семантике Windows, мерится на Linux.

На Windows `os.rename` при занятом имени всегда бросает FileExistsError (CPython `Doc/library/os.rst`, `rename`), на Unix
пустой каталог назначения заменяется молча. Здесь семантика Windows подставлена на время вызова: `sys.platform` —
"win32", `os.rename` — тот же перенос, но занятое имя — FileExistsError. Что подстановка не меряет (NTFS, жёсткие
ссылки, права) — мерит CI на Windows.
"""
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.contracts import ContractError
from core.paths import Files

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
_safe = shutil.rmtree.avoids_symlink_attacks
shutil.rmtree.avoids_symlink_attacks = False
try:
    got = removed("videos/kept")
finally:
    shutil.rmtree.avoids_symlink_attacks = _safe
check("не Windows и rmtree не устойчив — отказ, как был: исключение Windows не открывает другие платформы",
      got == "OSError" and (ws / "videos/kept/sub").is_dir(), got)
check("Unix: устойчивый rmtree снимает каталог", removed("videos/kept") == "dir" and not (ws / "videos/kept").exists())

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
