"""scripts/findings_count.py — счёт реестра находок по самим его строкам.

## Назначение
Шапка `docs/roadmap/02_findings.md` называет числа, а перечень ниже их опровергал: последние
находки попадали в таблицы и не попадали в шапку. Счёт здесь считается разбором строк, чтобы
расхождение падало командой, а не всплывало через сессию.

## Границы
Закрыта = идентификатор строки зачёркнут ИЛИ в колонке severity стоит галочка. Проза ячейки не
разбирается: «ЗАКРЫТ (остаток)» встречается и у тех находок, что остались открытыми.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REGISTRY = Path(__file__).resolve().parent.parent / "docs" / "roadmap" / "02_findings.md"
ROW = re.compile(r"^\| (~~)?\*{0,2}(F\d+)\*{0,2}(?:~~)?\s*\|([^|]*)\|")
HEADER = re.compile(r"(\d+) наход\w+, (\d+) закрыт\w+, (\d+) открыт\w+")  # окончания склоняются по числу


def scan(text: str) -> dict[str, bool]:
    """{идентификатор: закрыта}. Один идентификатор может встретиться в нескольких прогонах."""
    rows: dict[str, bool] = {}
    for line in text.splitlines():
        if m := ROW.match(line):
            closed = bool(m.group(1)) or "✅" in m.group(3)
            rows[m.group(2)] = rows.get(m.group(2), False) or closed
    return rows


def main() -> int:
    if not REGISTRY.exists():
        print(f"findings_count: реестра нет — {REGISTRY}", file=sys.stderr)
        return 2
    text = REGISTRY.read_text(encoding="utf-8")
    rows = scan(text)
    if not rows:
        print("findings_count: ни одной строки не разобрано — замер не состоялся", file=sys.stderr)
        return 2
    closed = sum(rows.values())
    openi = sorted((f for f, c in rows.items() if not c), key=lambda x: int(x[1:]))
    print(f"всего: {len(rows)} · закрыты: {closed} · открытых: {len(openi)}")
    print("открытые: " + " · ".join(openi))

    if "--check" not in sys.argv:
        return 0
    if not (m := HEADER.search(text)):
        print("findings_count: в шапке нет строки счёта — сверять не с чем", file=sys.stderr)
        return 1
    claimed = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    actual = (len(rows), closed, len(openi))
    if claimed != actual:
        print(f"findings_count: шапка говорит {claimed}, строки дают {actual}", file=sys.stderr)
        return 1
    print("шапка сходится со строками")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
