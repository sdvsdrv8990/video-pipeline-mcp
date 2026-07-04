# Q&A: ядро сервера (core/)

> **Роль:** общие движки: contracts, engine, state, reactions, ids, transport, providers.
> **Сквозное:** [G2](../global.md#g2-единый-конверт-ответа-toolresult), [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст), [G13](../global.md#g13-контракты-вынесены-для-разрыва-цикла-импорта), [G15](../global.md#g15-строковые-словари-без-единого-реестра-дрейфят-literalреестр--лекарство).
> **Статус кода:** реализован (кроме providers — заглушки).
> **Навигация:** `core/` → contracts/ (модели) → engine/ (движок) → state/ (данные) → reactions/ (ошибки) → ids/ (идентификаторы) → transport/ (туннель) → providers/ (бэкенды).

## Решение 1: Структура core/
**Q:** как организовано ядро?
**A:** 7 подмодулей, каждый с чёткой ответственностью:
```
core/
├── contracts/      — Pydantic: ToolResult, ErrorDetail, Recovery, Fact, TaskStatus
├── engine/         — GENERIC исполнитель деклараций (ops + templates)
├── state/          — read.json / write.json / _SESSION_LOG / project_memory.md
├── reactions/      — читалка server_reactions → сборка ErrorDetail
├── ids/            — генерация ID (Genesis) + реестр связей
├── transport/      — JSON-RPC транспорт + туннель к Claude
└── providers/      — адаптеры бэкендов (stt/tts/img/ffmpeg)
```
**Связь:** SESSIONS.md §Сессия 2.

## Решение 2: Контракты вынесены для разрыва цикла импорта
**Q:** как связать модули без циклического импорта?
**A:** общие модели данных в лист-модуле `contracts`, который сам ничего из своего пакета не импортирует. Два уровня:
- `core/contracts/*` — Pydantic v2 (ToolResult/ErrorDetail/Fact/TaskStatus) — на провод MCP
- `core/firewall/contracts.py` — stdlib `@dataclass` (FirewallDecision/Request/Result) — внутренние DTO
**Регрессия:** если contracts-модуль начнёт импортировать из своего пакета — цикл вернётся.
**Связь:** [G13](../global.md#g13-контракты-вынесены-для-разрыва-цикла-импорта), [G6](../global.md#g6-порядок-кода-в-файле--граф-зависимостей-снизу-вверх).

## Решение 3: engine — generic-движок деклараций
**Q:** как один движок обслуживает разные категории инструментов?
**A:** читает `config/ops/*.ops.yaml` + `config/templates/`. Один движок на категорию; различия — в конфигах. Валидация `input_schema` через `jsonschema.validate` ДО вызова хендлера (D5 закрыт). Ошибки через реестр реакций `_error()` (D4 частично закрыт).
**Регрессия:** `additionalProperties: false` в схемах — лишний ключ → `VALIDATION_ERROR`, а не `INTERNAL_ERROR` (D26).
**Связь:** [files/core_engine_engine.md](core_engine_engine.md), D5, D4, D26.

## Решение 4: state — управление состоянием
**Q:** где хранятся данные и как они защищены?
**A:**
- `read.json` — снапшот для Claude (сжатый контекст)
- `write.json` — очередь pending_updates
- `_SESSION_LOG` — хронология событий (факты туда НЕ проводятся — D24)
- `project_memory.md` — решения и последствия
**Атомарность (D9 закрыт):** `_atomic_write_json` — temp-файл + fsync + os.replace. Конкурентная запись: `threading.Lock` (один процесс).
**Traversal (D29 открыт):** методы делают `workspace_path / entity_path` БЕЗ safe-join → `read_snapshot("../…")` читает вне workspace.
**Регрессия:** при мультипроцессе нужен `filelock` (кросс-процессная блокировка).
**Связь:** [files/core_state_state_manager.md](core_state_state_manager.md), D9, D24, D29, [G17](../global.md#g17-containment-workspace--единая-точка-а-не-проверка-в-каждом-хендлере).

## Решение 5: reactions — чтение серверных реакций
**Q:** как код ошибки превращается в recovery для Claude?
**A:** читает `config/server_reactions.yaml`, собирает `ErrorDetail{code, message, recovery}`. 5 классов: ai_recoverable, server_recoverable, human_required, integrity, unknown. 13 кодов.
**Дефект D4:** движок ходит через реестр, НО fs_*/table_* хендлеры инлайнят коды (PATH_NOT_FOUND не в yaml). Код `code: str` не ограничен реестром.
**Дефект D27:** `class` загружается из yaml, но ErrorDetail не имеет поля → не доходит до Claude.
**Связь:** [files/core_reactions_reactions.md](core_reactions_reactions.md), D4, D27, [G15](../global.md#g15-строковые-словари-без-единого-реестра-дрейфят-literalреестр--лекарство).

## Решение 6: ids — Genesis
**Q:** кто генерирует ID?
**A:** сервер (`IDGenerator`), формат `PREFIX_uuid4().hex` (32 hex, 122 бита). Формат: `is_valid_format` проверяет длину и hex-символы.
**Дефект D28:** IDGenerator создаётся, но ни один хендлер не зовёт `.generate()`. `id_registry` не существует.
**Связь:** [files/core_ids_id_generator.md](core_ids_id_generator.md), D9 (закрыт), D28, [G10](../global.md#g10-id-генерирует-сервер-не-claude).

## Решение 7: transport — JSON-RPC 2.0 + туннель
**Q:** как Claude подключается к серверу?
**A:** JSON-RPC 2.0 на aiohttp. Нотификации → HTTP 202. `initialize` согласует версию. Туннель: cloudflared (G11). Bind 127.0.0.1 (D12).
**Дефект D30:** transport роняет `facts` и структурный `error` мимо MCP-content.
**Дефект D13 закрыт:** нотификации корректно обрабатываются.
**Связь:** [files/core_transport_transport.md](core_transport_transport.md), [files/core_transport_tunnel.md](core_transport_tunnel.md), D30, D13, D12.

## Решение 8: providers — адаптеры бэкендов
**Q:** как сервер работает с внешними сервисами?
**A:** 4 адаптера: stable-ts (локальный STT), LiteLLM (TTS/IMG), cloudflared (FFmpeg через внешний MCP). Все через `_map_error` → коды из `server_reactions.yaml`.
**Чистая сторона D4:** провайдеры используют registry-коды (PROVIDER_FAILED, CONTENT_REJECTED, LOCAL_INFERENCE_FAILED).
**Статус:** заглушки с `NotImplementedError` (честная незавершённость, G16).
**Связь:** SESSIONS.md §Приложение В, [G16](../global.md#g16-незавершённость-должна-кричать-честная-заглушка-а-не-молчать).

## Открытые вопросы файла
- **D24 (🟡):** facts → _SESSION_LOG не проведён (log_event имеет 0 вызовов).
- **D22 (🟡):** status↔error/result не форсится ни в ToolResult, ни в TaskStatus.
- **D26 (🟡):** Лишние params → INTERNAL_ERROR вместо VALIDATION_ERROR.
- **D28 (⚪):** IDGenerator не задействован, id_registry не существует.

## Что улучшить
- Вынести `_safe_resolve` из server.py в `core/paths.py` (единая точка containment).
- Провести fs_*/table_* хендлеры через `Engine._error` (миграция кодов в реестр).
- Добавить `reaction_class` в ErrorDetail и прокинуть class из yaml.
- Подключить facts → _SESSION_LOG или убрать обещание из доков.
