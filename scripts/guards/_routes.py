#!/usr/bin/env python3
"""scripts/guards/_routes.py — источник улики: какие потоки данных задевает тронутый файл.

Не судит и exit-кода не даёт. Отвечает на один вопрос: стоит ли правленный файл рубежом на
маршруте (`tests/routes/routes.yaml`), что через него течёт, что значит обрыв и каким прогоном это
видно. Читатели — приёмка обоих деревьев и шим `vpm-invariants`: своей копии разбора у них больше
нет, а разъехавшиеся копии работают ровно наполовину.

Карты может не быть (клон без наборов, чужое дерево) — тогда «улики нет», а не выдуманный ноль.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def map_path(root: Path = ROOT) -> Path:
    return root / "tests" / "routes" / "routes.yaml"


def routes(path: Path | None = None, root: Path = ROOT) -> list[dict]:
    """Объявленные маршруты. Гниение карты судит `test_routes` — здесь битая просто не улика."""
    path = path or map_path(root)
    if not path.exists():
        return []
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError):
        return []


def crossed(paths: list[str], path: Path | None = None,
            root: Path = ROOT) -> list[tuple[dict, str]]:
    """Пары «маршрут, задетый рубеж». Рубеж бывает не файлом (`конверт MCP`) — сравнение точное."""
    свои, out = set(paths), []
    for маршрут in routes(path, root):
        задеты = свои & {hop.get("at", "") for hop in (маршрут.get("hops") or [])}
        if задеты:
            out.append((маршрут, sorted(задеты)[0]))
    return out


def lines(paths: list[str], path: Path | None = None, root: Path = ROOT) -> list[str]:
    """Готовые строки тому, кто правит рубеж: что течёт, что значит обрыв, чем это видно."""
    return [f"поток `{м.get('route')}` — {м.get('what')}; правишь рубеж {рубеж}. "
            f"Обрыв значит: {м.get('means')}. "
            f"Видно прогоном: {(м.get('proof') or {}).get('scenario', 'опора не объявлена')}"
            for м, рубеж in crossed(paths, path, root)]
