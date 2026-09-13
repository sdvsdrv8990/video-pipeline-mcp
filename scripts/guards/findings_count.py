"""scripts/guards/findings_count.py — счёт реестра находок замером и проверка его читаемости.

Числа о реестре не пишутся прозой — они печатаются здесь. Судит же скрипт другое: строки реестра
обязаны РАЗБИРАТЬСЯ. Находки не удаляются, только закрываются, поэтому разобранных строк не может
стать меньше; уменьшение значит сломанный формат таблицы — а вместе с ним слепнет ось «статус мимо
реестра», которая читает те же строки и на пустом наборе молча зеленеет.

Закрыта = идентификатор строки зачёркнут ИЛИ в колонке важности стоит знак закрытия. Проза ячейки
не разбирается: «ЗАКРЫТ (остаток)» встречается и у тех находок, что остались открытыми. Разбор
здесь ОДИН на весь проект — второй читатель того же реестра дал бы второй вердикт.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _stamp  # noqa: E402 — там же: роспись шагов лежит рядом со сторожами
import _verdict                                                            # noqa: E402

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


ПОВТОР = ".venv/bin/python scripts/guards/findings_count.py --check"


def _итог(ряды: list[dict], код: int, чужое: bool) -> int:
    """Записать роспись и вернуть код. Чужое дерево не расписываем — судим его не нашей меркой.

    Роспись живёт в НАШЕМ журнале, и ряд о чужом дереве читался бы как шаг по своему: та же
    причина, по которой туда не уходит подпись вердикта.
    """
    if not чужое:
        _stamp.роспись(ряды, механизм="findings_count.py", cmd=ПОВТОР, зона="сторожа")
    return код


def main() -> int:
    try:                                       # след: без отметки механизм выглядит пылящимся
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".claude" / "hooks"))
        import _trace
        _trace.mark("findings_count.py")
    except Exception:                          # noqa: BLE001 — след не важнее самой работы
        pass
    global REGISTRY, FLOOR
    if (named := _named_tree(sys.argv)) is not None:
        REGISTRY = named / "docs" / "roadmap" / "02_findings.md"
        FLOOR = named / "scripts" / "guards" / "findings_count_baseline.txt"
    чужое = named is not None
    ряды = [_stamp.строка_шага(
        "поднять реестр", "REGISTRY.exists",
        [_stamp.условие("реестр находок лежит на месте", True, REGISTRY.exists())],
        исход="сработал" if REGISTRY.exists() else "молчал",
        следствие=f"источник читается: {REGISTRY.name}" if REGISTRY.exists()
        else "реестра нет — судить нечем, и ноль строк тут не чистота")]
    if not REGISTRY.exists():
        print(f"findings_count: реестра нет — {REGISTRY}", file=sys.stderr)
        return _итог(ряды, 2, чужое)
    text = REGISTRY.read_text(encoding="utf-8")
    rows = scan(text)
    ряды.append(_stamp.строка_шага(
        "разобрать строки", "scan",
        [_stamp.условие("разобрана хоть одна строка", True, bool(rows))],
        исход="сработал" if rows else "молчал",
        следствие=f"разобрано строк: {len(rows)}" if rows
        else "ни одной строки — сломан формат таблицы, и вместе с ним слепнет ось статусов"))
    if not rows:
        print("findings_count: ни одной строки не разобрано — замер не состоялся", file=sys.stderr)
        return _итог(ряды, 2, чужое)
    closed = sum(rows.values())
    openi = sorted((f for f, c in rows.items() if not c), key=lambda x: int(x[1:]))
    print(f"всего: {len(rows)} · закрыты: {closed} · открытых: {len(openi)}")
    print("открытые: " + " · ".join(openi))

    if "--bless" in sys.argv:
        FLOOR.write_text(f"{len(rows)}\n", encoding="utf-8")
        print(f"пол поднят до {len(rows)}")
        return _итог(ряды, 0, чужое)
    if "--check" not in sys.argv:
        return _итог(ряды, 0, чужое)
    # Пол, а не потолок: реестр только растёт, поэтому падение числа — это сломанный разбор,
    # а не закрытая находка. Поднимается вместе с новой находкой через `--bless`.
    # Пола нет или он нечитаем — это «улики нет», а не «чисто»: молча выключенный храповик
    # зеленеет на любом числе строк, включая ноль.
    if not FLOOR.exists():
        print(f"findings_count: пола нет — {FLOOR}. Храповик выключен, замер ни с чем не сверен: "
              "заведите пол через --bless", file=sys.stderr)
        ряды.append(_stamp.строка_шага(
            "сверить с полом", "храповик пола",
            [_stamp.условие("пол объявлен и читается числом", True, False)],
            исход="молчал",
            следствие="пола нет — храповик выключен, и любое число строк выглядело бы зелёным"))
        return _итог(ряды, 2, чужое)
    try:
        floor = int(FLOOR.read_text(encoding="utf-8").strip())
    except ValueError:
        print(f"findings_count: пол не читается числом — {FLOOR}", file=sys.stderr)
        ряды.append(_stamp.строка_шага(
            "сверить с полом", "храповик пола",
            [_stamp.условие("пол объявлен и читается числом", True, False)],
            исход="молчал", следствие="пол есть, но не число — сверять не с чем"))
        return _итог(ряды, 2, чужое)
    ряды.append(_stamp.строка_шага(
        "сверить с полом", "храповик пола",
        [_stamp.условие("пол объявлен и читается числом", True, True),
         _stamp.условие("разобранных строк стало МЕНЬШЕ пола", True, len(rows) < floor)],
        исход="сработал" if len(rows) < floor else "молчал",
        следствие=f"реестр ужался: {len(rows)} при поле {floor}" if len(rows) < floor
        else f"разобрано не ниже пола: {len(rows)} при поле {floor}"))
    if len(rows) < floor:
        print(f"findings_count: разобрано {len(rows)} строк при поле {floor} — реестр ужался. "
              "Находки не удаляются, только закрываются: значит сломался разбор таблицы, и вместе "
              "с ним ослеп status_off_registry", file=sys.stderr)
        return _итог(ряды, 1, чужое)
    print(f"реестр читается машиной: {len(rows)} строк при поле {floor}")
    # Чужое дерево судится не нашим полом, и подпись туда была бы вердиктом о чужой мерке.
    if чужое:
        return _итог(ряды, 0, чужое)
    _итог(ряды, 0, чужое)
    return _verdict.close("реестр находок", 0,
                          ".venv/bin/python scripts/guards/findings_count.py --check",
                          улика="есть")


if __name__ == "__main__":
    raise SystemExit(main())
