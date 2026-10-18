"""core/transport/transport.py — разбор JSON-RPC: метод протокола превращается в вызов движка.

Версия протокола СОГЛАСУЕТСЯ при знакомстве, а не берётся своей: иначе клиент другой версии
получал бы отказ там, где разговор возможен.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from core.contracts import ContractError, ToolResult
from core.declaration import Declared
from core.declaration_schemas import TRANSPORT, TransportDeclaration


@cache
def _door(path: Path) -> Declared[TransportDeclaration]:
    return Declared(path, ContractError, "«transport»", schema=TRANSPORT.schema)


def declared_transport(config_path: Path) -> TransportDeclaration:
    """Обещания провода по схеме у двери — одной на каталог деклараций: origins сервера, бюджет ответа транспорта и
    страниц инструментов читают один объект; правка файла — новый объект по mtime, битая — отказ с путём ключа."""
    return _door(Path(config_path) / TRANSPORT.file).model


def answer(result: ToolResult) -> dict[str, Any]:
    """Результат tools/call в форме MCP: `structuredContent` — итог целиком (у успеха — данные и факты типов реестра
    фактов, у отказа — ErrorDetail под `error` один раз и без пустых полей: код, класс, текст, рецепт, факты причины,
    ошибки ввода; EB-11), `content` — его сериализация: клиент, который показывает модели одно из двух, отдаёт тот же
    итог (MCP 2025-06-18)."""
    if result.status == "success":
        whole: dict[str, Any] = {**(result.data or {}),
                                 **({"facts": [f.model_dump() for f in result.facts]} if result.facts else {})}
    elif result.error:
        whole = {"error": _bare(result.error.model_dump())}
    else:
        return {"content": [{"type": "text", "text": "Unknown error"}], "isError": True}
    # Текст — как есть: экранирование \uXXXX клиент видит буквально, и кириллица стоит ему 6 символов.
    payload: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(whole, ensure_ascii=False)}],
                               "structuredContent": whole}
    return payload if result.status == "success" else {**payload, "isError": True}


def _bare(value: Any) -> Any:
    """Отказ без пустот на любой глубине: поле без значения (None, {}, []) клиенту не идёт."""
    if isinstance(value, dict):
        kept = {key: _bare(item) for key, item in value.items()}
        return {key: item for key, item in kept.items() if item is not None and item != {} and item != []}
    if isinstance(value, list):
        return [_bare(item) for item in value]
    return value


class Transport:
    """Транспорт MCP-сервера.

    Принимает JSON-RPC запросы и возвращает ответы.

    Attributes:
        engine: Движок инструментов
        firewall: Файрвол (опционально)
    """

    def __init__(self, engine=None, firewall=None, *, config_path: Path):
        """Инициализация.

        Args:
            engine: Движок инструментов
            firewall: Файрвол (опционально)
            config_path: Каталог деклараций — бюджет ответа клиента (`transport.yaml → answer.chars`)
        """
        self.engine = engine
        self.firewall = firewall
        self.config_path = config_path

    async def handle_request(self, raw_request: str) -> str | None:
        """Обработка JSON-RPC запроса.

        Args:
            raw_request: Сырой JSON-RPC запрос

        Returns:
            JSON-RPC ответ, либо None для нотификаций (вызывающий шлёт HTTP 202).
        """
        try:
            request = json.loads(raw_request)
        except json.JSONDecodeError as e:
            return self._error_response(None, -32700, f"Parse error: {e}")

        jsonrpc = request.get("jsonrpc", "2.0")
        method = request.get("method", "")
        params = request.get("params", {})
        request_id = request.get("id")

        # Нотификация — сообщение без "id" (или из пространства notifications/).
        # На неё НЕЛЬЗЯ отвечать JSON-RPC-ответом (JSON-RPC 2.0 / MCP): возвращаем
        # None, а HTTP-слой отдаёт 202 Accepted без тела.
        is_notification = ("id" not in request) or method.startswith("notifications/")
        if is_notification:
            # notifications/initialized и прочие клиентские нотификации: принять и молчать.
            return None

        # Проверка версии протокола JSON-RPC
        if jsonrpc != "2.0":
            return self._error_response(request_id, -32600, "Invalid Request: version must be 2.0")

        # Обработка методов
        if method == "tools/list":
            return await self._handle_tools_list(request_id)
        elif method == "tools/call":
            return await self._handle_tools_call(request_id, params)
        elif method == "initialize":
            return self._handle_initialize(request_id, params)
        elif method == "ping":
            return self._success_response(request_id, {})
        else:
            return self._error_response(request_id, -32601, f"Method not found: {method}")

    async def _handle_tools_list(self, request_id: Any) -> str:
        """Обработка tools/list.

        Args:
            request_id: ID запроса

        Returns:
            JSON-RPC ответ со списком инструментов (плоский список по MCP протоколу)
        """
        if not self.engine:
            return self._error_response(request_id, -32000, "Engine not initialized")

        tools = self.engine.list_tools()  # Плоский список для MCP протокола
        return self._success_response(request_id, {"tools": tools})

    async def _handle_tools_call(self, request_id: Any, params: dict) -> str:
        """Обработка tools/call.

        Args:
            request_id: ID запроса
            params: Параметры (name, arguments)

        Returns:
            JSON-RPC ответ с результатом
        """
        if not self.engine:
            return self._error_response(request_id, -32000, "Engine not initialized")

        name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Бюджет судится до вызова: битая декларация провода или установки — кодовый отказ, и инструмент не исполнялся.
        try:
            budget = client_budget(self.config_path)
        except ContractError as refused:
            return self._success_response(request_id, answer(self.engine.failure(name, refused)))
        result = await self.engine.call(name, arguments)
        # Ответ больше бюджета клиентов установки не шлётся: клиент обрезал бы или отверг его молча — отказ называет вес.
        if budget.load(result) > 1:
            result = self.engine.failure(name, budget.refused(name, result))
        return self._success_response(request_id, answer(result))

    # Версии протокола, которые сервер понимает (по убыванию новизны).
    SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

    def _handle_initialize(self, request_id: Any, params: dict) -> str:
        """Обработка initialize с согласованием версии.

        Args:
            request_id: ID запроса
            params: Параметры инициализации (в т.ч. protocolVersion клиента)

        Returns:
            JSON-RPC ответ с информацией о сервере
        """
        # Согласуем версию — если клиент просит поддерживаемую, отвечаем ею;
        # иначе отдаём свою новейшую. Раньше версия хардкодилась (устаревшая).
        client_version = (params or {}).get("protocolVersion")
        if client_version in self.SUPPORTED_PROTOCOL_VERSIONS:
            negotiated = client_version
        else:
            negotiated = self.SUPPORTED_PROTOCOL_VERSIONS[0]

        return self._success_response(request_id, {
            "protocolVersion": negotiated,
            "serverInfo": {
                "name": "video-pipeline-mcp",
                "version": "1.0.0"
            },
            "capabilities": {
                "tools": {"listChanged": False}
            }
        })

    def _success_response(self, request_id: Any, result: Any) -> str:
        """Формирование успешного ответа.

        Args:
            request_id: ID запроса
            result: Результат

        Returns:
            JSON-RPC ответ
        """
        return json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result
        }, ensure_ascii=False)

    def _error_response(self, request_id: Any, code: int, message: str) -> str:
        """Формирование ответа с ошибкой.

        Args:
            request_id: ID запроса
            code: Код ошибки JSON-RPC
            message: Сообщение об ошибке

        Returns:
            JSON-RPC ответ с ошибкой
        """
        return json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message
            }
        }, ensure_ascii=False)


def client_budget(config_path: Path) -> "Budget":
    """Бюджет ответа и срок вызова этой установки. Клиенты — по её фактам (install.yaml): клиентский конец туннеля —
    `tunnel`, MCP на этой машине — `loopback`; не подходит ни один — все объявленные. По каждой мере — самый тесный."""
    from core import deploy  # здесь, а не сверху: файл установки ищется в том же каталоге деклараций, что и transport.yaml
    mcp, end = deploy.install(Path(config_path) / "install.yaml")
    ways = ({"tunnel"} if end in ("client", "both") else set()) | ({"loopback"} if mcp else set())
    declared = list(declared_transport(config_path).clients.values())
    clients = [c for c in declared if c.reached in ways] or declared
    return Budget(chars=min((c.answer_chars for c in clients if c.answer_chars), default=None),
                  bytes=min((c.answer_bytes for c in clients if c.answer_bytes), default=None),
                  call_sec=min((c.call_sec or 0 for c in clients), default=0),
                  error_chars=min((c.error_chars for c in clients if c.error_chars), default=None))


@dataclass(frozen=True)
class Budget:
    """Бюджет ответа установки — самый тесный из её клиентов по каждой мере: знаки (`chars`) и байты UTF-8 тела
    (`bytes`); None — мера не ограничена. `call_sec` — срок вызова самого тесного клиента; клиент без объявленного срока —
    0: ожидания нет. `error_chars` — сколько знаков текста отказа клиент принимает целым (None — не режет)."""

    chars: int | None
    bytes: int | None
    call_sec: int
    error_chars: int | None = None

    def load(self, result: ToolResult) -> float:
        """Доля бюджета, которую занимает ответ так, как его шлёт провод; больше 1 — не влезет."""
        text = json.dumps(answer(result), ensure_ascii=False)
        return max(len(text) / self.chars if self.chars else 0.0,
                   len(text.encode("utf-8")) / self.bytes if self.bytes else 0.0)

    def refused(self, tool: str, result: ToolResult) -> ContractError:
        """Отказ RESULT_TOO_LARGE: сколько ответ весит в каждой мере и какую долю бюджета занял бы."""
        text = json.dumps(answer(result), ensure_ascii=False)
        return ContractError("RESULT_TOO_LARGE", facts={
            "tool": tool, "chars": len(text), "bytes": len(text.encode("utf-8")), "load": round(100 * self.load(result))})
