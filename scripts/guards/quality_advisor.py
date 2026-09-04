#!/usr/bin/env python3
"""scripts/guards/quality_advisor.py — сторож качества студии: приёмка правки ИИ по React.

Четыре условия приёмки, каждое судится ПАРНОЙ уликой, а не счётом литералов:
  П1 компонент не сломан — тег ↔ импорт, импорт ↔ экспорт цели;
  П2 стиль не сменён — значение ↔ токен того же рода, форма ↔ снимок рядом с деревом;
  П3 не вышел за рамки и не оставил мёртвого — тронутое ↔ зона задачи, проп ↔ читатель;
  П4 компонент адресуем — `data-component` равен имени и уникален; `--кто` даёт файл и строку.

Дерево по умолчанию — эмуляция (`tests/studio_emulation/app`), потому что студии на диске ещё нет.
Чужое дерево (копию стенда) судит `--дерево`: снимок формы лежит рядом с деревом и едет с копией.
"""
import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _studio_surface as surface                                          # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TREE = ROOT / "tests" / "studio_emulation" / "app"
SNAPSHOT = "surface_baseline.json"
ROOTS = ("App",)          # компоненты, которых законно не рисует никто: вершина дерева отрисовки


def _defined_in(got: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for name, item in got["components"].items():
        out.setdefault(item["file"], set()).add(name)
    return out


def broken(got: dict, **_) -> list[str]:
    """П1: тег без импорта и импорт в файл, где такого экспорта уже нет.

    Две половины одной беды. Первая ловит снесённый импорт, вторая — ПЕРЕИМЕНОВАННЫЙ компонент:
    у импортёра строка на месте и тег на месте, а в цели экспорта нет, и приложение падает на
    отрисовке. Без второй половины самый частый промах ИИ по React проезжает зелёным.
    """
    where = _defined_in(got)
    notes = []
    for name, item in sorted(got["components"].items()):
        known = set(item["imported"]) | where.get(item["file"], set())
        for tag in item["tags"]:
            if tag not in known:
                notes.append(f"{item['file']}:{item['line']} — {name} рисует <{tag}>, которого не "
                             f"импортировал и рядом не объявил: компонент сломан")
    for source, module in sorted(got.get("modules", {}).items()):
        for принос in module["imports"]:
            цель = surface.resolve(source, принос["from"])
            if цель is None:
                continue
            дом = next((name for name in got["modules"]
                        if name.rsplit(".", 1)[0] in (цель, f"{цель}/index")), None)
            if дом is None:
                notes.append(f"{source} — импорт из {принос['from']!r} ведёт в файл, которого в "
                             f"дереве нет: компонент сломан")
                continue
            for имя in принос["names"]:
                if имя not in got["modules"][дом]["exports"]:
                    notes.append(f"{source} — импортирует {имя} из {принос['from']!r}, а {дом} "
                                 f"такого не экспортирует: компонент сломан")
    return notes


def styles(got: dict, tree: Path = TREE, **_) -> list[str]:
    """П2: значение мимо токена, токен без читателя и незаявленная смена формы."""
    notes = [f"{item['file']}:{item['line']} — {item['prop']}: \"{item['value']}\" мимо токена: "
             f"единый стиль держится объявлением, а не глазами"
             for item in got["style_literals"]]
    notes += [f"токен {path} объявлен, а читателя нет: мёртвая половина декларации"
              for path in sorted(set(got["tokens"]) - set(got["token_use"]))]
    shot = tree.parent / SNAPSHOT
    if not shot.exists():
        return notes + [f"{shot} — снимка формы нет: смену стиля сравнить не с чем "
                        f"(создать: --bless)"]
    was = json.loads(shot.read_text(encoding="utf-8"))
    now = json.loads(surface.surface_json(tree))
    for key in ("tokens", "components"):
        было, стало = was.get(key, {}), now.get(key, {})
        for name in sorted(set(было) | set(стало)):
            if было.get(name) != стало.get(name):
                notes.append(f"форма разошлась со снимком ({key}.{name}): было {было.get(name)!r}, "
                             f"стало {стало.get(name)!r} — если смена стиля заявлена, --bless")
    return notes


def scope(got: dict, tree: Path = TREE, zones: tuple[str, ...] = (), root: Path = ROOT,
          **_) -> list[str]:
    """П3а: тронутое вне объявленной зоны задачи. Зона не объявлена — судить нечем."""
    if not zones:
        return []
    done = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                          capture_output=True, text=True)
    touched = [line[3:].strip().split(" -> ")[-1] for line in done.stdout.splitlines() if line[3:]]
    return [f"{path} — тронуто вне зоны задачи {list(zones)}: правка вышла за рамки поставленного"
            for path in touched
            if not any(fnmatch.fnmatch(path, zone) for zone in zones)]


