#!/usr/bin/env python3
"""scripts/guards/acceptance_studio.py — ПРИЁМКА правки ИИ в дереве студии (React/TS).

Условия приёмки, каждое на ПАРНОЙ улике, а не на счёте литералов:
  П1 компонент не сломан — тег ↔ импорт, импорт ↔ экспорт цели;
  П2 стиль не сменён — значение ↔ токен того же рода, форма ↔ снимок рядом с деревом;
  П8 стиль и анимация не потеряны — снимок хранит объявления ДОСЛОВНО, `--восстановить` печатает
     исчезнувшее той же строкой (похожее возвратом не считается);
  П3 в границах задачи и без мёртвого — тронутое ↔ зона, проп ↔ читатель;
  П4 компонент адресуем — `data-component` равен имени и уникален, `--кто` даёт файл и строку.

Дерево по умолчанию — эмуляция (`tests/studio_emulation/app`): студии на диске ещё нет. Чужое
дерево (копию стенда) судит `--дерево`; снимок лежит рядом с деревом и едет вместе с копией.
Совет (`--совет`) выбирает сценарии по роду улики; объявление — `acceptance_scenarios.yaml`,
задания стенда — `tasks.yaml` рядом с деревом.
"""
import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _studio_surface as surface                                          # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TREE = ROOT / "tests" / "studio_emulation" / "app"
SNAPSHOT = "surface_baseline.json"
SCENARIOS = Path(__file__).resolve().parent / "acceptance_scenarios.yaml"
TASKS = "tasks.yaml"
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


def _snapshot(tree: Path) -> dict | None:
    shot = tree.parent / SNAPSHOT
    return json.loads(shot.read_text(encoding="utf-8")) if shot.exists() else None


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
    for name in sorted(set(was.get("tokens", {})) | set(now.get("tokens", {}))):
        прежде, теперь = was["tokens"].get(name), now["tokens"].get(name)
        if прежде != теперь:
            notes.append(f"токен {name} сменился: было {прежде!r}, стало {теперь!r} — правка "
                         f"токена меняет ВСЁ приложение, а не одно место")
    прежние, нынешние = was.get("components", {}), now.get("components", {})
    for name in sorted(set(прежние) | set(нынешние)):
        было, стало = прежние.get(name), нынешние.get(name)
        if было == стало:
            continue
        if было is None or стало is None:
            notes.append(f"компонент {name} {'исчез' if стало is None else 'появился'} против "
                         f"снимка формы — если это и есть задача, --bless")
            continue
        # Поля называются поимённо, а не свалкой двух словарей: свалку не читают, а «стиль»
        # разбирает П8 подробнее — второй раз печатать его здесь значит топить обе находки.
        разошлось = [поле for поле in sorted(set(было) | set(стало))
                     if поле != "стиль" and было.get(поле) != стало.get(поле)]
        for поле in разошлось:
            notes.append(f"{name}: {поле} разошлось со снимком — было {было.get(поле)!r}, "
                         f"стало {стало.get(поле)!r}; если смена заявлена, --bless")
    return notes


def motion(got: dict, tree: Path = TREE, **_) -> list[str]:
    """П8: стиль и анимация не теряются молча, а исчезнувшее называется дословно.

    Отдельно от П2: там речь о смене ФОРМЫ (какие токены и пропсы), здесь — о ПОТЕРЕ значения.
    Разница практическая: «похожая анимация» проходит сравнение формы и не проходит сравнение
    объявлений, а вернуть без дословной строки нельзя ничем, кроме бэкапа, которого нет.
    """
    было = _snapshot(tree)
    if было is None:
        return []                       # об отсутствии снимка уже сказала П2 — второй раз молчим
    notes = []
    for имя, item in sorted(got["components"].items()):
        прежде = (было.get("components", {}).get(имя) or {}).get("стиль", {})
        теперь = item["стиль"]
        for свойство, значение in sorted(прежде.items()):
            if свойство not in теперь:
                notes.append(f"{item['file']} — у {имя} ИСЧЕЗЛО объявление {свойство}: было "
                             f"`{свойство}: {значение}`{_worth(было, значение)}")
            elif теперь[свойство] != значение:
                notes.append(f"{item['file']} — у {имя} подменено {свойство}: было "
                             f"`{значение}`{_worth(было, значение)}, стало `{теперь[свойство]}`")
    for имя, тело in sorted(было.get("кадры", {}).items()):
        if имя not in got["кадры"]:
            notes.append(f"кадры анимации `{имя}` исчезли: было `@keyframes {имя} {{ {тело} }}`")
        elif got["кадры"][имя] != тело:
            notes.append(f"кадры анимации `{имя}` подменены: было `{тело}`, стало "
                         f"`{got['кадры'][имя]}`")
    зовут = {выражение.strip("\"'`") for item in got["components"].values()
             for свойство, выражение in item["стиль"].items() if свойство == "animationName"}
    notes += [f"анимация зовёт кадры `{имя}`, которых в дереве нет: она не проиграется"
              for имя in sorted(зовут - set(got["кадры"]))]
    return notes


def _worth(было: dict, выражение: str) -> str:
    """Значение токена рядом с его именем: восстанавливают по строке, а сверяют глазами по числу."""
    путь = выражение.removeprefix("tokens.")
    значение = было.get("tokens", {}).get(путь)
    return f" (= {значение})" if значение else ""


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
          ("П8 стиль и анимация потеряны", motion),
          ("П3а вышел за рамки задачи", scope),
          ("П3б мёртвый код", dead),
          ("П4 адресация компонента", addressable))


