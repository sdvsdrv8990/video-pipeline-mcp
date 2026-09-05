#!/usr/bin/env python3
"""scripts/guards/_acceptance.py — общее для обеих приёмок: сценарии, задания, границы задачи.

Сам не судит и не советует: держит то, что у приёмки студии и приёмки сервера одинаково по
существу — объявление сценариев, роды улик, спектр заданий, вычисление тронутого вне зоны и хвост
суда: задетые потоки данных плюс подпись вердикта в общий журнал тандема. Две копии этого кода
разъехались бы на первой правке, а разъехавшаяся половина работает наполовину.
"""
import fnmatch
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _routes                                                             # noqa: E402
import _stamp                                                              # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = Path(__file__).resolve().parent / "acceptance_scenarios.yaml"
TASKS = "tasks.yaml"


def scenarios(path: Path = SCENARIOS) -> dict:
    if not path.exists():
        sys.exit(f"приёмка: нет объявления сценариев {path} — советовать не из чего")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def tasks(рядом: Path) -> dict:
    """Задания стенда лежат рядом с деревом — копия дерева увозит их с собой."""
    path = рядом / TASKS
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def rods_of(files: list[str], config: dict) -> list[str]:
    """Род улики выводится из тронутых файлов: спрашивать его у ИИ значило бы верить ему на слово."""
    out = []
    for name, item in config.get("роды", {}).items():
        globs = item.get("когда") or []
        if any(fnmatch.fnmatch(имя, glob)
               for имя in [*files, *(Path(f).name for f in files)] for glob in globs):
            out.append(name)
    return out


def advise(config: dict, rods: list[str], дерево: str) -> int:
    """Совет: что здесь ломается, почему и ЧЕМ это доказать. Вердикта не выносит."""
    выбранные = [s for s in config.get("сценарии", [])
                 if s.get("дерево", "студия") == дерево
                 and (not rods or set(s.get("улики", [])) & set(rods))]
    print(f"── дерево: {дерево}; улика: {', '.join(rods) if rods else 'не названа — все сценарии'}; "
          f"сценариев: {len(выбранные)}")
    for item in выбранные:
        print(f"\n▸ {item['имя']}  [{item['состояние']}]")
        print(f"  больно:    {item['больно']}")
        print(f"  ломается:  {item['ломается']}")
        print(f"  доказать:  {item['доказать']}")
        кто = item.get("исполнитель")
        print(f"  ловит:     {кто['набор']} :: {кто['метка']}" if кто
              else f"  судимым станет: {item['станет-судимым']}")
        print(f"  журнал:    {item['журнал']}")
    if not выбранные:
        print("  сценария на эту улику нет — это находка, а не тишина: заведи его в "
              "scripts/guards/acceptance_scenarios.yaml")
    return 0


def touched(root: Path = ROOT) -> list[str]:
    return [line[3:].strip().split(" -> ")[-1]
            for line in subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                       capture_output=True, text=True).stdout.splitlines()
            if line[3:]]


def outside(zones: tuple[str, ...], root: Path = ROOT) -> list[str]:
    """Тронутое вне объявленной зоны задачи. Зона не объявлена — судить нечем, и это не «чисто»."""
    if not zones:
        return []
    return [f"{path} — тронуто вне зоны задачи {list(zones)}: правка вышла за рамки поставленного"
            for path in touched(root)
            if not any(fnmatch.fnmatch(path, zone) for zone in zones)]


def zones_of(задания: dict, ключ: str) -> list[str] | None:
    нашлось = [i for i in задания.get("задания", []) if i["id"] == ключ]
    return нашлось[0]["зона"] if нашлось else None


def flows(paths: list[str], root: Path = ROOT) -> list[str]:
    """Что течёт через тронутый рубеж — ЗНАНИЕ приёмки, а не её вердикт.

    Правка рубежа нарушением не является; молчание о ней — является: правящий не узнаёт, что через
    место течёт значение до клиента и чем это видно.
    """
    return _routes.lines(paths, root=root)


def stamp(дерево: str, нарушений: int, потоки: list[str], команда: str,
          root: Path = ROOT) -> str:
    """Вердикт приёмки — в ОБЩИЙ журнал тандема, иначе он живёт до закрытия терминала.

    Роль `УЛИКА` здесь запрещена намеренно: её подписи считает гейт поставки, и приёмка,
    подписавшись уликой, удовлетворяла бы его собственным прогоном.
    """
    роль = "ОТКАЗ" if нарушений else "ВЕРДИКТ"
    итог = f"{нарушений} нарушений" if нарушений else "чисто"
    _, подпись = _stamp.sign("гейт", роль, f"приёмка правки в дереве «{дерево}»: {итог}",
                             intent=f"приёмка-{дерево}", expected="правка принимается",
                             actual=итог, cmd=команда,
                             detail={"дерево": дерево, "нарушений": нарушений,
                                     "потоки": потоки}, root=root)
    return подпись


def close(дерево: str, нарушений: int, команда: str, root: Path = ROOT) -> int:
    """Хвост суда, общий у обеих приёмок: задетые потоки + подпись в журнал тандема."""
    потоки = flows(touched(root), root=root)
    print(f"── задетые потоки данных: {len(потоки) or 'нет'}")
    for строка in потоки:
        print(f"   ⇢ {строка}")
    print(stamp(дерево, нарушений, потоки, команда, root=root))
    return 1 if нарушений else 0
