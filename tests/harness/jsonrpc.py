"""
tests/harness/jsonrpc.py — клиент JSON-RPC 2.0 к живому серверу.

Говорит ровно тем же протоколом, что Claude AI Web: HTTP POST на `/mcp`, ключ в заголовке.

## Где что лежит в ответе (проверено живым сервером)
`content[0].text` — только ДАННЫЕ (при отказе — текст сообщения). Всё остальное наш транспорт
кладёт в `structuredContent`: на успехе — `facts`, на отказе — `code` / `reaction_class` /
`recovery`, а роль `status` играет родное `isError`. То есть паритет на проводе есть,
и клиент обязан читать оба места — иначе тест «не увидел код» означал бы «кода нет».
"""

import itertools
import json

import httpx

_ids = itertools.count(1)


class JsonRpc:
    """Вызовы сервера по протоколу. Без ретраев: тесту нужен первый же ответ, каким бы он ни был."""

    def __init__(self, url: str, token: str = "", timeout: float = 30.0):
        self.url = url
        self.token = token
        self.timeout = timeout

    def headers(self, token: str | None = None) -> dict:
        """Заголовки вызова. `token=''` — намеренно БЕЗ ключа (проверка отказа auth)."""
        value = self.token if token is None else token
        return {"Authorization": f"Bearer {value}"} if value else {}

    def request(self, method: str, params: dict | None = None, token: str | None = None,
                extra_headers: dict | None = None) -> httpx.Response:
        """Сырой ответ: код HTTP тоже предмет проверки (401 на плохой ключ — это не тело)."""
        body = {"jsonrpc": "2.0", "id": f"t{next(_ids)}", "method": method}
        if params is not None:
            body["params"] = params
        return httpx.post(self.url, json=body, timeout=self.timeout,
                          headers={**self.headers(token), **(extra_headers or {})})

    def call_raw(self, method: str, params: dict | None = None, **kw) -> dict:
        """Конверт JSON-RPC целиком. Не JSON в теле — это тоже факт, а не повод падать молча."""
        response = self.request(method, params, **kw)
        try:
            return response.json()
        except ValueError:
            return {"_http_status": response.status_code, "_text": response.text[:500]}

    def tools_list(self, **kw) -> list[dict]:
        return (self.call_raw("tools/list", {}, **kw).get("result") or {}).get("tools") or []

    def call_tool(self, name: str, arguments: dict | None = None, **kw) -> dict:
        """Вызов инструмента: конверт + разобранные данные и код рядом, а не вместо конверта."""
        envelope = self.call_raw("tools/call", {"name": name, "arguments": arguments or {}}, **kw)
        return {"envelope": envelope, "data": self.data(envelope),
                "code": self.error_code(envelope), "is_error": self.is_error(envelope),
                "facts": self.facts(envelope)}

    @staticmethod
    def _result(envelope: dict) -> dict:
        return envelope.get("result") or {}

    @staticmethod
    def structured(envelope: dict) -> dict:
        return JsonRpc._result(envelope).get("structuredContent") or {}

    @staticmethod
    def is_error(envelope: dict) -> bool:
        """Роль `status` на проводе играет родное `isError` — так велит спека MCP."""
        return bool(JsonRpc._result(envelope).get("isError"))

    @staticmethod
    def text(envelope: dict) -> str:
        content = JsonRpc._result(envelope).get("content") or []
        return "".join(str(i.get("text") or "") for i in content if i.get("type") == "text")

    @staticmethod
    def data(envelope: dict) -> dict:
        """Данные из `content[0].text`. При отказе там текст сообщения — тогда пусто."""
        try:
            parsed = json.loads(JsonRpc.text(envelope))
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def error_code(envelope: dict) -> str:
        """Код реакции — по нему тест и ассертит, а не по тексту сообщения."""
        return str(JsonRpc.structured(envelope).get("code") or "")

    @staticmethod
    def facts(envelope: dict) -> list[dict]:
        return list(JsonRpc.structured(envelope).get("facts") or [])
