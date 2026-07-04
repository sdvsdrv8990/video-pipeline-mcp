# Q&A: сервер (точка входа)

> **Роль:** MCP-эндпоинт, конвейер: Origin → parse → Auth → Firewall → Transport. Точка запуска + туннель.
> **Сквозное:** [G3](../global.md#g3-firewall-перед-ядром), [G7](../global.md#g7-транспорт--json-rpc-20-на-aiohttp), [G11](../global.md#g11-поставщик-туннеля--cloudflare-tunnel-cloudflared), [G18](../global.md#g18-за-туннелем-клиент-один--гранулярность-по-ip-бессмысленна-секрет-уязвим).
> **Статус кода:** реализован; v2.1 — статус туннеля, MCP_DEV_MODE; v2.2 — D3 auth закрыт.
> **Навигация:** `server.py` → `core/engine` → `tools/*` + `core/firewall` + `core/transport` + `core/state`.

## Решение 1: JSON-RPC 2.0 на aiohttp
**Q:** какой протокол и сервер под MCP?
**A:** JSON-RPC 2.0 (стандарт MCP), aiohttp (async, минимальный код). Порт через argparse.
**Alt:** FastAPI (лишняя сложность), Flask (синхронный) — отброшены.
**Связь:** [G7](../global.md#g7-транспорт--json-rpc-20-на-aiohttp), SESSIONS.md §Сессия 5.

## Решение 2: Конвейер обработки запроса
**Q:** в каком порядке обрабатываются входящие запросы?
**A:** `handle_jsonrpc` (v2.2):
```
Origin-check → JSON parse → Auth check → Firewall.check(request) → Transport.handle_request → Engine.call
```
**Fail-closed (D10 закрыт):** тело не парсится → HTTP 400; исключение в firewall → HTTP 403 (не пропуск в ядро).
**Auth (D3 закрыт):** bearer-токен `MCP_AUTH_TOKEN` из env; если не задан — auth отключена; HTTP 401 при отсутствии/неверном токене.
**Связь:** [files/core_firewall_firewall.md](core_firewall_firewall.md), [files/core_transport_transport.md](core_transport_transport.md), D10, D3.

## Решение 3: D3 — bearer-аутентификация (ЗАКРЫТО)
**Q:** кто вправе вызывать инструменты на публичном эндпоинте?
**A (фактически):** bearer-токен `MCP_AUTH_TOKEN` из env. Конвейер: `Origin → parse → Auth → Firewall → Transport`.
**Реализация:** `server.py:59-60` (config), `server.py:371-386` (check). Если `MCP_AUTH_TOKEN` не задан — auth отключена (локальная разработка). Constant-time сравнение через `secrets.compare_digest`. HTTP 401 при отсутствии/неверном токене.
**Реестр:** `AUTH_REQUIRED`/`AUTH_FAILED` добавлены в `server_reactions.yaml` (класс `human_required`).
**Связь:** [D3 закрыт](../AUDIT.md#-d3), [D14](../AUDIT.md#-d14), [G18](../global.md#g18-за-туннелем-клиент-один--гранулярность-по-ip-бессмысленна-секрет-уязвим).

## Решение 4: тонкие хендлеры возвращают ToolResult — но коды дрейфят (D4)
**Q:** все ли хендлеры следуют контракту ToolResult?
**A:** да, после v1.1 (dict → ToolResult). НО fs_*/table_* хендлеры строят ErrorDetail **напрямую**, минуя реестр: `PATH_NOT_FOUND`/`FILE_NOT_FOUND`/`TABLE_NOT_FOUND` — отсутствуют в yaml (там `MISSING_TARGET_FILE`).
**Связь:** D4, [G15](../global.md#g15-строковые-словари-без-единого-реестра-дрейфят-literalреестр--лекарство).

## Решение 5: D12 — bind 127.0.0.1 + Origin (обновлён v2)
**Q:** как ограничить доступ к эндпоинту?
**A:** bind 127.0.0.1 по умолчанию (наружу — только туннель). Origin-проверка: off-by-default (пустой ALLOWED_ORIGINS → пропуск); даже включённый обходится опусканием заголовка. **Origin НЕ заменяет аутентификацию (D3).**
**Связь:** [D12](../AUDIT.md#-d12), [D3](../AUDIT.md#-d3).

## Решение 6: v2.1 — статус + production tools
**Q:** что изменилось в v2.1?
**A:**
- 4 production инструмента: `fs_get_directory_tree`, `fs_read_file`, `fs_create_file`, `json_read_snapshot`
- Тестовые инструменты (`test_echo`, `test_error`) удалены
- Вывод статуса после запуска: `ГОТОВ | Туннель: ...`
- Проверка `tunnel.status()` после `start()` — реальное состояние соединения
- 3 режима туннеля: quick/named-token/named-credentials
**Связь:** SESSIONS.md §Приложение Г.

## Открытые вопросы файла
- нет критичных
