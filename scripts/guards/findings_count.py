"""scripts/guards/findings_count.py — счёт реестра находок замером и проверка его читаемости.

## Назначение
Числа о реестре не пишутся прозой — они печатаются здесь. Судит же скрипт другое: строки реестра
обязаны РАЗБИРАТЬСЯ. Находки не удаляются, только закрываются, поэтому разобранных строк не может
стать меньше; уменьшение значит сломанный формат таблицы — а вместе с ним слепнет
`invariants.status_off_registry`, который читает те же строки и на пустом наборе молча зеленеет.

## Границы
Закрыта = идентификатор строки зачёркнут ИЛИ в колонке severity стоит знак закрытия (✅ или 🟢).
Проза ячейки не разбирается: «ЗАКРЫТ (остаток)» встречается и у тех находок, что остались
открытыми. Разбор здесь ОДИН на весь проект: `invariants.status_off_registry` зовёт `scan`, а не
повторяет своё выражение — второй читатель того же реестра даёт второй вердикт.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[2] / "docs" / "roadmap" / "02_findings.md"
FLOOR = Path(__file__).with_name("findings_count_baseline.txt")
ROW = re.compile(r"^\| (~~)?\*{0,2}(F\d+)\*{0,2}(?:~~)?\s*\|([^|]*)\|")
CLOSED = ("✅", "🟢")


def scan(text: str) -> dict[str, bool]:
    """{идентификатор: закрыта}. Первая строка каноническая: ниже лежат таблицы переформулировок."""
    rows: dict[str, bool] = {}
    for line in text.splitlines():
        if m := ROW.match(line):
            closed = bool(m.group(1)) or any(mark in m.group(3) for mark in CLOSED)
            rows.setdefault(m.group(2), closed)
    return rows


def _named_tree(argv: list[str]) -> Path | None:
    """`--root <путь>` — судить НАЗВАННОЕ дерево, а не то, в котором лежит сам сторож.

    Без флага вердикт достижим только из python-кода: объявление умеет запустить команду, но не
    умеет передать ей корень, и обе стороны сторожа остаются недостижимы из карты.
    """
    for i, arg in enumerate(argv):
        if arg == "--root" and i + 1 < len(argv):
            return Path(argv[i + 1]).resolve()
        if arg.startswith("--root="):
            return Path(arg.split("=", 1)[1]).resolve()
    return None


def main() -> int:
    global REGISTRY, FLOOR
    if (named := _named_tree(sys.argv)) is not None:
        REGISTRY = named / "docs" / "roadmap" / "02_findings.md"
        FLOOR = named / "scripts" / "guards" / "findings_count_baseline.txt"
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

    if "--bless" in sys.argv:
        FLOOR.write_text(f"{len(rows)}\n", encoding="utf-8")
        print(f"пол поднят до {len(rows)}")
        return 0
    if "--check" not in sys.argv:
        return 0
    # Пол, а не потолок: реестр только растёт, поэтому падение числа — это сломанный разбор,
    # а не закрытая находка. Поднимается вместе с новой находкой через `--bless`.
    # Пола нет или он нечитаем — это «улики нет», а не «чисто»: молча выключенный храповик
    # зеленеет на любом числе строк, включая ноль.
    if not FLOOR.exists():
        print(f"findings_count: пола нет — {FLOOR}. Храповик выключен, замер ни с чем не сверен: "
              "заведите пол через --bless", file=sys.stderr)
        return 2
    try:
        floor = int(FLOOR.read_text(encoding="utf-8").strip())
    except ValueError:
        print(f"findings_count: пол не читается числом — {FLOOR}", file=sys.stderr)
        return 2
    if len(rows) < floor:
        print(f"findings_count: разобрано {len(rows)} строк при поле {floor} — реестр ужался. "
              "Находки не удаляются, только закрываются: значит сломался разбор таблицы, и вместе "
              "с ним ослеп status_off_registry", file=sys.stderr)
        return 1
    print(f"реестр читается машиной: {len(rows)} строк при поле {floor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
