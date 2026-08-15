"""tests/harness — живой сервер под тестом (T2): процесс, протокол, консоль."""

from .console import Console
from .jsonrpc import JsonRpc
from .live_server import LiveServer, LiveServerError, free_port, live_server

__all__ = ["LiveServer", "LiveServerError", "live_server", "free_port", "JsonRpc", "Console"]
