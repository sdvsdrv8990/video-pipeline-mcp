"""
tests/harness/live_server.py — настоящий сервер под тестом: свой порт, своя рабочая область.

## Назначение
Поведенческий тест бьёт по живому серверу, а не по собранному в памяти движку: только так
проверяются транспорт, auth, файрвол и то, что сервер СООБЩАЕТ в консоль.

## Границы
- Рабочая область — свежий временный каталог (`MCP_WORKSPACE`): иначе набор правил бы
  настоящие книги канала владельца, и «зелёный тест» стоил бы данных.
- Сервер поднимается С аутентификацией, ключ идёт через `MCP_AUTH_TOKEN`, `.env` проекта не
  трогается. Калитка `MCP_ALLOW_NO_AUTH` намеренно НЕ используется — иначе слой auth,
  который и надо проверить, остался бы непроверенным.
"""

import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .console import Console
from .jsonrpc import JsonRpc

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    """Порт, свободный ПРЯМО СЕЙЧАС. Фиксированный номер ронял бы набор при живом сервере рядом."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LiveServerError(RuntimeError):
    """Сервер не поднялся. С хвостом его консоли: причина всегда там, а не в нашей догадке."""


class LiveServer:
    """Живой `server.py` на своём порту. Контекстный менеджер — teardown гарантирован."""

    def __init__(self, env: dict | None = None, ready_timeout: float = 60.0, echo: bool = False):
        self.port = free_port()
        self.token = secrets.token_urlsafe(24)
        self.workspace = Path(tempfile.mkdtemp(prefix="live_ws_")) / "workspace"
        self.workspace.mkdir(parents=True)
        self.extra_env = env or {}
        self.ready_timeout = ready_timeout
        self.echo = echo
        self.proc: subprocess.Popen | None = None
        self.console: Console | None = None
        self.rpc = JsonRpc(self.url, self.token)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def _env(self) -> dict:
        return {**os.environ,
                "MCP_HOST": "127.0.0.1", "MCP_PORT": str(self.port),
                "MCP_WORKSPACE": str(self.workspace),
                # Ключ через среду: `ensure_digest` берёт его первым и .env проекта не трогает.
                "MCP_AUTH_TOKEN": self.token,
                "PYTHONUNBUFFERED": "1",
                **self.extra_env}

    # ═══ Жизненный цикл ═══

    def start(self) -> "LiveServer":
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), "--port", str(self.port)],
            cwd=str(ROOT), env=self._env(), text=True, bufsize=1,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.console = Console(self.proc.stdout, echo=self.echo)
        self._await_ready()
        return self

    def _await_ready(self) -> None:
        """Готовность = сервер ОТВЕТИЛ на `tools/list`, а не «прошло N секунд» и не строка в логе.

        Строка в логе печатается до того, как поднят слушатель; таймер же врёт в обе стороны.
        Отвечающий протокол — единственный честный признак.
        """
        deadline = time.time() + self.ready_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise LiveServerError(
                    f"server.py завершился при старте (код {self.proc.returncode}).\n"
                    f"{self.console.tail()}")
            try:
                if self.rpc.tools_list():
                    return
            except Exception:  # порт ещё не слушает
                pass
            time.sleep(0.1)
        self.stop()
        raise LiveServerError(f"Сервер не ответил за {self.ready_timeout} с.\n{self.console.tail()}")

    def stop(self) -> None:
        """Остановка и уборка. Гарантированная: временная область не должна пережить набор."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        shutil.rmtree(self.workspace.parent, ignore_errors=True)

    def __enter__(self) -> "LiveServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ═══ Удобства набора ═══

    def write(self, rel: str, content: str) -> Path:
        """Положить файл в рабочую область СЕРВЕРА — минуя инструменты (подготовка сцены)."""
        path = self.workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


def live_server(**kw) -> LiveServer:
    """`with live_server() as srv:` — сервер поднят, порт свой, область своя, ключ настоящий."""
    return LiveServer(**kw)
