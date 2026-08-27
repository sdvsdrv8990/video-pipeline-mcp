"""scripts/guards/findings_count.py — счёт реестра находок замером и проверка его читаемости.

## Назначение
Числа о реестре не пишутся прозой — они печатаются здесь. Судит же скрипт другое: строки реестра
обязаны РАЗБИРАТЬСЯ. Находки не удаляются, только закрываются, поэтому разобранных строк не может
стать меньше; уменьшение значит сломанный формат таблицы — а вместе с ним слепнет
`invariants.status_off_registry`, который читает те же строки и на пустом наборе молча зеленеет.

## Границы
Закрыта = идентификатор строки зачёркнут ИЛИ в колонке severity стоит галочка. Проза ячейки не
разбирается: «ЗАКРЫТ (остаток)» встречается и у тех находок, что остались открытыми.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[2] / "docs" / "roadmap" / "02_findings.md"
FLOOR = Path(__file__).with_name("findings_count_baseline.txt")
ROW = re.compile(r"^\| (~~)?\*{0,2}(F\d+)\*{0,2}(?:~~)?\s*\|([^|]*)\|")


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
    # Пол, а не потолок: реестр только растёт, поэтому падение числа — это сломанный разбор,
    # а не закрытая находка. Поднимается вместе с новой находкой через `--bless`.
    floor = int(FLOOR.read_text(encoding="utf-8").strip()) if FLOOR.exists() else 0
    if "--bless" in sys.argv:
        FLOOR.write_text(f"{len(rows)}\n", encoding="utf-8")
        print(f"пол поднят до {len(rows)}")
        return 0
    if len(rows) < floor:
        print(f"findings_count: разобрано {len(rows)} строк при поле {floor} — реестр ужался. "
              "Находки не удаляются, только закрываются: значит сломался разбор таблицы, и вместе "
              "с ним ослеп status_off_registry", file=sys.stderr)
        return 1
    print(f"реестр читается машиной: {len(rows)} строк при поле {floor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
