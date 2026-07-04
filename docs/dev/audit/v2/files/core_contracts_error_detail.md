# Q&A: core/contracts/error_detail.py

> **Роль:** конверт ошибки для Claude — `ErrorDetail{code,message,recovery,raw_response}` + вспомогательная `Recovery{suggested_tool,suggested_params,reason}`. Живёт в `ToolResult.error` и `TaskStatus.error`.
> **Сквозное:** [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст) (осн. — ловим+кодируем, отдаём полный текст); [G6](../global.md#g6-порядок-кода-в-файле--граф-зависимостей-снизу-вверх) (Recovery→ErrorDetail = граф зависимостей); [G1](../global.md#g1-роль-сервера-исполнительвалидатор-оркестратор--снаружи) (сервер не чинит, отдаёт причину Claude).
> **Статус кода:** реализован (Pydantic v2). Механически исправен; реестр реакций подключён к движку (D4 частично), но `code` контрактом не привязан к реестру → дрейф; `raw_response`/`message` уходят без редактирования (D23).
> **Навигация (знать не читая):** `core/contracts/error_detail.py`. Поверхность: `Recovery(BaseModel)` (`suggested_tool:str|None`, `suggested_params:dict|None`, `reason:str=""`) → `ErrorDetail(BaseModel)` (`code:str`, `message:str`, `recovery:Recovery` [обяз.], `raw_response:dict|None=None`). Производители: `core/reactions/reactions.py:get_error` (из yaml), `core/engine/engine.py:_error`, прямые хендлеры `server.py`. Реестр — `config/server_reactions.yaml`.
> **Аудит-линзы:** mcp-developer (осн. — контракт/проводка реестра), security-reviewer (утечка `raw_response`/`message`), test-master. Находки доказаны запуском на `.venv`.

## Решение 1: `Recovery` определён ПЕРЕД `ErrorDetail` (G6)
**Q:** в каком порядке разместить две модели в файле?
**A:** `Recovery` — вспомогательная, `ErrorDetail` её использует → сначала используемое, потом использующее ([G6]). Файл читается как история: «что такое Recovery» → «как встраивается в ErrorDetail».
**Alt:** `ErrorDetail` первым — отброшено: forward-reference, хуже читаемость (в контрактах — риск порядка импорта).
**Регрессия:** перестановка ломает нить зависимостей; тот же принцип — в `firewall/contracts.py` ([G13]).
**Связь:** [G6](../global.md#g6-порядок-кода-в-файле--граф-зависимостей-снизу-вверх).

## Решение 2: четыре сведения об ошибке — `code` + полный `message` + `recovery` + `raw_response` (G5)
**Q:** сколько информации об ошибке давать Claude?
**A:** машинный `code` (класс реакции из реестра) + **полный** текст `message` (провайдера/сервера) + `recovery` (что делать) + `raw_response` (оригинал API для анализа паттернов). Полный текст нужен, чтобы Claude отличил модерацию от таймаута от битого файла ([G5]); сервер не чинит сам ([G1]).
**Alt:** только `code` — отброшено: теряется причина, Claude гадает.
**Регрессия:** смена формата `code` → ломает маппинг реестра; урезание `message` → Claude теряет причину.
**Связь:** [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст); потребитель — [core_contracts_tool_result.md · Решение 1] (`ToolResult.error`).

## Решение 3: `recovery` обязателен — «у ошибки всегда есть след для Claude»
**Q:** делать ли recovery опциональным?
**A:** нет — `recovery: Recovery` обязателен: каждая ошибка обязана нести подсказку следующего шага (retry/смена/человек). Реестр заполняет её из `server_reactions.yaml`.
**Alt:** `recovery: Recovery|None` — отброшено: разрешило бы «немую» ошибку без плана действий.
**Регрессия / находка:** гарантия **поверхностна** — `Recovery` со всеми `None`+`reason=""` валиден (доказано: `ErrorDetail(...,recovery=Recovery())` проходит, `recovery.model_dump()` весь пустой). Обязателен объект, но не его содержательность → «обязательный, но пустой» recovery обходит замысел. Улучшение: требовать непустой `reason` (`min_length=1`) на уровне модели или через реестр.
**Связь:** реестр — `config/server_reactions.yaml` (каждая запись несёт `recovery.reason`).

## Решение 4: `code` — свободная строка, источник истины в yaml; движок собирает через реестр (D4)
**Q:** откуда берётся `code` и его `message_template`/`recovery`/`class`?
**A:** замысел — из `config/server_reactions.yaml` (добавить реакцию = строка в yaml, не код). `Reactions.get_error(code)` по коду достаёт шаблон и собирает `ErrorDetail`; `Engine._error` идёт через реестр, если код в нём есть (иначе fallback). **D4 частично закрыт:** движок подключён (`server.py:99` грузит `Reactions`, `engine.py:_error` роутит через `get_reaction`).
**Alt:** хардкодить тексты/recovery в хендлерах — отброшено (замысел D4): рассинхрон, дубли.
**Регрессия / находки (доказано запуском):**
- **`code: str` НЕ привязан к реестру** — принят `code="TOTALLY_MADE_UP_CODE_123"`. Контракт ничего не форсит → тихий дрейф.
- **Дрейф в проде:** `server.py` fs_*/table_* хендлеры строят `ErrorDetail` **напрямую**, минуя реестр, с кодами `PATH_NOT_FOUND`/`FILE_NOT_FOUND`/`TABLE_NOT_FOUND` — **которых нет в yaml** (там `MISSING_TARGET_FILE`). Для них `class`/`message_template`/`recovery` реестра не применяются никогда.
- **Fallback тоже дрейфит:** `get_error` на неизвестный код эмитит `code="UNKNOWN_ERROR"` — тоже не ключ реестра (там `DEFAULT`).
**Как чинить:** генерировать `Literal`/валидатор `code` из ключей реестра (единый источник истины); провести fs_*/table_* хендлеры через `Engine._error`/реестр; согласовать fallback-код с `DEFAULT`. См. [../AUDIT.md#-d4](../AUDIT.md#-d4).
**Связь:** [core_reactions_reactions.md] (реестр), [core_engine_engine.md] (`_error`), [files/server.md] (прямые хендлеры-дрейф); [D4](../AUDIT.md#-d4).

## Открытые вопросы файла
- **🟡 D4 (реестр частично проведён):** движок — через реестр, но `server.py` fs/table хендлеры минуют его и используют незарегистрированные коды; корень — `code: str` не ограничен реестром. Доказано. См. [../AUDIT.md#-d4](../AUDIT.md#-d4).
- **⚪ D23 (утечка `raw_response`/`message`):** сериализуются дословно без редактирования — `raw_response={"headers":{"authorization":"Bearer …","set-cookie":…}}` и `message` с `sk-live-…` уходят как есть через туннель в Claude AI Web (проверено). Латентно (провайдеры — заглушки), эскалирует до 🟡 при внедрении провайдеров. См. [../AUDIT.md#-d23](../AUDIT.md#-d23).
- **⚪ recovery-required-but-empty:** обязателен объект, но `Recovery()` пустой валиден — гарантия «есть план» поверхностна (Решение 3).

## Что улучшить (регрессия-тесты, линзы test-master / security-reviewer)
- Тест привязки кода: каждый `code`, встречающийся в `server.py`/`engine`/`providers`, ∈ ключей `server_reactions.yaml` (страж от дрейфа D4; сейчас упадёт на `PATH_NOT_FOUND`/`FILE_NOT_FOUND`/`TABLE_NOT_FOUND`/`UNKNOWN_ERROR`).
- Тест редактирования (после фикса D23): `ErrorDetail` с `raw_response`, содержащим `authorization`/`api_key`/`token`/`set-cookie`, → эти ключи замаскированы перед сериализацией.
- Тест recovery-содержательности (после фикса Решения 3): `ErrorDetail` требует непустой `recovery.reason`.
