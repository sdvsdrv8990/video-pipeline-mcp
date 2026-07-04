# Q&A: core/reactions/reactions.py

> **Роль:** загрузчик и маппер реестра реакций — читает `config/server_reactions.yaml`, по коду ошибки собирает `ErrorDetail` с шаблоном/recovery. От него зависят `Engine._error`/`get_error`.
> **Сквозное:** [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст) (осн. — ошибка → код + класс поведения + recovery); [G8](../global.md#g8-дисциплина-документирования-решений-5-вопросов--4-уровня) (история честна про незакрытый D4 — контраст с D3).
> **Статус кода:** реализован, тонкий. Для известных кодов собирает корректно, НО роняет реакционный `class` (D27) и fallback дрейфит (D4-инстанс). Реестр подключён к движку (D4 частично).
> **Навигация (знать не читая):** `core/reactions/reactions.py`. Поверхность: `Reactions(config_path=None)` — `load(path)`, `get_error(code, raw_message, raw_response)->ErrorDetail`, `get_reaction(code)->dict|None`, `list_codes()`. Состояние: `reactions: dict` (весь yaml). Потребитель — `Engine._error` (`engine.py:78`), `server.py:99` создаёт.
> **Аудит-линзы:** mcp-developer (осн. — контракт/маппинг), test-master. Находки доказаны запуском на `.venv`.

## Решение 1: YAML-реестр + маппинг по коду ошибки
**Q:** как задавать реакции, чтобы добавление = правка конфига, не кода?
**A:** `server_reactions.yaml` (human-readable) → `load()` в `dict` → `get_error(code)` собирает `ErrorDetail`. Единая точка входа для всех ошибок ([G5]).
**Alt:** JSON (менее читаем) / маппинг по типу исключения (смешение уровней) — отброшены.
**Регрессия / находка (доказано):** `load()` при отсутствии файла молча оставляет `reactions={}` (нет warn) → `get_error` всегда идёт в fallback (UNKNOWN). Тихая тотальная деградация при опечатке пути (сейчас файл на месте).
**Связь:** [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст); реестр — `config/server_reactions.yaml`.

## Решение 2: `get_error(known)` → ErrorDetail из шаблона; но `class` роняется (D27)
**Q:** что из записи реестра попадает в `ErrorDetail`?
**A:** для известного кода — `code`, `message` (`raw_message` или `message_template`), `recovery` (из `recovery`-блока), `raw_response`.
**Регрессия / находка (доказано запуском):** запись реестра несёт поле **`class`** (`ai_recoverable`/`server_recoverable`/`human_required`/`integrity`/`unknown`) — но `get_error` его НЕ прокидывает: `ErrorDetail(PROVIDER_FAILED).model_dump()` → `[code,message,recovery,raw_response]`, `class` отсутствует (у `ErrorDetail` нет такого поля), хотя в реестре `PROVIDER_FAILED.class=server_recoverable`. **Механизм [G5] «код → КЛАСС поведения» не доходит до Claude** — он вынужден выводить retry/reformulate/human из строки кода/`recovery.reason`, а не из структурного класса. Семейство «продюсер несёт поле, оно роняется до Claude» (ср. [D20], facts-вне-content [D22]). См. [D27](../AUDIT.md#-d27).
**Alt (фикс):** добавить `reaction_class` в `ErrorDetail` и прокинуть в `get_error`.
**Связь:** [D27](../AUDIT.md#-d27), [core_contracts_error_detail.md] (нет поля class).

## Решение 3: fallback на неизвестный код — инстанс дрейфа D4
**Q:** что при коде вне реестра?
**A:** возвращает `ErrorDetail(code="UNKNOWN_ERROR", message="Непредвиденная ошибка", recovery=Recovery(reason=DEFAULT.recovery.reason))`.
**Регрессия / находка (доказано запуском):** три несогласованности:
1. эмитит `code="UNKNOWN_ERROR"` — **не ключ реестра** (fallback-ключ там `DEFAULT`); повторный lookup `UNKNOWN_ERROR` снова промахнётся.
2. хардкодит `message="Непредвиденная ошибка"` (без точки), **игнорируя** `DEFAULT.message_template="Непредвиденная ошибка."` — хотя формально «падает в DEFAULT».
3. дропает `DEFAULT.class=unknown` (общий с [D27]).
Инстанс дрейфа [D4]: реестр — источник истины, а fallback его частично обходит.
**Alt (фикс):** эмитить `code="DEFAULT"` и брать полный шаблон DEFAULT (`message_template`+recovery+class).
**Связь:** [D4](../AUDIT.md#-d4), [core_contracts_error_detail.md · Решение 4].

## Решение 4: реестр подключён, но история ЧЕСТНА про незакрытый D4 (контраст с прошлым D3)
**Q:** насколько D4 закрыт?
**A:** движок ходит через реестр ([core_engine_engine.md · Решение 3] — чистая сторона), НО `server.py` fs/table хендлеры инлайнят коды вне реестра. SESSIONS.md §Сессия 2 (history_core_reactions v2.0) **сам это признаёт**: «Код в хендлерах `server.py` (`PATH_NOT_FOUND`, `FILE_NOT_FOUND`) пока частично инлайновый — полная миграция… следующий шаг».
**Почему важно:** позитивный контраст с историей [D3] — там v2.0 ЗАЯВИЛА bearer-auth закрытым, а кода не было (враньё → ложное чувство защищённости); здесь история честно помечает D4 незавершённым. [G8]: доки-нарратив ценен, когда честен. D3 закрыт (S6), D4 всё ещё открыт.
**Связь:** [D4](../AUDIT.md#-d4), [D3](../AUDIT.md#-d3) (исторический контраст честности доков), [G8](../global.md#g8-дисциплина-документирования-решений-5-вопросов--4-уровня).

## Открытые вопросы файла
- **🟡 D27 (реакционный `class` роняется):** загружается из yaml, но не доходит до Claude (`ErrorDetail` без поля class) — доказано. Ломает [G5] «код→класс». См. [../AUDIT.md#-d27](../AUDIT.md#-d27).
- **🟡 D4 (fallback-инстанс):** `UNKNOWN_ERROR` не ключ реестра + игнор `DEFAULT.message_template`/`class` — доказано. См. [../AUDIT.md#-d4](../AUDIT.md#-d4).
- **⚪ молчаливый пустой реестр:** отсутствие конфига → `reactions={}` без warn → всё в UNKNOWN (латентно, файл на месте).

## Что улучшить (регрессия-тесты, линза test-master)
- Тест D27 (после фикса): `get_error(known)` → в `ErrorDetail` присутствует реакционный `class` из реестра.
- Тест D4-fallback (после фикса): неизвестный код → `code`∈реестра (`DEFAULT`), `message`==`DEFAULT.message_template`.
- Тест загрузки: `Reactions("несуществующий")` → явный warn/ошибка, а не тихий пустой реестр.
- Тест целостности реестра: каждый код, эмитируемый где-либо (`engine`/`server`/`providers`), ∈ `list_codes()` ∪ спец-кодов (ловит дрейф D4).
