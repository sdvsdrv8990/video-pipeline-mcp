#!/usr/bin/env python3
"""scripts/guards/_symbol_graph.py — вторая улика о радиусе: статический граф вызовов (SocratiCode).

Не сторож: ничего не судит и exit-кода не даёт. Отвечает на вопрос, которого прогон не слышит, —
КТО зовёт тронутое место, если самого вызывающего не исполняет ни один сценарий.

Граф разрешает не все вызовы (замер на нашем дереве: 56.9% рёбер неразрешены), поэтому ответ —
НИЖНЯЯ граница: он вправе расширить подозрение и не вправе заверить, что правка безопасна.

Улика внешняя и необязательная: нет сервера, нет графа, нет сети — возвращается `None`
(«улики нет»), и это не то же самое, что «вызывающих нет».
"""
import json
import os
import shlex
import subprocess
from itertools import count
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Команда — политика, а не константа кода: сервер ставится в Claude, а не в дерево проекта.
COMMAND = shlex.split(os.environ.get("VPM_SYMBOL_GRAPH_CMD", "npx -y socraticode"))
ABSENT = ("No symbol graph found", "No code graph found", "not indexed")


class _Server:
    """Один процесс на пачку вопросов: подъём стоит секунды, вопрос — миллисекунды."""

    def __init__(self, timeout: float):
        self.proc = subprocess.Popen(COMMAND, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1)
        # Каналы `Popen` с PIPE даёт всегда, но по типу они `IO | None`: без проверки
        # отсутствие канала обвалилось бы немым `AttributeError` посреди диалога.
        if self.proc.stdin is None or self.proc.stdout is None or self.proc.stderr is None:
            raise OSError("процесс поднялся без каналов — спросить его нечем")
        self.вход, self.выход, self.ошибки = self.proc.stdin, self.proc.stdout, self.proc.stderr
        self.ids = count(1)
        self.timeout = timeout
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "vpm-guards", "version": "1"}})
        self._rpc("notifications/initialized", {}, notify=True)

    def _rpc(self, method: str, params: dict, notify: bool = False) -> dict:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            body["id"] = next(self.ids)
        self.вход.write(json.dumps(body) + "\n")
        self.вход.flush()
        if notify:
            return {}
        while True:
            line = self.выход.readline()
            if not line:
                raise OSError("сервер закрыл вывод: " + (self.ошибки.read() or "")[-500:])
            try:
                message = json.loads(line)
            except ValueError:
                continue                      # сервер пишет в stdout и не-JSON строки прогресса
            if "id" in message:
                return message

    def ask(self, tool: str, arguments: dict) -> str:
        answer = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        content = (answer.get("result") or {}).get("content") or []
        return "".join(part.get("text", "") for part in content) or json.dumps(answer)[:300]

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _sites(answer: str) -> list[str]:
    """Точки вызова из ответа: строки вида `  ← файл:строка`."""
    return [row.split("←", 1)[1].strip() for row in answer.splitlines() if "←" in row]


def callers(names, timeout: float = 120.0) -> dict[str, list[str]] | None:
    """Имя символа → точки вызова `файл:строка`. `None` — улики нет (сервера или графа)."""
    names = list(dict.fromkeys(names))
    if not names:
        return {}
    try:
        server = _Server(timeout)
    except (OSError, FileNotFoundError):
        return None
    try:
        out: dict[str, list[str]] = {}
        for name in names:
            answer = server.ask("codebase_symbol", {"projectPath": ROOT, "name": name})
            if any(marker in answer for marker in ABSENT):
                return None
            out[name] = _sites(answer)
        return out
    except OSError:
        return None
    finally:
        server.close()
