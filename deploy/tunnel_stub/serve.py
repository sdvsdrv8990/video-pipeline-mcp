"""serve.py — минимальная веб-заглушка сервера туннеля: на любой путь отдаёт страницу 404.

Только stdlib: на сервере нет Node и нет пакетов. Порт — из переменной PORT (ставит systemd-юнит,
значение объявлено в config/tunnel_diagnostics.yaml → заглушка → порт). Страница читается один раз
при старте; тела запроса не читаем и в журнал ничего не пишем.
"""

import http.server
import os
import socketserver
from pathlib import Path

PORT = int(os.environ.get("PORT", "8080"))
HTML = (Path(__file__).resolve().parent / "index.html").read_bytes()


class Handler(http.server.BaseHTTPRequestHandler):
    def _head(self) -> None:
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(HTML)))
        self.end_headers()

    def do_GET(self) -> None:
        self._head()
        self.wfile.write(HTML)

    def do_HEAD(self) -> None:
        self._head()

    def log_message(self, *_args) -> None:
        pass


class Server(socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    with Server(("0.0.0.0", PORT), Handler) as srv:
        srv.serve_forever()
