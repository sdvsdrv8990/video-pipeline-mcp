#!/usr/bin/env python3
"""tests/quick/test_environment.py — двери среды сервера: перечень `core/environment.py` совпадает с кодом, и справка
`server.py --help` печатается из него.

Истина о коде — обход исходника сервера (`core/`, `tools/`, `server.py`): каждая строка вида `MCP_*` — дверь перечня или
объявленное «не дверь»; у каждой двери есть место в коде, и ровно одно — имя объявлено один раз, читатели берут его оттуда.
Предел: читается строка-литерал; имя, собранное в рантайме (`"MCP_" + x`), обход не видит.
"""
import ast
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.environment import DOORS, NOT_DOORS, SECRETS, child_environ

_checks = 0
_fails: list[str] = []
NAME = re.compile(r"MCP_[A-Z0-9_]+")


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def names_in_code() -> dict[str, list[str]]:
    """Строки вида MCP_* в коде сервера вне перечня — имя → места."""
    places = defaultdict(list)
    sources = [ROOT / "server.py", *sorted((ROOT / "core").rglob("*.py")), *sorted((ROOT / "tools").rglob("*.py"))]
    for path in sources:
        if path == ROOT / "core" / "environment.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and NAME.fullmatch(node.value):
                places[node.value].append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return places


print("== 1. Перечень дверей совпадает с кодом сервера ==")
code = names_in_code()
check("каждое имя MCP_* в коде — дверь перечня или объявленное «не дверь»",
      code and not set(code) - set(DOORS) - set(NOT_DOORS), {n: code[n] for n in set(code) - set(DOORS) - set(NOT_DOORS)})
check("у каждой двери перечня есть место в коде — мёртвой двери в справке нет",
      not set(DOORS) - set(code), sorted(set(DOORS) - set(code)))
check("объявленное «не дверь» в коде есть", not set(NOT_DOORS) - set(code), sorted(set(NOT_DOORS) - set(code)))
twice = {n: p for n, p in code.items() if len(p) > 1}
check("имя каждой двери объявлено в коде ровно одним местом", not twice, twice)

print("== 2. Справка сервера печатается из перечня ==")
shown = subprocess.run([sys.executable, str(ROOT / "server.py"), "--help"], cwd=ROOT, capture_output=True,
                       encoding="utf-8")
lines = shown.stdout.splitlines()
missing = [n for n, what in DOORS.items() if not any(n in line and what in line for line in lines)]
check("server.py --help называет каждую дверь и что она меняет", shown.returncode == 0 and not missing,
      (shown.returncode, missing, shown.stderr[-300:]))

print("== 3. Вывод сервера в канал — utf-8 при любой кодировке локали ==")
# PYTHONIOENCODING=cp1252 воспроизводит канал Windows; UTF-8 Mode гейта снят — кодировку решает сам сервер.
channel = {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"} | {"PYTHONIOENCODING": "cp1252"}
raw = subprocess.run([sys.executable, str(ROOT / "server.py"), "--help"], cwd=ROOT, capture_output=True, env=channel)
try:
    said = raw.stdout.decode("utf-8")
except UnicodeDecodeError:
    said = ""
check("сервер в канале cp1252 печатает справку в utf-8, а не падает на первом русском символе",
      raw.returncode == 0 and "двери среды" in said, (raw.returncode, raw.stderr[-200:]))

print("== 4. Среда ребёнка сервера — без дверей-секретов, в UTF-8 Mode ==")
check("двери-секреты — двери перечня", SECRETS and SECRETS <= set(DOORS), sorted(SECRETS - set(DOORS)))
server_env = {name: f"секрет-{name}" for name in SECRETS} | {"PATH": "/bin", "MCP_HOME": "/дом"}
child = child_environ(environ=server_env)
check("ни одна дверь-секрет не уходит ребёнку, прочая среда сервера — уходит",
      not set(child) & SECRETS and child.get("PATH") == "/bin" and child.get("MCP_HOME") == "/дом", sorted(child))
check("ребёнок — в UTF-8 Mode; явный выбор владельца не перекрывается",
      child.get("PYTHONUTF8") == "1" and child_environ(environ={"PYTHONUTF8": "0"})["PYTHONUTF8"] == "0")
check("свой секрет ребёнку кладёт тот, кто его выпускает (extra главнее)",
      child_environ({"MCP_RUNNER_TOKEN": "выпущен"}, environ=server_env).get("MCP_RUNNER_TOKEN") == "выпущен")

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
