"""core/engines.py — где сервер находит внешний движок (бинарь): PATH, затем объявленные места установки этой ОС
(`config/engines.yaml`, схема `EnginePlaces`). Не нашёлся — вызывающий отказывает кодом реестра, а где искали — в журнал
сервера: пути хоста клиенту не идут."""

import logging
import os
import shutil
import sys

from core.declaration_schemas import EnginePlaces

log = logging.getLogger(__name__)


def locate(spec: EnginePlaces) -> str | None:
    """Путь движка: имена — в PATH, затем в объявленных местах этой ОС по порядку; не нашёлся — None и запись журнала."""
    places = {"linux": spec.linux, "darwin": spec.darwin, "win32": spec.win32}.get(sys.platform, ())
    folders = [os.path.expandvars(place) for place in places]
    for where in (None, *folders):                    # None — PATH процесса
        for name in spec.names:
            if found := shutil.which(name, path=where):
                return found
    log.info("движок %s не найден: PATH и места %s", "/".join(spec.names), folders or "— (для этой ОС не объявлены)")
    return None
