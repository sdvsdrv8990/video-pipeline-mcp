# Q&A: core/transport/transport.py

> **Роль:** JSON-RPC 2.0 транспорт — роутит `tools/list`/`tools/call`/`initialize`/`ping`, конвертирует `ToolResult` в MCP-ответ, различает нотификации.
> **Сквозное:** [G7](../global.md#g7-транспорт--json-rpc-20-на-aiohttp) (осн. — JSON-RPC 2.0); [G2](../global.md#g2-единый-конверт-ответа-toolresult) (`ToolResult` → провод).
> **Статус кода:** реализован. **D13 (lifecycle/версия) закрыт** (доказано). **D12 (Streamable-HTTP) частичен** (форма ок; SSE/`Mcp-Session-Id` на офф. SDK). **parity-разрыв D30** — `facts`/структурный `error` кладутся вне MCP-`content`. Мёртвые `self.firewall`/`self._request_id`.
> **Навигация (знать не читая):** `core/transport/transport.py`. Поверхность: `Transport(engine,firewall)` — `handle_request(raw)->str|None`, приватные `_handle_tools_list/call`, `_handle_initialize`, `_success/_error_response`. `SUPPORTED_PROTOCOL_VERSIONS=(2025-06-18,2025-03-26,2024-11-05)`. Вызывается `server.py:390`; firewall прогоняется В `server.py` ДО транспорта (тут `self.firewall` не используется).
> **Аудит-линзы:** mcp-developer (осн. — протокол/маппинг), security-reviewer (утечка в error-ветке). Находки доказаны запуском на `.venv`.

## Решение 1: JSON-RPC 2.0 роутер + различение нотификаций (D13 lifecycle)
**Q:** как принимать запросы Claude и не отвечать на нотификации?
**A:** роутер по `method` (`tools/list`/`tools/call`/`initialize`/`ping`); нотификация (нет `id` или `notifications/*`) → `handle_request` возвращает `None`, HTTP-слой отдаёт 202 ([D13]). JSON-RPC 2.0 — стандарт MCP ([G7]).
**Alt:** кастомный протокол — несовместим; отвечать на нотификации JSON-ответом — нарушение JSON-RPC/MCP (баг до D13).
**Регрессия / доказано:** `notifications/initialized` → `None` (→202); неизвестный метод → `-32601`. **Все вызывающие обязаны трактовать `None` как 202** (учтено в server.py).
**Связь:** [G7](../global.md#g7-транспорт--json-rpc-20-на-aiohttp), [D13 закрыт](../AUDIT.md).

## Решение 2: `ToolResult` → MCP-ответ — parity-разрыв (D30, доказано)
**Q:** как внутренний `ToolResult{status,data,error,facts}` лечь в MCP `CallToolResult`?
**A (факт):** `_handle_tools_call` (transport.py:135-149): success → `{"content":[text(json(data))], "facts":[…]}`; error → `{"content":[text(error.message)], "isError":True, "error":{…}}`.
**Регрессия / доказано запуском:** MCP `CallToolResult` знает поля `content`/`isError`/`structuredContent`/`_meta`. А транспорт кладёт:
- success: ключи `['content','facts']` — **`facts` нестандартный**, в `content` их НЕТ → спек-клиент их роняет (Claude не видит структурные факты; корень [D24]).
- error: ключи `['content','isError','error']` — **`error` нестандартный**; спек-клиент видит лишь `content[0].text="boom"`, а `error.code=PROVIDER_FAILED`/recovery/`class`/`raw_response` — в дропаемом ключе → структурная ошибка роняется (корни [D20]/[D27]). Плюс секрет `raw_response` ([D23]) сидит там же.
**Почему важно:** это ТОЧКА СХОДА нескольких находок — внутренний `ToolResult` богаче того, что реально доходит до спек-совместимого Claude. Обещания «facts=память», «код→класс поведения» умирают на этой границе.
**Как чинить (blast-radius — только `_handle_tools_call`):** `facts` → `structuredContent` (валидное MCP-поле) или в `content`; структурный `error` → `structuredContent`/`_meta`; для ошибки — человекочитаемый текст в `content` + `isError`, структура в `structuredContent`. Один фикс закрывает невидимость facts (D24) и кода/класса (D20/D27). **G# кандидат (S2/S3):** «внутренний ToolResult ≠ то, что транспорт кладёт в CallToolResult».
**Связь:** [D30](../AUDIT.md#-d30), [D22](../AUDIT.md#-d22), [core_contracts_tool_result.md · Решение 4], [D20]/[D24]/[D27].

## Решение 3: `initialize` согласует версию протокола (D13 закрыт)
**Q:** как не захардкодить версию MCP?
**A:** `SUPPORTED_PROTOCOL_VERSIONS` — если клиент просит поддержанную, эхо её; иначе новейшая (`2025-06-18`).
**Регрессия / доказано:** `initialize{2025-06-18}` → `2025-06-18`; `{1999}` → fallback `2025-06-18`. Раньше хардкод `2024-11-05`.
**Связь:** [D13 закрыт](../AUDIT.md); история v2.0.

## Решение 4: транспорт доверяет контракту; мёртвые атрибуты
**Q:** что транспорт делает при неверном типе ответа хендлера?
**A:** доверяет, что engine вернёт `ToolResult` (урок v1.1: хендлеры возвращали `dict` → `AttributeError`). Ошибки бизнес-логики — не его забота (изоляция).
**Регрессия / находка (доказано):** `self.firewall` и `self._request_id` **не используются** (firewall прогоняется в server.py; id берётся из запроса) — мёртвые атрибуты (семейство dead-inject [D28]).
**Связь:** [D28](../AUDIT.md#-d28) (мёртвая проводка), история v1.1.

## Открытые вопросы файла
- **🟡 D30 (parity-разрыв):** `facts`/структурный `error` вне MCP-`content` → спек-клиент роняет (доказано). Точка схода [D20]/[D24]/[D27]/[D22]. См. [../AUDIT.md#-d30](../AUDIT.md#-d30).
- **🟡 D12 (Streamable-HTTP частичен):** POST JSON-RPC ок, но нет GET/SSE, `Mcp-Session-Id` — миграция на офф. `mcp`-SDK (история). См. [D12](../AUDIT.md#-d12).
- **⚪ D23-хвост:** `raw_response` с секретом попадает в error-ветку маппинга (в дропаемый ключ, но виден не-спек-клиенту). Общий фикс — редакция в `reactions.get_error` (D23).
- **⚪ мёртвые `self.firewall`/`self._request_id`** — убрать или задействовать.

## Что улучшить (регрессия-тесты, линза test-master)
- Тест D30 (после фикса): `tools/call` success → `facts` доступны в `structuredContent`/`content` (не в дропаемом ключе); error → структурный `code`/`recovery`/`class` в `structuredContent`, `isError=true`.
- Тест D13 (страж): `notifications/initialized`→`None`; `initialize` эхо поддержанной версии, fallback на новейшую; неизвестный метод→`-32601`.
- Тест изоляции: хендлер, вернувший не-`ToolResult`, → контролируемая ошибка транспорта, не сырой `AttributeError` (усиление урока v1.1).
