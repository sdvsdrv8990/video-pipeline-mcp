"""core/paths.py — единая точка containment и запрет закрытых путей.

Containment здесь choke-point, а не проверка в каждом хендлере: одна реализация на весь сервер.
Запрет закрытых каталогов стоит в той же точке — иначе его пришлось бы помнить в каждом хендлере,
и первый забывший открыл бы дверь к ключам. Войти внутрь закрытого можно только явным
разрешением: молчаливых исключений тут нет.
"""

from pathlib import Path

# Встроенный минимум: даже без конфига каталог секретов закрыт. Конфиг может ДОБАВИТЬ имена
# (`firewall.yaml → secret_paths`), но не может опустошить этот набор — иначе правка одной
# строки YAML тихо открывала бы ключи всем инструментам.
BUILTIN_SECRET_DIRS = frozenset({".secrets"})
_secret_dirs: set[str] = set(BUILTIN_SECRET_DIRS)


class PathEscapeError(ValueError):
    """Путь выходит за пределы workspace/ (traversal). Подтип ValueError — back-compat
    с `except ValueError` у вызывающих; позволяет отличать escape от прочих ValueError."""


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


def is_secret_path(path) -> bool:
    """Путь ведёт в закрытый каталог? Проверяется КАЖДЫЙ сегмент, а не только последний."""
    parts = Path(str(path)).parts
    return any(part in _secret_dirs for part in parts)


def safe_resolve(path: str, workspace: Path, allow_secrets: bool = False) -> Path:
    """Разрешение пути с containment внутри workspace/.

    Резолвит символические `..`/абсолютные пути и проверяет, что итог остаётся внутри
    workspace/. Дополнительно закрывает каталоги секретов: `allow_secrets=True` передаёт
    ТОЛЬКО `core/secrets.py` — тот единственный, кому положено туда ходить.

    Raises:
        PathEscapeError: путь выходит за пределы workspace/ (подтип ValueError)
        SecretAccessError: путь ведёт в закрытый каталог (подтип ValueError)
    """
    root = workspace.resolve()
    target = (root / path).resolve()
    if target != root and not target.is_relative_to(root):
        raise PathEscapeError(f"path escapes workspace: {path}")
    if not allow_secrets and is_secret_path(target.relative_to(root) if target != root else ""):
        raise SecretAccessError(f"secret path is closed: {path}")
    return target
