#!/usr/bin/env python3
""".claude/skills/studio-web-standards/scripts/contrast.py — контраст ПАР токенов цвета студии.

Контраст — свойство пары «текст на фоне», и объявлены обе стороны в одном файле токенов. Поэтому
меряется объявление, а не экран: пара, прошедшая здесь, проходит везде, где её используют.
Формула — относительная яркость и отношение контраста WCAG 2.2 (критерии 1.4.3 и 1.4.11).

Роль токена выводится из имени: фон содержит одно из слов `--фон`, линия — `--линия`, остальное —
передний план. Какие пары реально встречаются в разметке, скрипт не знает, поэтому это замер, а не
вердикт: слабая пара законна для крупного текста и декора, если так и решено рядом с токеном.
Значения не в `#hex` (oklch палитры Tailwind) не разбираются и называются вслух.
"""
import argparse
import re
import sys
from pathlib import Path

TOKEN = re.compile(r"--color-([\w-]+)\s*:\s*([^;]+);")
TEXT, LARGE = 4.5, 3.0              # WCAG 2.2: 1.4.3 обычный текст; 1.4.3 крупный и 1.4.11 не-текст


def _rgb(value: str) -> tuple[float, float, float] | None:
    hexpart = value.strip().removeprefix("#")
    if not value.strip().startswith("#") or len(hexpart) not in (3, 6):
        return None
    if len(hexpart) == 3:
        hexpart = "".join(c * 2 for c in hexpart)
    try:
        r, g, b = (int(hexpart[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None
    return r, g, b


def luminance(rgb: tuple[float, float, float]) -> float:
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def verdict(value: float) -> str:
    if value >= TEXT:
        return "текст"
    return "крупный/UI" if value >= LARGE else "только декор"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tokens", type=Path, help="файл токенов (studio/app/tokens.css)")
    parser.add_argument("--фон", nargs="+", default=["ground", "surface"],
                        help="слова в имени токена фона")
    parser.add_argument("--линия", nargs="+", default=["line"], help="слова в имени токена линии")
    args = parser.parse_args(argv)

    if not args.tokens.exists():
        print(f"contrast: файла токенов нет: {args.tokens}", file=sys.stderr)
        return 2
    tokens, unparsed = {}, []
    for name, value in TOKEN.findall(args.tokens.read_text(encoding="utf-8")):
        rgb = _rgb(value)
        if rgb is None:
            unparsed.append(name)
        else:
            tokens[name] = rgb
    if not tokens:
        print(f"contrast: в {args.tokens} нет ни одного `--color-*` в #hex — мерить нечего",
              file=sys.stderr)
        return 2

    grounds = [n for n in tokens if any(w in n for w in args.фон)]
    lines = [n for n in tokens if n not in grounds and any(w in n for w in args.линия)]
    fronts = [n for n in tokens if n not in grounds and n not in lines]
    width = max(map(len, tokens))
    for title, names, floor in (("передний план (текст, 1.4.3)", fronts, TEXT),
                                ("линии и границы (не-текст, 1.4.11)", lines, LARGE)):
        print(f"── {title}")
        for name in names:
            cells = []
            for ground in grounds:
                value = ratio(tokens[name], tokens[ground])
                mark = "" if value >= floor else " ✗"
                cells.append(f"{ground} {value:5.2f} {verdict(value)}{mark}")
            print(f"   {name.ljust(width)}  " + " · ".join(cells))
    if unparsed:
        print(f"── не разобраны (не #hex): {', '.join(unparsed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
