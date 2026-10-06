"""
core/trash.py — Корзина: разрушающая файловая операция обратима владельцем, а не агентом

## Назначение
Удаление и полная перезапись файла уводят прежнее содержимое в корзину партией
`<время>-<случайный хвост>`, сохраняя относительный путь. Подтверждение `force` решает тот же
агент, которого могли уговорить инъекцией или подменённым ответом, поэтому от потери данных
защищает не оно, а обратимость на стороне сервера.

## Границы
- Корзина лежит РЯДОМ с `workspace/`, а не внутри: containment (`core/paths.safe_resolve`) не
  пускает туда ни один инструмент — агент не может ни очистить корзину, ни прочитать из неё.
- Очистка и восстановление — руками владельца; инструмента для этого нет намеренно.
"""

import secrets
import shutil
import time
from pathlib import Path

# Только владелец процесса: в корзину уходит всё, что было в рабочей области, включая чужой текст.
TRASH_MODE = 0o700


def trash_root(workspace: Path) -> Path:
    workspace = workspace.resolve()
    return workspace.parent / f"{workspace.name}.trash"


def _batch_home(workspace: Path, target: Path) -> tuple[str, Path]:
    batch = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(3)}"
    root = trash_root(workspace)
    root.mkdir(mode=TRASH_MODE, parents=True, exist_ok=True)
    destination = root / batch / target.resolve().relative_to(workspace.resolve())
    destination.parent.mkdir(parents=True, exist_ok=True)
    return batch, destination


def discard(target: Path, workspace: Path) -> str:
    batch, destination = _batch_home(workspace, target)
    shutil.move(str(target), str(destination))
    return batch


def keep_copy(target: Path, workspace: Path) -> str:
    batch, destination = _batch_home(workspace, target)
    shutil.copy2(target, destination)
    return batch
