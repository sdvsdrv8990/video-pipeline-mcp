"""tests/harness/auth_proxy.py — прокси, подставляющий ключ за клиента, который его не умеет.

Зачем: сторонние conformance-клиенты не шлют наш `Authorization: Bearer`, и весь прогон
упирается в `AUTH_REQUIRED`. Калитка `MCP_ALLOW_NO_AUTH` для этого не годится — она сняла бы
проверяемый слой целиком (помечена на снос). Прокси оставляет auth включённым: сервер
по-прежнему требует ключ, просто заголовок добавляет посредник.

Ничего не переписывает: статус, заголовки и тело возвращаются как есть — иначе прокси начал бы
чинить несоответствия вместо того, чтобы их показывать.
"""

import asyncio
import threading

from aiohttp import ClientSession, web

_HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade",
               "proxy-authenticate", "proxy-authorization", "te", "trailer"}


class AuthProxy:
    """Слушает свой порт, добавляет ключ и передаёт запрос дальше. Запуск — в своём потоке."""

    def __init__(self, target_url: str, token: str, host: str = "127.0.0.1", port: int = 0):
        self.target = target_url
        self.token = token
        self.host = host
        self._port = port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.url = ""

    async def _forward(self, request: "web.Request") -> "web.Response":
        body = await request.read()
        # Host передаётся КАК ЕСТЬ: именно по нему сервер отбивает DNS-rebinding, и прокси,
        # подменяющий его на свой, скрыл бы проверку вместо того, чтобы её показать.
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
        headers["Authorization"] = f"Bearer {self.token}"
        async with ClientSession() as sess:
            async with sess.request(request.method, self.target, data=body,
                                    headers=headers, allow_redirects=False) as resp:
                raw = await resp.read()
                out = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP}
                return web.Response(body=raw, status=resp.status, headers=out)

    def _serve(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._forward)
        self._runner = web.AppRunner(app)
        self._loop.run_until_complete(self._runner.setup())
        site = web.TCPSite(self._runner, self.host, self._port)
        self._loop.run_until_complete(site.start())
        self.url = f"http://{self.host}:{site._server.sockets[0].getsockname()[1]}/mcp"
        self._ready.set()
        self._loop.run_forever()

    def __enter__(self) -> "AuthProxy":
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("прокси не поднялся за 10 секунд")
        return self

    def __exit__(self, *exc) -> None:
        if self._loop and self._runner:
            # cleanup дожидаемся: брошенная корутина печатает «Task was destroyed» в чужой вывод.
            fut = asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop)
            try:
                fut.result(timeout=5)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
