"""
tests/quick/test_tools_inventory.py — контракт инвентаря инструментов (сеть безопасности A2).

Standalone-прогон:  python tests/quick/test_tools_inventory.py
Обновить эталон:    python tests/quick/test_tools_inventory.py --bless

Распил монолита (A2) двигает МЕСТО определения хендлеров, но не контракт клиента:
набор имён, группы, title, annotations и input_schema обязаны остаться идентичными.
Эталон — tools_inventory.golden.json; --bless разрешён только при осознанном
изменении контракта (тогда diff эталона обязан быть виден в ревью коммита).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import server

GOLDEN = Path(__file__).parent / "tools_inventory.golden.json"

_checks = 0
_fails = []


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def snapshot() -> dict:
    """Инвентарь живого сервера: имя → контракт, видимый клиенту в tools/list."""
    engine, _transport, _firewall = server.create_server()
    return {
        name: {
            "group": tool.group,
            "title": tool.title,
            "description": tool.description,
            "annotations": tool.annotations,
            "input_schema": tool.input_schema,
        }
        for name, tool in sorted(engine.tools.items())
    }


current = snapshot()

if "--bless" in sys.argv:
    GOLDEN.write_text(json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Эталон обновлён: {GOLDEN.relative_to(ROOT)} ({len(current)} инструментов)")
    sys.exit(0)

golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

print("== Инвентарь: состав ==")
ok(len(current) == len(golden), f"инструментов {len(current)}, эталон {len(golden)}")

missing = sorted(set(golden) - set(current))
extra = sorted(set(current) - set(golden))
ok(not missing, f"ни один инструмент не пропал (пропали: {missing or '—'})")
ok(not extra, f"нет незаявленных инструментов (лишние: {extra or '—'})")

print("== Инвентарь: группы (клиент видит их в tools/list) ==")
for name in sorted(set(current) & set(golden)):
    ok(current[name]["group"] == golden[name]["group"],
       f"{name}: группа {current[name]['group']} == {golden[name]['group']}")

print("== Инвентарь: контракт (title/description/annotations/schema) ==")
for field in ("title", "description", "annotations", "input_schema"):
    diverged = [n for n in sorted(set(current) & set(golden)) if current[n][field] != golden[n][field]]
    ok(not diverged, f"{field} идентичен эталону (разошлись: {diverged or '—'})")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    print("\nЕсли контракт менялся ОСОЗНАННО — обнови эталон: --bless (diff обязан быть в ревью).")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
