#!/usr/bin/env python3
"""scripts/guards/_symbol_graph.py — вторая улика о радиусе: статический граф вызовов.

Не сторож: ничего не судит и кода возврата не даёт. Отвечает на вопрос, которого прогон не слышит,
— КТО зовёт тронутое место, если самого вызывающего не исполняет ни один сценарий.

Граф разрешает не все вызовы, поэтому ответ — НИЖНЯЯ граница: он вправе расширить подозрение и не
вправе заверить, что правка безопасна. Улика внешняя и необязательная: нет сервера, нет графа, нет
сети — улики нет, и это не то же самое, что «вызывающих нет».
"""
import json
import os
import re
import shlex
import subprocess
from itertools import count
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Команда — политика, а не константа кода: сервер ставится в Claude, а не в дерево проекта.
COMMAND = shlex.split(os.environ.get("VPM_SYMBOL_GRAPH_CMD", "npx -y socraticode"))
ABSENT = ("No symbol graph found", "No code graph found", "not indexed")
# Улика есть, только если ответ ОБЪЯВИЛ символ и место его определения. Перечислять формулировки
# отказа нельзя: сменит источник текст — и «не знаю» опять поедет как «никто не зовёт».
ОБЪЯВЛЕН = re.compile(r"^Symbol:\s*(\S+)", re.M)
ГДЕ_ОПРЕДЕЛЁН = re.compile(r"^Defined:\s*(\S+?):", re.M)


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


def объявление(answer: str) -> tuple[str, str] | None:
    """Символ и файл, которые ответ ОБЪЯВИЛ о себе; `None` — не объявил ничего, улики нет.

    Источник знает только короткие имена, а коротких однофамильцев в дереве много —
    поэтому объявленное место — единственный способ понять, о ТОМ ЛИ символе пришёл ответ.
    """
    символ, где = ОБЪЯВЛЕН.search(answer), ГДЕ_ОПРЕДЕЛЁН.search(answer)
    return (символ.group(1), где.group(1)) if символ and где else None


def _sites(answer: str) -> list[str]:
    """Точки вызова из ответа: строки вида `  ← файл:строка`."""
    return [row.split("←", 1)[1].strip() for row in answer.splitlines() if "←" in row]


def callers(names, timeout: float = 120.0, ask=None, где=None) -> dict[str, list[str]] | None:
    """Имя символа → точки вызова `файл:строка`. Три ответа, и они РАЗНЫЕ:

    `None` — графа нет вовсе; имя ЕСТЬ в ответе — граф объявил ЭТОТ символ, список это его
    вызывающие (нижняя граница); имя ОТСУТСТВУЕТ — улики нет: символ не объявлен, формулировка
    чужая либо объявлен однофамилец из другого файла.

    Третий прежде сливался со вторым: непонятое имя давало пустой список, и читатель печатал по нему
    проверенную тишину. Однофамилец давал улику ЛОЖНУЮ, что хуже молчания.

    `где` — имя → файл, где его тронули; `ask` — точка внедрения для набора.
    """
    names = list(dict.fromkeys(names))
    if not names:
        return {}                             # не спрашивали — это не «спросили и не знаем»
    if ask is None:
        try:
            server = _Server(timeout)
        except (OSError, FileNotFoundError):
            return None
        ask, close = (lambda name: server.ask("codebase_symbol", {"projectPath": ROOT, "name": name}),
                      server.close)
    else:
        close = lambda: None                  # noqa: E731 — внедрённый источник закрывать нечем
    try:
        out: dict[str, list[str]] = {}
        for name in names:
            answer = ask(name)
            if any(marker in answer for marker in ABSENT):
                return None
            сказано = объявление(answer)
            if сказано is None:
                continue                      # ответ ничего не объявил — улики нет, а не пустота
            if где and где.get(name) and сказано[1] != где[name]:
                continue                      # объявлен однофамилец из другого файла — улика ложная
            out[name] = _sites(answer)
        return out
    except OSError:
        return None
    finally:
        close()
