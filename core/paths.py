"""
core/paths.py — Единая точка containment для workspace/ + запрет секретных путей

## Назначение
Проверка путей: любой путь к данным ОБЯЗАН оставаться внутри workspace/. Одна реализация
safe-join для server.py, state_manager.py и tools/.

## Архитектура (G17)
- Containment = choke-point, а не проверка в каждом хендлере
- Нарушение → ValueError → вызывающий код маппит в PATH_ESCAPE (G15)
- Стыкуется с D1 (fs_*) и D29 (state_manager)

## Секретные пути (S23): внутри рабочей области есть то, чего не читает НИКТО
Ключи провайдеров лежат на уровне канала — там же, где его данные, потому что они и есть
свойство канала. Но ни один инструмент до них не дотягивается: имя каталога объявлено закрытым,
и запрет стоит здесь, в той же точке, что и containment, — иначе его пришлось бы помнить в
каждом хендлере, и первый забывший открыл бы дверь. Сервер читает секрет единственным путём —
`core/secrets.py` с явным `allow_secrets=True`.

**Fail-closed:** список закрытых имён приходит из `config/firewall.yaml`, но встроенный минимум
живёт здесь. Не загрузился конфиг — запрет остаётся, а не исчезает.

## Паттерн
Path.resolve() → is_relative_to(root) — как в MCP servers filesystem (path-validation.ts)
"""

from pathlib import Path

# Встроенный минимум: даже без конфига каталог секретов закрыт. Конфиг может ДОБАВИТЬ имена
# (`firewall.yaml → secret_paths`), но не может опустошить этот набор — иначе правка одной
# строки YAML тихо открывала бы ключи всем инструментам.
BUILTIN_SECRET_DIRS = frozenset({".secrets"})
_secret_dirs: set[str] = set(BUILTIN_SECRET_DIRS)


class PathEscapeError(ValueError):
    """Путь выходит за пределы workspace/ (traversal). Подтип ValueError — back-compat
    с `except ValueError` у вызывающих; позволяет отличать escape от прочих ValueError (F37)."""


class SecretAccessError(ValueError):
    """Путь ведёт в закрытый каталог секретов. Тоже ValueError — старые перехваты не рвутся,
    но код отличает «секрет» от «выход за область»: причины и recovery у них разные."""


def configure_secret_dirs(names) -> set[str]:
    """Объявить закрытые каталоги (вызывается один раз при сборке контекста из firewall.yaml).

    Встроенный минимум остаётся всегда: конфиг только расширяет запрет.
    """
    global _secret_dirs
    extra = {str(n).strip() for n in (names or []) if str(n).strip()}
    _secret_dirs = set(BUILTIN_SECRET_DIRS) | extra
    return set(_secret_dirs)


def secret_dirs() -> set[str]:
    return set(_secret_dirs)


def is_secret_path(path) -> bool:
    """Путь ведёт в закрытый каталог? Проверяется КАЖДЫЙ сегмент, а не только последний."""
    parts = Path(str(path)).parts
    return any(part in _secret_dirs for part in parts)


def safe_resolve(path: str, workspace: Path, allow_secrets: bool = False) -> Path:
    """Разрешение пути с containment внутри workspace/.

    Резолвит символические `..`/абсолютные пути и проверяет, что итог остаётся внутри
    workspace/. Дополнительно закрывает каталоги секретов: `allow_secrets=True` передаёт
    ТОЛЬКО `core/secrets.py` — тот единственный, кому положено туда ходить.

    Args:
        path: Путь относительно workspace/
        workspace: Абсолютный путь к workspace/
        allow_secrets: Разрешить закрытый каталог (служебный доступ сервера)

    Returns:
        Абсолютный Path внутри workspace/

    Raises:
        PathEscapeError: если путь выходит за пределы workspace/ (подтип ValueError)
        SecretAccessError: если путь ведёт в закрытый каталог (подтип ValueError)
    """
    root = workspace.resolve()
    target = (root / path).resolve()
    if target != root and not target.is_relative_to(root):
        raise PathEscapeError(f"path escapes workspace: {path}")
    if not allow_secrets and is_secret_path(target.relative_to(root) if target != root else ""):
        raise SecretAccessError(f"secret path is closed: {path}")
    return target
