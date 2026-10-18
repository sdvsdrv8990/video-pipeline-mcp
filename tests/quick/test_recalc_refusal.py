#!/usr/bin/env python3
"""tests/quick/test_recalc_refusal.py — отказ пересчёта формул на двери: код на причину, а не падение обработчика.

Причина рождается в дочернем процессе (поддельный `soffice` в PATH): упал с кодом 1 и путём хоста в stderr, вышел с 0
без книги, завис дольше срока. Следствие меряется на двери — отказ, который видит клиент (текст провода), и причина для
журнала сервера. Законная пара — движка нет ни в PATH, ни в объявленных местах этой ОС (копия деклараций с пустыми
местами): «не найден» с рецептом для владельца. Один код — одна причина (EB-11): у каждой свой рецепт.
"""
import logging
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import openpyxl
import yaml

from core.engine import Engine
from core.environment import ChildSaid
from core.excel.excel_core import ExcelEngine
from core.reactions import Reactions
from core.transport.transport import answer
from tests.harness.live_server import declarations_copy

_checks = 0
_fails: list[str] = []
HOST = "/home/host/secret/книга.xlsx"
SAID = "Error: source file could not be loaded"
SHORT = 1          # срок пересчёта на время события: факт отказа обязан назвать его, а не число из кода


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def fake_soffice(body: str) -> Path:
    """Каталог с `soffice`, который исполняет `body` (Python); каталог идёт в PATH вместо настоящего."""
    folder = Path(tempfile.mkdtemp(prefix="vpm-soffice-"))
    script = folder / "fake_soffice.py"
    script.write_text(body, encoding="utf-8")
    if os.name == "nt":
        (folder / "soffice.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = folder / "soffice"
        launcher.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    return folder


def refused(path_dir: Path, config: Path | None = None):
    """Отказ, как его видит клиент: исключение переводит дверь движка; PATH — только `path_dir`, декларации — `config`."""
    held = {k: os.environ.get(k) for k in ("PATH", "MCP_CONFIG")}
    os.environ["PATH"] = str(path_dir)
    if config is not None:
        os.environ["MCP_CONFIG"] = str(config)
    try:
        ExcelEngine(ws).validate_formulas("книга.xlsx")
        return None, None, ""
    except Exception as e:
        result = engine.failure("excel_validate_formulas", e)
        return result, e, "".join(c.get("text", "") for c in answer(result).get("content", []))
    finally:
        for k, v in held.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def journaled(result, words: str) -> bool:
    """Журнал сервера: запись с номером события из фактов ответа несёт слова причины."""
    event = str(result.error.facts.get("event", "")) if result is not None else ""
    return bool(event) and any(event in r.getMessage() and words in r.getMessage() for r in heard)


ws = Path(tempfile.mkdtemp(prefix="vpm-recalc-"))
book = openpyxl.Workbook()
book.active["A1"] = "=1+1"
book.save(ws / "книга.xlsx")
engine = Engine(reactions=Reactions(ROOT / "config" / "server_reactions.yaml"))
heard: list[logging.LogRecord] = []            # записи журнала сервера, которые пишет дверь движка
ear = logging.Handler()
ear.emit = heard.append  # type: ignore[method-assign]
logging.getLogger("core.engine.engine").addHandler(ear)
# Уровень — как у журнала сервера (config/observability.yaml → logs.heard): кодовый отказ пишется строкой INFO.
logging.getLogger("core.engine.engine").setLevel(
    yaml.safe_load((ROOT / "config" / "observability.yaml").read_text(encoding="utf-8"))["logs"]["heard"]["level"])
recipes: dict[str, str] = {}

print("== 1. LibreOffice упал: RECALC_FAILED с кодом выхода, слова и путь хоста — причиной для журнала ==")
crashed = fake_soffice(f"import sys\nsys.stderr.write({f'convert {HOST}' + chr(10) + SAID + chr(10)!r})\nsys.exit(1)\n")
result, exc, wire = refused(crashed)
check("RECALC_FAILED с кодом выхода 1, а не падение обработчика (INTERNAL_ERROR)",
      result is not None and result.error.code == "RECALC_FAILED" and result.error.facts.get("exit_code") == 1
      and "код выхода 1" in result.error.message,
      (result and result.error.code, result and result.error.facts, type(exc).__name__))
check("путь хоста и слова LibreOffice в ответ не ушли", bool(wire) and HOST not in wire and SAID not in wire, wire[:200])
check("причина для журнала — последняя строка stderr ребёнка (ChildSaid)",
      exc is not None and isinstance(exc.__cause__, ChildSaid) and str(exc.__cause__) == SAID,
      exc and (type(exc.__cause__).__name__, str(exc.__cause__)))
check("журнал сервера: запись с номером события из фактов ответа несёт слова ребёнка", journaled(result, SAID),
      [r.getMessage()[:120] for r in heard])
if result is not None and result.error.recovery:
    recipes["упал"] = result.error.recovery.reason

print("== 2. LibreOffice вышел с 0 без книги: тот же RECALC_FAILED, код выхода 0 и слова ребёнка в журнал ==")
NOFILE = "Error: no export filter"
silent = fake_soffice(f"import sys\nsys.stderr.write({NOFILE + chr(10)!r})\nsys.exit(0)\n")
result, exc, wire = refused(silent)
check("RECALC_FAILED с кодом выхода 0: движок отработал, вычисленной книги нет",
      result is not None and result.error.code == "RECALC_FAILED" and result.error.facts.get("exit_code") == 0,
      (result and result.error.code, result and result.error.facts, type(exc).__name__))
check("журнал сервера: слова ребёнка под номером события, в ответе их нет",
      journaled(result, NOFILE) and NOFILE not in wire, (wire[:200], [r.getMessage()[:120] for r in heard[-3:]]))

print("== 3. LibreOffice завис дольше срока: RECALC_TIMEOUT называет срок фактом, а не числом из кода ==")
real_run = subprocess.run
subprocess.run = lambda *a, **k: real_run(*a, **{**k, "timeout": SHORT})  # type: ignore[assignment]
try:
    result, exc, wire = refused(fake_soffice("import time\ntime.sleep(5)\n"))
finally:
    subprocess.run = real_run  # type: ignore[assignment]
check(f"RECALC_TIMEOUT: facts.timeout_sec — срок вызова ({SHORT}), текст называет его",
      result is not None and result.error.code == "RECALC_TIMEOUT" and result.error.facts.get("timeout_sec") == SHORT
      and f"{SHORT} с" in result.error.message,
      (result and result.error.code, result and result.error.facts, result and result.error.message))
check("команда с путями хоста — в журнал под номером события, не в ответ",
      bool(wire) and str(ws) not in wire and journaled(result, "TimeoutExpired"), wire[:200])
if result is not None and result.error.recovery:
    recipes["срок"] = result.error.recovery.reason

print("== 4. Законная пара: движка нет ни в PATH, ни в объявленных местах — «не найден» и рецепт владельцу ==")
config = declarations_copy()
engines = yaml.safe_load((config / "engines.yaml").read_text(encoding="utf-8"))
engines["recalc"][sys.platform] = []
(config / "engines.yaml").write_text(yaml.safe_dump(engines, allow_unicode=True, sort_keys=False), encoding="utf-8")
result, exc, wire = refused(Path(tempfile.mkdtemp(prefix="vpm-nopath-")), config)
reason = result.error.recovery.reason if result is not None and result.error.recovery else ""
check("RECALC_UNAVAILABLE «не найден», рецепт — владельцу, без бинаря, пакета и файла конфигурации",
      result is not None and result.error.code == "RECALC_UNAVAILABLE" and "не найден" in result.error.message
      and "владельцу" in reason and not any(w in reason for w in ("soffice", "libreoffice-calc", "config/", ".yaml")),
      result and (result.error.code, result.error.message, reason))
recipes["нет"] = reason

print("== 5. Один код — одна причина: у каждой причины свой рецепт, и «поставь движок» только там, где его нет ==")
check("три причины — три рецепта", len(set(recipes.values())) == 3 and len(recipes) == 3, recipes)
check("упавшему и зависшему пересчёту рецепт не велит ставить движок",
      all("LibreOffice" not in recipes.get(k, "LibreOffice") for k in ("упал", "срок")), recipes)

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
