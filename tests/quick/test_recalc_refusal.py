#!/usr/bin/env python3
"""tests/quick/test_recalc_refusal.py — отказ пересчёта формул на двери: LibreOffice упал — код, а не падение обработчика.

Причина рождается в дочернем процессе (поддельный `soffice` в PATH: путь хоста в stderr, код выхода 1), следствие
меряется на двери — отказ, который видит клиент (текст провода), и причина для журнала сервера. Законная пара —
`soffice` нет в PATH: отказ «недоступен» с рецептом для владельца.
"""
import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import openpyxl

from core.engine import Engine
from core.environment import ChildSaid
from core.excel.excel_core import ExcelEngine
from core.reactions import Reactions
from core.transport.transport import answer

_checks = 0
_fails: list[str] = []
HOST = "/home/host/secret/книга.xlsx"
SAID = "Error: source file could not be loaded"


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def fake_soffice(folder: Path) -> None:
    """`soffice`, который пишет в stderr путь хоста и свою причину и выходит с кодом 1."""
    script = folder / "fake_soffice.py"
    script.write_text(f"import sys\nsys.stderr.write({f'convert {HOST}' + chr(10) + SAID + chr(10)!r})\nsys.exit(1)\n",
                      encoding="utf-8")
    if os.name == "nt":
        (folder / "soffice.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = folder / "soffice"
        launcher.write_text(f"#!{sys.executable}\n" + script.read_text(encoding="utf-8"), encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)


def refused(engine: Engine, ws: Path, path_dir: Path):
    """Отказ, как его видит клиент: исключение переводит дверь движка; PATH — только каталог `path_dir`."""
    held = os.environ["PATH"]
    os.environ["PATH"] = str(path_dir)
    try:
        ExcelEngine(ws).validate_formulas("книга.xlsx")
        return None, None
    except Exception as e:
        return engine.failure("excel_validate_formulas", e), e
    finally:
        os.environ["PATH"] = held


ws = Path(tempfile.mkdtemp(prefix="vpm-recalc-"))
book = openpyxl.Workbook()
book.active["A1"] = "=1+1"
book.save(ws / "книга.xlsx")
engine = Engine(reactions=Reactions(ROOT / "config" / "server_reactions.yaml"))
heard: list[logging.LogRecord] = []            # записи журнала сервера, которые пишет дверь движка
ear = logging.Handler(logging.INFO)
ear.emit = heard.append  # type: ignore[method-assign]
logging.getLogger("core.engine.engine").addHandler(ear)
logging.getLogger("core.engine.engine").setLevel(logging.INFO)

print("== 1. LibreOffice упал: отказ кодом, слова и путь хоста — причиной для журнала, не в ответе ==")
broken = Path(tempfile.mkdtemp(prefix="vpm-soffice-"))
fake_soffice(broken)
result, exc = refused(engine, ws, broken)
wire = "".join(c.get("text", "") for c in answer(result).get("content", [])) if result else ""
check("RECALC_UNAVAILABLE с кодом выхода, а не падение обработчика (INTERNAL_ERROR)",
      result is not None and result.error.code == "RECALC_UNAVAILABLE" and result.error.facts.get("exit_code") == 1,
      (result and result.error.code, result and result.error.facts, type(exc).__name__))
check("путь хоста и слова LibreOffice в ответ не ушли", bool(wire) and HOST not in wire and SAID not in wire, wire[:200])
check("причина для журнала — последняя строка stderr ребёнка (ChildSaid)",
      exc is not None and isinstance(exc.__cause__, ChildSaid) and str(exc.__cause__) == SAID,
      exc and (type(exc.__cause__).__name__, str(exc.__cause__)))
event = str(result.error.facts.get("event", "")) if result is not None else ""
check("журнал сервера: запись с номером события из фактов ответа несёт слова ребёнка",
      bool(event) and any(event in r.getMessage() and SAID in r.getMessage() for r in heard),
      (event, [r.getMessage()[:120] for r in heard]))

print("== 2. Законная пара: soffice нет в PATH — «недоступен» и рецепт владельцу ==")
result, exc = refused(engine, ws, Path(tempfile.mkdtemp(prefix="vpm-nopath-")))
check("RECALC_UNAVAILABLE «недоступен», рецепт называет soffice в PATH, а не пакет одного дистрибутива",
      result is not None and result.error.code == "RECALC_UNAVAILABLE" and "недоступен" in result.error.message
      and "soffice" in (result.error.recovery.reason if result.error.recovery else "")
      and "libreoffice-calc" not in (result.error.recovery.reason if result.error.recovery else ""),
      result and (result.error.message, result.error.recovery))

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
