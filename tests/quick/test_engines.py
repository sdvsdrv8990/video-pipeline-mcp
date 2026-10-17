#!/usr/bin/env python3
"""tests/quick/test_engines.py — сервер находит внешний движок по объявленным местам установки, а не только в PATH.

На Windows установщик LibreOffice кладёт soffice в Program Files без PATH: сервер, ищущий только в PATH, отвечал «движка
нет», хотя он стоит. Истина — поддельный движок, положенный тестом в каталог, которого в PATH нет; место объявлено
переменной среды, как `%ProgramFiles%` в `config/engines.yaml`.
"""
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.contracts import ContractError
from core.declaration import Declaration, declarations_root
from core.declaration_schemas import RECALC_ENGINE, EnginePlaces
from core.engines import locate

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


def spec(places: list[str]) -> EnginePlaces:
    """Объявление движка с местами только для этой ОС — как раздел `config/engines.yaml`."""
    own = sys.platform if sys.platform in ("linux", "darwin", "win32") else "linux"
    return EnginePlaces.model_validate({"names": ["soffice"], **{k: places if k == own else []
                                                                  for k in ("linux", "darwin", "win32")}})


class Heard(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


installed = Path(tempfile.mkdtemp(prefix="engine_place_"))
fake = installed / ("soffice.cmd" if os.name == "nt" else "soffice")
fake.write_text("@echo off\r\n" if os.name == "nt" else "#!/bin/sh\n", encoding="utf-8")
fake.chmod(0o755)
empty = Path(tempfile.mkdtemp(prefix="empty_path_"))
held_path = os.environ["PATH"]
os.environ["PATH"] = str(empty)                 # движка в PATH нет ни под каким именем
os.environ["ENGINES_PROBE_DIR"] = str(installed)
heard = Heard()
logging.getLogger("core.engines").addHandler(heard)
logging.getLogger("core.engines").setLevel(logging.INFO)
try:
    print("== больное: движка нет в PATH, место объявлено переменной среды — найден на месте ==")
    place = "%ENGINES_PROBE_DIR%" if os.name == "nt" else "${ENGINES_PROBE_DIR}"
    found = locate(spec([str(empty / "нет-такого"), place]))
    check("найден в объявленном месте по порядку, переменная раскрыта", found is not None
          and Path(found).resolve().parent == installed.resolve(), found)

    print("== законное: мест для этой ОС нет — None и запись журнала, где искали ==")
    heard.lines.clear()
    check("не найден — None, а не чужой путь", locate(spec([])) is None)
    check("где искали — в журнале сервера (PATH и «не объявлены»)",
          any("soffice" in line and "не объявлены" in line for line in heard.lines), heard.lines)
finally:
    os.environ["PATH"] = held_path

print("== декларация движка читается дверью: имена и места для каждой ОС ==")
declared = Declaration(declarations_root() / RECALC_ENGINE.file, ContractError, "движков сервера", "") \
    .section(*RECALC_ENGINE.section, schema=RECALC_ENGINE.schema)
check("recalc: soffice среди имён, у Windows и macOS места объявлены",
      "soffice" in declared.names and declared.win32 and declared.darwin, declared)

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
