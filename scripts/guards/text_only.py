#!/usr/bin/env python3
"""scripts/guards/text_only.py — судит УТВЕРЖДЕНИЕ «правка только текстовая».

Паспорт: будит редактирование, рука, наряд · читает git · расписывает «поднять тронутые → сверить
  поток токенов → отделить новые файлы» · подписывает через _verdict.
Зовёт: _stamp, _tokens, _verdict.
Зовут: рука, наряд и test_text_only.

Сравнивает поток токенов КОДА в дереве и в названной ревизии по каждому тронутому файлу. Краснеет
под `--только-текст`, если поток сдвинулся хоть одним токеном: значит правка унесла код, а автор
считал её текстовой. Храповика нет — утверждение либо верно, либо нет. Сжатие докстринга способно
захватить соседние операторы, и ни линтер, ни набор этого не видят.

    text_only.py                  # улика: где правка текстовая, где задет код
    text_only.py --только-текст   # суд: exit 1, если задет код
    text_only.py --ревизия HEAD~1
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _stamp
import _tokens  # импорт после правки sys.path: источник улики лежит рядом
import _verdict

ROOT = Path(__file__).resolve().parents[2]


def тронутые(ревизия: str) -> list[str]:
    """Файлы `.py`, различающиеся между деревом и ревизией, включая неотслеживаемые."""
    готово = subprocess.run(["git", "diff", "--name-only", ревизия, "--", "*.py"],
                            cwd=ROOT, capture_output=True, text=True)
    новые = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "*.py"],
                           cwd=ROOT, capture_output=True, text=True)
    return sorted(set(готово.stdout.split()) | set(новые.stdout.split()))


def было(rel: str, ревизия: str) -> str | None:
    """Содержимое файла в ревизии; `None` — файла там не было (новый), сверять не с чем."""
    готово = subprocess.run(["git", "show", f"{ревизия}:{rel}"], cwd=ROOT,
                            capture_output=True, text=True)
    return готово.stdout if готово.returncode == 0 else None


def разбор(ревизия: str) -> tuple[list[str], list[str], list[str]]:
    """Три списка: правка текстовая · задет КОД · сверять не с чем (новый файл)."""
    текстовые: list[str] = []
    кодовые: list[str] = []
    новые: list[str] = []
    for rel in тронутые(ревизия):
        старое = было(rel, ревизия)
        путь = ROOT / rel
        if старое is None or not путь.is_file():
            новые.append(rel)
            continue
        сдвиг, чем = _tokens.разошлось(старое, путь.read_text(encoding="utf-8", errors="replace"))
        (кодовые if сдвиг else текстовые).append(f"{rel} — {чем}" if сдвиг else rel)
    return текстовые, кодовые, новые


ПОВТОР = ".venv/bin/python scripts/guards/text_only.py --только-текст"


def роспись(ревизия: str, текстовые: list[str], кодовые: list[str],
            новые: list[str]) -> list[dict]:
    """Три шага сверки: что подняли, что сдвинулось, с чем сверять было НЕ с чем.

    Вынесено из `main` отдельной функцией, потому что судить надо ряды, а не печать: при разборе
    спрашивают «на каком шаге он замолчал», и ответ обязан быть один и тот же у прогона и у пробы.
    """
    всего = len(текстовые) + len(кодовые) + len(новые)
    return [
        _stamp.строка_шага(
            "поднять тронутые", "git diff --name-only",
            [_stamp.условие("против ревизии тронут хоть один .py", True, bool(всего))],
            исход="сработал" if всего else "молчал",
            следствие=f"тронутых файлов: {всего}" if всего
            else f"против {ревизия} не тронут ни один .py — сверять нечего, и это отказ судить"),
        _stamp.строка_шага(
            "сверить поток токенов", "_tokens.разошлось",
            [_stamp.условие("поток КОДА сдвинулся хоть у одного файла", True, bool(кодовые))],
            исход="сработал" if кодовые else "молчал",
            следствие=f"файлов с задетым кодом: {len(кодовые)}" if кодовые
            else f"поток кода не сдвинулся ни у одного из {len(текстовые)} файлов"),
        _stamp.строка_шага(
            "отделить новые файлы", "git show ревизия:файл",
            [_stamp.условие("есть файл, которого в ревизии не было", True, bool(новые))],
            исход="сработал" if новые else "молчал",
            следствие=f"сверять не с чем у файлов: {len(новые)}" if новые
            else "все тронутые файлы есть в ревизии — сверка полная"),
    ]


def main() -> int:
    try:                                       # след: без отметки механизм выглядит пылящимся
        sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
        import _trace
        _trace.mark("text_only.py")
    except Exception:  # след не важнее самой работы
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--только-текст", action="store_true",
                    help="судить утверждение: задет код — exit 1")
    ap.add_argument("--ревизия", default="HEAD", help="с чем сверять (по умолчанию HEAD)")
    a = ap.parse_args()
    текстовые, кодовые, новые = разбор(a.ревизия)
    _stamp.роспись(роспись(a.ревизия, текстовые, кодовые, новые),
                   механизм="text_only.py", cmd=ПОВТОР, зона="сторожа")
    if not (текстовые or кодовые or новые):
        print(f"тронутых файлов .py против {a.ревизия} нет — сверять нечего")
        if getattr(a, "только_текст"):
            return _verdict.close("правка текстовая", 0,
                                  ".venv/bin/python scripts/guards/text_only.py --только-текст",
                                  улика="нет", почему_молчит=f"против {a.ревизия} не тронут ни один .py")
        return 0
    print(f"правка текстовая ({len(текстовые)}): {', '.join(текстовые) or '—'}")
    for строка in кодовые:
        print(f"✗ задет КОД: {строка}")
    if новые:
        print(f"○ сверять не с чем ({len(новые)}): {', '.join(новые)} — в {a.ревизия} их нет")
    if getattr(a, "только_текст") and кодовые:
        print(f"\nУтверждение «правка только текстовая» НЕВЕРНО: файлов с задетым кодом "
              f"{len(кодовые)}. Набор этого не покажет — он остаётся зелёным.")
        return _verdict.close("правка текстовая", 1,
                              ".venv/bin/python scripts/guards/text_only.py --только-текст",
                              улика="есть")
    if getattr(a, "только_текст"):
        return _verdict.close("правка текстовая", 0,
                              ".venv/bin/python scripts/guards/text_only.py --только-текст",
                              улика="есть")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
