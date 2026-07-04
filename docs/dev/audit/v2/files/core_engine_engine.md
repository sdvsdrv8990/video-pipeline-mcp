# Q&A: core/engine/engine.py

> **Роль:** декларативный движок — реестр (`register`) и исполнитель (`call`) инструментов. Claude → `tools/list`/`tools/call` → Engine находит инструмент, валидирует params, зовёт хендлер, возвращает `ToolResult`.
> **Сквозное:** [G2](../global.md#g2-единый-конверт-ответа-toolresult) (все инструменты → `ToolResult`); [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст) (ошибки через реестр реакций); [G13](../global.md#g13-контракты-вынесены-для-разрыва-цикла-импорта) (`ToolDefinition` — внутренний `@dataclass`, на провод не выходит).
> **Статус кода:** реализован, тонкий/generic. **D5 закрыт** (валидация схемы до хендлера — доказано). Чистая сторона [D4] (эмитит только registry-валидные коды). Новый [D26] — мисклассификация лишних params в `INTERNAL_ERROR`.
> **Навигация (знать не читая):** `core/engine/engine.py`. Поверхность: `Engine(reactions=None)` — `register(name,description,input_schema,handler)`, `call(name,params)->ToolResult`, `list_tools()`, `get_tool`/`has_tool`, приватные `_error`/`_validate`. Состояние: `tools: dict[name→ToolDefinition]`. `ToolDefinition` — `@dataclass{name,description,input_schema,handler}`.
> **Аудит-линзы:** mcp-developer (осн. — декларативность/контракты/валидация), security-reviewer (валидация входа/утечка), test-master. Находки доказаны запуском на `.venv`.

## Решение 1: generic `register`/`call` — движок не меняется при новых инструментах
**Q:** как добавлять инструменты, не трогая ядро?
**A:** `register(name, schema, handler)` кладёт `ToolDefinition` в `dict`; `call(name, params)` ищет и исполняет. Новый инструмент = запись в реестр + handler, движок неизменен. Декларативность (mcp-конвенция «обёртка→декларация→движок→контракт»).
**Alt:** if/elif по имени инструмента в ядре — отброшено: ядро растёт с каждым инструментом, не масштабируется.
**Регрессия:** `register` молча перезаписывает одноимённый инструмент (`self.tools[name]=…`) — коллизия имён не ловится (реестр внутренний/контролируемый, риск низкий).
**Связь:** [G2](../global.md#g2-единый-конверт-ответа-toolresult); производители — `server.py:register_basic_tools`.

## Решение 2: D5 — валидация params по `input_schema` ДО хендлера (ЗАКРЫТ, доказано)
**Q:** проверяются ли `params` против схемы перед исполнением?
**A:** да — `call()` зовёт `_validate()` до `handler(**params)` (engine.py:127). `_validate` использует `jsonschema.validate`; при `ImportError` — облегчённый fallback (только `required`).
**Alt:** схема только «на витрине» `tools/list` (поведение v1 — декоративная) — отброшено ([D5]): вход не проверялся.
**Регрессия / доказано запуском:** `jsonschema 4.23.0` установлен и закреплён (`requirements.txt:22 jsonschema>=4.0`) → активна СИЛЬНАЯ ветка. Проверено: `{}` (нет required) → `VALIDATION_ERROR`; `{"path":123}` (неверный тип) → `VALIDATION_ERROR`; валидные params → `success`. **НО** fallback без `jsonschema` проверяет только наличие `required`, не типы — тихая деградация, если зависимость выпадет из окружения (сейчас не выпадает — закреплена).
**Связь:** [D5 закрыт](../AUDIT.md); поверхность вызова — [server.md · Открытые вопросы](server.md) (P2 разблокирован: движок форсит схему).

## Решение 3: `_error` через реестр реакций — чистая сторона D4
**Q:** как движок собирает ошибку?
**A:** `_error(code,…)` роутит через `reactions.get_error`, если код есть в реестре, иначе fallback на переданный `ErrorDetail` ([G5]). Движок эмитит `TOOL_NOT_FOUND`/`VALIDATION_ERROR`/`INTERNAL_ERROR` — **все три ∈ `server_reactions.yaml`**.
**Alt:** хардкодить тексты в движке — отброшено ([D4]).
**Регрессия / контраст:** движок — **чистая** сторона [D4]: его коды зарегистрированы. Дрейф ([D4]) живёт в `server.py` fs/table хендлерах, которые строят `ErrorDetail` в обход `_error` кодами вне yaml. Провести их через `Engine._error` = фикс дрейфа.
**Связь:** [D4](../AUDIT.md#-d4), [core_reactions_reactions.md], [core_contracts_error_detail.md · Решение 4].

## Решение 4: `handler(**params)` + отсутствие `additionalProperties` → мисклассификация (D26)
**Q:** как params попадают в хендлер и что при лишнем/неверном ключе?
**A:** `await tool.handler(**params)` — dict раскрывается в kwargs. Схемы не задают `additionalProperties: false` → лишний ключ проходит `jsonschema.validate`, но падает на раскрытии kwargs.
**Регрессия / находка (доказано запуском):** `call("echo", {"path":"x","bogus":9})` → схема ОК → `handler(**params)` → `TypeError: echo() got an unexpected keyword argument 'bogus'` → generic `except` → **`INTERNAL_ERROR`** (класс `human_required`!) с `message="echo() got an unexpected keyword argument 'bogus'"`. Клиентская ошибка параметров подаётся как серверный сбой → Claude эскалирует к человеку вместо исправления params; плюс утечка Python-текста ([G5] допускает, но класс неверный). См. [D26](../AUDIT.md#-d26).
**Alt (фикс):** `additionalProperties:false` в схемах (лишний ключ → `VALIDATION_ERROR` на валидации); либо фильтровать params по сигнатуре; либо ловить `TypeError`→`VALIDATION_ERROR`.
**Связь:** [D26](../AUDIT.md#-d26), [core_contracts_error_detail.md] (класс реакции).

## Открытые вопросы файла
- **✅ D5 (валидация схемы) — закрыт:** движок форсит `input_schema` до хендлера (доказано). Разблокирует server P2.
- **🟡 D26 (мисклассификация лишних params):** лишний/неизвестный ключ → `INTERNAL_ERROR`/`human_required` вместо `VALIDATION_ERROR`/`ai_recoverable` (доказано). См. [../AUDIT.md#-d26](../AUDIT.md#-d26).
- **⚪ fallback `_validate` без jsonschema:** проверяет только `required`, не типы — тихая деградация (зависимость сейчас закреплена, риск латентный).
- **⚪ `register` перезаписывает одноимённый инструмент; `handler(**params)` требует async-хендлер** — sync-хендлер → `TypeError`→`INTERNAL_ERROR` (сейчас все async).

## Что улучшить (регрессия-тесты, линза test-master)
- Тест D5 (страж): `call` с missing-required и с неверным типом → `VALIDATION_ERROR` до хендлера; валидные → `success` (эталон уже проходит).
- Тест D26 (после фикса): лишний ключ → `VALIDATION_ERROR` (`ai_recoverable`), НЕ `INTERNAL_ERROR`.
- Тест D4-чистоты: все коды, эмитируемые `Engine` (`TOOL_NOT_FOUND`/`VALIDATION_ERROR`/`INTERNAL_ERROR`), ∈ ключей `server_reactions.yaml`.
