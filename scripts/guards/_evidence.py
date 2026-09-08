#!/usr/bin/env python3
"""scripts/guards/_evidence.py — источник улики: единый словарь улик и роспись сторожей.

Не судит и exit-кода не даёт. Отвечает на три вопроса по одному объявлению (`evidence.yaml`):
какая улика перед нами (род выводится из ТРОНУТЫХ файлов, а не спрашивается у правщика), к какой
из трёх главных она привязана и кто на неё отзывается: сторожа с командой ручного подъёма и хуки,
которых поднимает само событие — по вторым сверяется ФАКТ срабатывания.

Объявления может не быть (клон без сторожей, чужое дерево) — тогда «улики нет», а не пустой ответ,
выдающий себя за полный.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
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


def понятия(path: Path | None = None) -> dict:
    """Значения слова «улика»: их три, и все машинные — без этого правила пишутся по примеру."""
    return словарь(path).get("понятия") or {}


def главные(path: Path | None = None) -> dict:
    return словарь(path).get("главные") or {}


def роды(path: Path | None = None) -> dict:
    return словарь(path).get("роды") or {}


def сторожа(path: Path | None = None) -> dict:
    return словарь(path).get("сторожа") or {}


def хуки(path: Path | None = None) -> dict:
    return словарь(path).get("хуки") or {}


def задачи(path: Path | None = None) -> dict:
    return словарь(path).get("задачи") or {}


def деревья(path: Path | None = None) -> dict:
    """Деревья замера: какие корни меряет карта радиуса и какой из вопросов у каждого судится."""
    return словарь(path).get("деревья") or {}


def горячие(path: Path | None = None) -> dict:
    """Пороги горячей точки: доля правок и порог размера. Нет объявления — судить нечем."""
    return словарь(path).get("горячие") or {}


def оси(path: Path | None = None) -> dict:
    """Оси суда: `храповики` (потолок и совет) и `жёсткие` (любое попадание — отказ)."""
    return словарь(path).get("оси") or {}


def rods_of(files: list[str], path: Path | None = None) -> list[str]:
    """Роды улики по тронутым файлам. Спрашивать род у правщика значило бы верить ему на слово."""
    имена = [*files, *(Path(f).name for f in files)]
    return [имя for имя, род in роды(path).items()
            if any(fnmatch.fnmatch(и, glob) for и in имена for glob in (род.get("когда") or []))]


def main_of(род: str, path: Path | None = None) -> str | None:
    """Главная улика, к которой привязан частный род: по ней и поднимается набор сторожей."""
    return (роды(path).get(род) or {}).get("главная")


def expand(улики: list[str], path: Path | None = None) -> list[str]:
    """Развернуть смесь главных и частных улик в набор ГЛАВНЫХ — единственный ключ росписи."""
    свод, известные = [], главные(path)
    for улика in улики:
        имя = улика if улика in известные else main_of(улика, path)
        if имя and имя not in свод:
            свод.append(имя)
    return свод


def ожидаются(улики: list[str], path: Path | None = None) -> list[str]:
    """Хуки, ОБЯЗАННЫЕ сработать на этих уликах: по ним таск сверяет факт срабатывания.

    Пустой ответ — утверждение «на эту улику события нет», а не «сторожей не нашлось»: у улики
    «чтение» хуков нет по устройству (Read/Grep события не дают), и это записанный остаток.
    """
    нужные = set(expand(улики, path))
    return sorted(имя for имя, дело in хуки(path).items()
                  if нужные & set(дело.get("улики") or []))


def наряд(улики: list[str], path: Path | None = None) -> list[tuple[str, list[str]]]:
    """Кого поднимать на эти улики: пары «сторож, команда». Без команды вручную не поднимается.

    Сторож объявляется либо на ГЛАВНУЮ улику (отзывается на всё под ней), либо на частный РОД
    (только на него). Без второго приёмка студии поднималась на правку сервера — ложное
    срабатывание, а сторожа, который зовёт зря, выключают целиком.
    """
    нужные = set(улики) | set(expand(улики, path))
    return [(имя, list(дело.get("команда") or []))
            for имя, дело in sorted(сторожа(path).items())
            if нужные & set(дело.get("улики") or []) and дело.get("команда")]
