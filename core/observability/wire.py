"""
core/observability/wire.py — отказы НИЖЕ обработчика

## Назначение
До нашего кода запрос успевает быть отвергнут: чужой метод и неизвестный путь отбивает маршрутизатор,
битый HTTP и разбухший заголовок — сам разбор протокола. Замер показал, что все они уходили бесследно,
а именно ими бьют по серверу, который ещё не понимает запрос как MCP.

## Границы
Наблюдаем ЧУЖИМ механизмом, своим оставляем политику: маршрут даёт `web.middleware`, разбор протокола
сообщает через собственный логгер. Отдельного парсера HTTP здесь нет и быть не должно.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging


def route_watch(trail):
    """Middleware: отказ маршрутизатора (чужой метод, неизвестный путь) — до нашего обработчика.

    Наш обработчик такого запроса не видит вовсе, поэтому уровнем записи он не «периметр»: там сервер
    уже разобрал конверт, а здесь ещё не выбрал, кому его отдать.
    """
    from aiohttp import web

    @web.middleware
    async def middleware(request, handler):
        try:
            return await handler(request)
        except web.HTTPException as refusal:
            if trail is not None and refusal.status >= 400:
                trail.refusal("route", f"HTTP_{refusal.status}", refusal.reason or "",
                              rpc=f"{request.method} {request.path}",
                              args={"Host": request.headers.get("Host", ""),
                                    "Origin": request.headers.get("Origin", ""),
                                    "method": request.method, "path": request.path,
                                    "ip": request.remote or ""})
            raise

    return middleware


class WireWatch(logging.Handler):
    """Слушает логгер разбора протокола: то, что отвергнуто до маршрутизации.

    Своего парсера HTTP тут нет намеренно — библиотека уже разобрала и уже сказала. Пишем факт и
    причину; тела нет по построению, потому что оно и не разобралось.
    """

    def __init__(self, trail):
        super().__init__(level=logging.DEBUG)
        self.trail = trail

    def emit(self, record: logging.LogRecord) -> None:
        if self.trail is None:
            return
        try:
            self.trail.refusal("wire", f"WIRE_{record.levelname}", record.getMessage()[:200],
                               args={"logger": record.name})
        except Exception:                          # наблюдение не имеет права ронять сервер
            pass


def attach(trail) -> WireWatch | None:
    """Подключить слушателя к логгеру разбора. Без следа — не подключаем ничего."""
    if trail is None or not getattr(trail, "enabled", False):
        return None
    watch = WireWatch(trail)
    logging.getLogger("aiohttp.server").addHandler(watch)
    logging.getLogger("aiohttp.server").setLevel(logging.DEBUG)
    return watch


async def socket_watch(runner, trail, period: float, threshold: int) -> None:
    """Сколько соединений держится открытыми. Это и есть наблюдаемая часть самого нижнего слоя.

    Счётчика запросов НА СОЕДИНЕНИИ библиотека наружу не отдаёт, а лезть в приватное значит строить
    обход вокруг чужого API. Зато число удерживаемых публично, и скан портов с медленным
    исчерпанием выглядят именно так: соединения есть, обмена нет.
    """
    if trail is None or not getattr(trail, "enabled", False):
        return
    peak = 0
    while True:
        await asyncio.sleep(period)
        server = getattr(runner, "server", None)
        held = len(getattr(server, "connections", ()) or ())
        # Пишем на ПОДЪЁМЕ, а не на каждом замере: строка в секунду на живом сервере утопит запись,
        # и в ней утонет то, ради чего её читают.
        if held >= threshold and held > peak:
            peak = held
            trail.refusal("socket", "SOCKET_HELD", f"соединений держится открытыми: {held}",
                          args={"held": held, "threshold": threshold})
        elif held < threshold:
            peak = 0


def start_socket_watch(runner, trail, declaration: dict | None):
    """Задача наблюдения за соединениями. Нет объявления — нет задачи, и это видно в конфиге."""
    cfg = (declaration or {}).get("socket") or {}
    if not cfg.get("enabled", False):
        return None
    task = asyncio.ensure_future(socket_watch(runner, trail, float(cfg.get("period_sec") or 1.0),
                                              int(cfg.get("held_threshold") or 8)))
    return task


async def stop_socket_watch(task) -> None:
    """Снять задачу наблюдения. Без снятия остановка сервера ждёт её вечно."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