def scenarios(path: Path = SCENARIOS) -> dict:
    if not path.exists():
        sys.exit(f"acceptance_studio: нет объявления сценариев {path} — советовать не из чего")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def tasks(tree: Path) -> dict:
    """Задания стенда лежат рядом с деревом — копия дерева увозит их с собой."""
    path = tree.parent / TASKS
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def rods_of(files: list[str], config: dict) -> list[str]:
    """Род улики выводится из тронутых файлов: спрашивать его у ИИ значило бы верить ему на слово."""
    out = []
    for name, item in config.get("роды", {}).items():
        globs = item.get("когда") or []
        if any(fnmatch.fnmatch(name_of, glob)
               for name_of in [*files, *(Path(f).name for f in files)] for glob in globs):
            out.append(name)
    return out


def advise(config: dict, rods: list[str]) -> int:
    """Совет эксперта: что здесь ломается, почему и ЧЕМ это доказать. Вердикта не выносит."""
    выбранные = [s for s in config.get("сценарии", [])
                 if not rods or set(s.get("улики", [])) & set(rods)]
    print(f"── улика: {', '.join(rods) if rods else 'не названа — показаны все сценарии'}; "
          f"сценариев: {len(выбранные)}")
    for item in выбранные:
        print(f"\n▸ {item['имя']}  [{item['состояние']}]")
        print(f"  больно:    {item['больно']}")
        print(f"  ломается:  {item['ломается']}")
        print(f"  доказать:  {item['доказать']}")
        если = item.get("исполнитель")
        print(f"  ловит:     {если['набор']} :: {если['метка']}" if если
              else f"  судимым станет: {item['станет-судимым']}")
        print(f"  журнал:    {item['журнал']}")
    if not выбранные:
        print("  сценария на эту улику нет — это находка, а не тишина: заведи его в "
              "scripts/guards/acceptance_scenarios.yaml")
    return 0


def restore(got: dict, tree: Path, имя: str) -> int:
    """`--восстановить`: что у компонента исчезло и ЧЕМ это было — строкой, годной к вставке."""
    было = _snapshot(tree)
    if было is None:
        print(f"{tree.parent / SNAPSHOT} — снимка нет: восстанавливать не из чего", file=sys.stderr)
        return 2
    прежде = (было.get("components", {}).get(имя) or {}).get("стиль")
    if прежде is None:
        print(f"компонента {имя!r} в снимке нет", file=sys.stderr)
        return 2
    теперь = (got["components"].get(имя) or {}).get("стиль", {})
    пропало = {с: з for с, з in прежде.items() if теперь.get(с) != з}
    if not пропало:
        print(f"{имя}: объявления стиля совпадают со снимком — восстанавливать нечего")
        return 0
    print(f"{имя} ({прежде and (было['components'][имя]['file'])}) — вернуть дословно:")
    for свойство, значение in sorted(пропало.items()):
        print(f"  {свойство}: {значение},{_worth(было, значение)}")
    for кадр, тело in sorted(было.get("кадры", {}).items()):
        if got["кадры"].get(кадр) != тело:
            print(f"  @keyframes {кадр} {{ {тело} }}")
    return 0


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
    parser.add_argument("--восстановить", help="компонент: печатает исчезнувшие объявления дословно")
    parser.add_argument("--совет", action="store_true", help="сценарии эксперта по роду улики")
    parser.add_argument("--улика", action="append", default=[], help="род улики (см. --совет без улик)")
    parser.add_argument("--файл", action="append", default=[], help="тронутый файл — род улики выводится сам")
    parser.add_argument("--задания", action="store_true", help="спектр заданий стенда")
    parser.add_argument("--задача", help="id задания: его зона становится границей суда")
    args = parser.parse_args(argv)

    if args.совет:
        config = scenarios()
        роды = list(dict.fromkeys(args.улика + rods_of(args.файл, config)))
        неизвестные = [r for r in роды if r not in config.get("роды", {})]
        if неизвестные:
            print(f"acceptance_studio: род улики не объявлен: {неизвестные}; известны "
                  f"{sorted(config.get('роды', {}))}", file=sys.stderr)
            return 2
        return advise(config, роды)

    tree = args.дерево
    if not tree.is_dir():
        print(f"acceptance_studio: дерева студии нет: {tree}", file=sys.stderr)
        return 2
    задания = tasks(tree)
    if args.задания:
        for item in задания.get("задания", []):
            print(f"▸ {item['id']}  (приёмка {' '.join(item['приёмка'])})\n  {item['что']}\n"
                  f"  зона: {' '.join(item['зона'])}")
        return 0
    зоны = list(args.зона)
    if args.задача:
        нашлось = [i for i in задания.get("задания", []) if i["id"] == args.задача]
        if not нашлось:
            print(f"acceptance_studio: задания {args.задача!r} нет в {tree.parent / TASKS}",
                  file=sys.stderr)
            return 2
        зоны += нашлось[0]["зона"]
        print(f"── задание {args.задача}: зона суда взята из объявления, а не из слов")
    got = surface.read(tree)
    if args.восстановить:
        return restore(got, tree, args.восстановить)
    if args.кто:
        return who(got, args.кто)
    if args.bless:
        (tree.parent / SNAPSHOT).write_text(surface.surface_json(tree), encoding="utf-8")
        print(f"снимок формы записан: {tree.parent / SNAPSHOT}")
        return 0
    if not args.суд:
        parser.print_help()
        return 2
    if not зоны:
        print("── П3а вышел за рамки задачи: зона не объявлена (--зона/--задача), критерий не судится")
    failed = False
    for title, check in CHECKS:
        if check is scope and not зоны:
            continue
        notes = check(got, tree=tree, zones=tuple(зоны))
        print(f"── {title}: {'чисто' if not notes else str(len(notes)) + ' шт.'}")
        for note in notes:
            print(f"   ✗ {note}")
        failed = failed or bool(notes)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
