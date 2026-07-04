"""
core/transport/transport.py — Транспорт к Claude

## Назначение
Управление туннелем к Claude AI Web. Принимает JSON-RPC запросы и возвращает ответы.

## 4 уровня анализа

### 1. Код
- Transport класс с методами handle_request, send_response
- Принимает JSON-RPC 2.0 запросы
- Возвращает JSON-RPC 2.0 ответы

### 2. Поведение
- Claude подключается через туннель
- Сервер принимает запросы и обрабатывает
- Ответы отправляются обратно

### 3. Поток данных
```
Claude → JSON-RPC request → Transport → Engine → ToolResult → Claude
```

### 4. Долгосрочный (6 мес)
- Транспорт стабилен (редко меняется)
- Добавляются новые методы по мере необходимости
- Логирование всех запросов

## Порядок полей
1. Конфигурация
2. Методы (handle_request, send_response)
"""

import json
from typing import Any, Callable, Awaitable

from core.contracts import ToolResult


class Transport:
    """Транспорт MCP-сервера.

    Принимает JSON-RPC запросы и возвращает ответы.

    Attributes:
        engine: Движок инструментов
        firewall: Файрвол (опционально)
    """

    def __init__(self, engine=None, firewall=None):
        """Инициализация.

        Args:
            engine: Движок инструментов
            firewall: Файрвол (опционально)
        """
        self.engine = engine
        self.firewall = firewall
        self._request_id = 0

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

        # D13: нотификация — сообщение без "id" (или из пространства notifications/).
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

        result = await self.engine.call(name, arguments)

        # D30: конвертируем ToolResult в MCP формат с structuredContent.
        # facts и error details → structuredContent (спек-совместимо).
        if result.status == "success":
            content = [{"type": "text", "text": json.dumps(result.data or {})}]
            structured = {}
            if result.facts:
                structured["facts"] = [f.model_dump() for f in result.facts]
            return self._success_response(request_id, {
                "content": content,
                "structuredContent": structured if structured else None
            })
        else:
            content = [{"type": "text", "text": result.error.message if result.error else "Unknown error"}]
            structured = {}
            if result.error:
                err_dump = result.error.model_dump()
                structured["code"] = err_dump.get("code")
                structured["reaction_class"] = err_dump.get("reaction_class")
                structured["recovery"] = err_dump.get("recovery")
            return self._success_response(request_id, {
                "content": content,
                "isError": True,
                "structuredContent": structured if structured else None
            })

    # D13: версии протокола, которые сервер понимает (по убыванию новизны).
    SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

    def _handle_initialize(self, request_id: Any, params: dict) -> str:
        """Обработка initialize с согласованием версии (D13).

        Args:
            request_id: ID запроса
            params: Параметры инициализации (в т.ч. protocolVersion клиента)

        Returns:
            JSON-RPC ответ с информацией о сервере
        """
        # D13: согласуем версию — если клиент просит поддерживаемую, отвечаем ею;
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
