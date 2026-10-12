#!/usr/bin/env python3
"""tests/quick/test_environment.py — двери среды сервера: перечень `core/environment.py` совпадает с кодом, и справка
`server.py --help` печатается из него.

Истина о коде — обход исходника сервера (`core/`, `tools/`, `server.py`): каждая строка вида `MCP_*` — дверь перечня или
объявленное «не дверь»; у каждой двери есть место в коде, и ровно одно — имя объявлено один раз, читатели берут его оттуда.
Предел: читается строка-литерал; имя, собранное в рантайме (`"MCP_" + x`), обход не видит.
"""
import ast
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.environment import DOORS, NOT_DOORS

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
shown = subprocess.run([sys.executable, str(ROOT / "server.py"), "--help"], cwd=ROOT, capture_output=True, text=True)
lines = shown.stdout.splitlines()
missing = [n for n, what in DOORS.items() if not any(n in line and what in line for line in lines)]
check("server.py --help называет каждую дверь и что она меняет", shown.returncode == 0 and not missing,
      (shown.returncode, missing, shown.stderr[-300:]))

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
