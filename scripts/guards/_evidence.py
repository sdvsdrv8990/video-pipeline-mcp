#!/usr/bin/env python3
"""scripts/guards/_evidence.py — реестр осей сторожа инвариантов (`evidence.yaml`).

Зовут: invariants.

Не судит и кода возврата не даёт. Объявления может не быть (чужое дерево) — тогда осей нет, и сторож
отказывает сам, а не зеленеет на пустом наборе.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DECL = Path(__file__).resolve().parent / "evidence.yaml"


def словарь(path: Path | None = None) -> dict:
    # Путь разрешается в момент вызова: связанный дефолт подменить нельзя, и набор,
    # подставивший своё объявление, молча читал бы боевое.
    path = path or DECL
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}


def оси(path: Path | None = None) -> dict:
    """Оси суда: `храповики` (потолок и совет) и `жёсткие` (любое попадание — отказ)."""
    return словарь(path).get("оси") or {}