def dead(got: dict, **_) -> list[str]:
    """П3б: компонент, которого никто не рисует, и проп, которого никто не читает."""
    drawn = {tag for item in got["components"].values() for tag in item["tags"]}
    notes = [f"{item['file']}:{item['line']} — {name} не отрисован никем: мёртвый код"
             for name, item in sorted(got["components"].items())
             if name not in drawn and name not in ROOTS]
    for name, item in sorted(got["components"].items()):
        тело = item["body"].split("}", 1)[-1] if item["props"] else ""
        notes += [f"{item['file']}:{item['line']} — проп {name}.{prop} объявлен, читателя нет: "
                  f"мёртвая половина" for prop in item["props"] if prop not in тело]
    return notes


def addressable(got: dict, **_) -> list[str]:
    """П4: маркер у каждого компонента, равен имени и уникален на дерево."""
    notes = []
    for name, item in sorted(got["components"].items()):
        if name not in item["markers"]:
            notes.append(f"{item['file']}:{item['line']} — у {name} нет своего "
                         f"data-component=\"{name}\": по скриншоту его не адресовать")
    места: dict[str, set[str]] = {}
    for item in got["components"].values():
        for marker in item["markers"]:
            места.setdefault(marker, set()).add(item["file"])
    notes += [f"маркер {marker!r} стоит в {sorted(files)}: по нему компонент не определить"
              for marker, files in sorted(места.items()) if len(files) > 1]
    return notes


CHECKS = (("П1 компонент сломан", broken),
          ("П2 стиль мимо токена и смена формы", styles),
          ("П3а вышел за рамки задачи", scope),
          ("П3б мёртвый код", dead),
          ("П4 адресация компонента", addressable))


def who(got: dict, marker: str) -> int:
    """`--кто`: маркер из скриншота или имя из описания → файл и строка."""
    for name, item in sorted(got["components"].items()):
        if marker in (name, *item["markers"]):
            print(f"{item['file']}:{item['line']} — {name}; пропсы {item['props']}; "
                  f"токены {item['tokens']}")
            return 0
    близкие = sorted(n for n in got["components"] if marker.lower() in n.lower())
    print(f"маркера {marker!r} в дереве нет" + (f"; близкие: {близкие}" if близкие else ""),
          file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--суд", action="store_true", help="приёмка правки по четырём условиям")
    parser.add_argument("--дерево", type=Path, default=TREE, help="дерево студии (по умолчанию — эмуляция)")
    parser.add_argument("--зона", action="append", default=[], help="glob разрешённой зоны задачи")
    parser.add_argument("--кто", help="маркер или имя компонента → файл:строка")
    parser.add_argument("--bless", action="store_true", help="записать снимок формы")
    args = parser.parse_args(argv)

    tree = args.дерево
    if not tree.is_dir():
        print(f"quality_advisor: дерева студии нет: {tree}", file=sys.stderr)
        return 2
    got = surface.read(tree)
    if args.кто:
        return who(got, args.кто)
    if args.bless:
        (tree.parent / SNAPSHOT).write_text(surface.surface_json(tree), encoding="utf-8")
        print(f"снимок формы записан: {tree.parent / SNAPSHOT}")
        return 0
    if not args.суд:
        parser.print_help()
        return 2
    if not args.зона:
        print("── П3а вышел за рамки задачи: зона не объявлена (--зона), критерий не судится")
    failed = False
    for title, check in CHECKS:
        if check is scope and not args.зона:
            continue
        notes = check(got, tree=tree, zones=tuple(args.зона))
        print(f"── {title}: {'чисто' if not notes else str(len(notes)) + ' шт.'}")
        for note in notes:
            print(f"   ✗ {note}")
        failed = failed or bool(notes)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
