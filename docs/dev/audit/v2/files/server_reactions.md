# Q&A: реестр реакций сервера

> **Роль:** единый реестр ошибок: код → класс → recovery. Добавить реакцию = строка в yaml, не код.
> **Сквозное:** [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст), [G15](../global.md#g15-строковые-словари-без-единого-реестра-дрейфят-literalреестр--лекарство).
> **Статус кода:** реализован; движок подключён; fs_/table_ хендлеры минуют.
> **Навигация:** `config/server_reactions.yaml` → `core/reactions/reactions.py` → `ErrorDetail`.

## Решение 1: YAML как единый источник истины
**Q:** где определены все коды ошибок?
**A:** `config/server_reactions.yaml`. Движок при любом исходе смотрит сюда по `code` и собирает `ErrorDetail{code, message, recovery}`. Добавить реакцию = строка в yaml, не код.
**Alt:** хардкод ошибок в хендлерах — отброшен (рассинхрон, дрейф).
**Связь:** [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст), SESSIONS.md §Сессия 2.

## Решение 2: 5 классов реакций
**Q:** какие типы поведения при ошибках?
**A:**
| Класс | Описание | Что делает Claude |
|---|---|---|
| `ai_recoverable` | Claude исправляет сам | Переформулировать / создать файл |
| `server_recoverable` | Сервер справляется | Retry / fallback / polling |
| `human_required` | Нужен человек | Эскалация |
| `integrity` | Нарушение связей | Фоновая сверка |
| `unknown` | Нет протокола | Человек |
**Связь:** [files/core_contracts_error_detail.md](core_contracts_error_detail.md), SESSIONS.md §Приложение Б.

## Решение 3: 13 кодов реакций
**Q:** какие конкретные ошибки обрабатываются?
**A:**
| Код | Класс | Recovery |
|---|---|---|
| `MISSING_TARGET_FILE` | ai_recoverable | suggested_tool: fs_create_file |
| `EMPTY_SNAPSHOT` | ai_recoverable | suggested_tool: fs_get_directory_tree |
| `MISSING_OPTIONAL_DATA` | ai_recoverable | выбор за Claude |
| `STRUCTURE_INCOMPLETE` | ai_recoverable | suggested_tool: fs_create_project_structure |
| `FRAGMENT_MISPLACED` | ai_recoverable | suggested_tool: fs_move |
| `PROVIDER_FAILED` | server_recoverable | retry 3 раза, backoff [2,5,15]с |
| `CONTENT_REJECTED` | ai_recoverable | переформулировать |
| `LOCAL_INFERENCE_FAILED` | server_recoverable | деградация large→medium→base |
| `TASK_PENDING` | server_recoverable | poll (продолжает опрос) |
| `ORPHAN` | integrity | register или delete |
| `BROKEN_ID_LINK` | integrity | восстановить родителя |
| `INTRUSIVE_DIR` | integrity | разобраться/удалить |
| `DEFAULT` | unknown | нет протокола → человек |
**Связь:** SESSIONS.md §Приложение Б, server_reactions.md.

## Решение 4: Три природы медиа-сбоя
**Q:** как обрабатываются ошибки провайдеров?
**A:**
| Природа | Код | Сервер | Claude |
|---|---|---|---|
| Техсбой провайдера | `PROVIDER_FAILED` | retry 3 → fallback → уведомление | ждёт/смена |
| Контент-отказ | `CONTENT_REJECTED` | нет retry | переформулировать |
| Локальный сбой | `LOCAL_INFERENCE_FAILED` | 1 retry → деградация → человек | ждёт |
**Связь:** SESSIONS.md §Приложение Б, [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст).

## Решение 5: get_error — движок читает реестр (D4)
**Q:** как движок собирает ErrorDetail из реестра?
**A:** `reactions.get_error(code)` → ищет код в yaml → собирает `ErrorDetail{code, message_template, recovery}`. Engine `_error()` зовёт `get_error`, если код есть в реестре.
**Дефект D4:** `code: str` НЕ ограничен реестром — принят любой код. Fs_*/table_* хендлеры minуют реестр и строят ErrorDetail напрямую с кодами `PATH_NOT_FOUND`/`FILE_NOT_FOUND` (не в yaml).
**Связь:** [files/core_engine_engine.md](core_engine_engine.md), D4, [G15](../global.md#g15-строковые-словари-без-единого-реестра-дрейфят-literalреестр--лекарство).

## Открытые вопросы файла
- **D4 (🟡):** Fs_/table_* хендлеры минуют реестр — коды не в yaml.
- **D27 (🟡):** `class` загружается, но ErrorDetail не имеет поля — не доходит до Claude.
- **D23 (⚪):** raw_response уходит без редактирования секретов (сейчас заглушки, эскалирует при провайдерах).

## Что улучшить
- Провести fs_*/table_* хендлеры через `Engine._error` (миграция кодов).
- Добавить `reaction_class: str` в ErrorDetail.
- Согласовать fallback-код с `DEFAULT`.
- Ограничить `code: str` валидатором из ключей реестра.
