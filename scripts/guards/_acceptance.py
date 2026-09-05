#!/usr/bin/env python3
"""scripts/guards/_acceptance.py — общее для обеих приёмок: сценарии, задания, границы задачи.

Сам не судит и не советует: держит то, что у приёмки студии и приёмки сервера одинаково по
существу — объявление сценариев, роды улик, спектр заданий и вычисление тронутого вне зоны. Хвост
суда (потоки + подпись) сюда не входит: он один на ВСЕХ сторожей и живёт в `_verdict.py`. Две копии
этого кода разъехались бы на первой правке, а разъехавшаяся половина работает наполовину.
"""
import fnmatch
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _verdict                                                            # noqa: E402

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


touched = _verdict.touched


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
