"""
core/paths.py — Единая точка containment для workspace/

## Назначение
Проверка путей: любой путь к данным ОБЯЗАН оставаться внутри workspace/.
Одна реализация safe-join для server.py, state_manager.py и будущих tools.

## Архитектура (G17)
- Containment = choke-point, а не проверка в каждом хендлере
- Нарушение → ValueError → вызывающий код маппит в PATH_ESCAPE (G15)
- Стыкуется с D1 (fs_*) и D29 (state_manager)

## Паттерн
Path.resolve() → is_relative_to(root) — как в MCP servers filesystem (path-validation.ts)
"""

from pathlib import Path


class PathEscapeError(ValueError):
    """Путь выходит за пределы workspace/ (traversal). Подтип ValueError — back-compat
    с `except ValueError` у вызывающих; позволяет отличать escape от прочих ValueError (F37)."""


def safe_resolve(path: str, workspace: Path) -> Path:
    """Разрешение пути с containment внутри workspace/.

    Резолвит символические `..`/абсолютные пути и проверяет, что итог
    остаётся внутри workspace/. Иначе — ValueError (path traversal).

    Args:
        path: Путь относительно workspace/
        workspace: Абсолютный путь к workspace/

    Returns:
        Абсолютный Path внутри workspace/

    Raises:
        PathEscapeError: если путь выходит за пределы workspace/ (подтип ValueError)
    """
    root = workspace.resolve()
    target = (root / path).resolve()
    if target != root and not target.is_relative_to(root):
        raise PathEscapeError(f"path escapes workspace: {path}")
    return target
