# Q&A: core/state/state_manager.py

> **Роль:** владелец состояния в `workspace/` — снапшоты (`read.json`), очередь операций (`write.json`), сессионный лог (`_SESSION_LOG.md`). Граница «двух вселенных».
> **Сквозное:** [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные) (осн. — данные в `workspace/`, НЕ за его пределами); [G6](../global.md#g6-порядок-кода-в-файле--граф-зависимостей-снизу-вверх).
> **Статус кода:** реализован. **D9 (атомарная запись) закрыт** (доказано). **D29 (path traversal) закрыт** — safe-join через `core/paths.py` (единая точка, G17). Владелец мёртвого `log_event` ([D24]).
> **Навигация (знать не читая):** `core/state/state_manager.py`. Поверхность: `StateManager(workspace_path)` — `read_snapshot`/`write_snapshot`/`push_to_queue`/`execute_queue`/`log_event`/`entity_exists`; модульный `_atomic_write_json`; `_lock=threading.Lock`. Потребитель: `json_read_snapshot` (`server.py:247`) зовёт `read_snapshot(table)`. `write_*`/`push`/`execute` пока не подключены к tools.
> **Аудит-линзы:** security-reviewer (осн. — containment/traversal), mcp-developer (проводка), test-master (атомарность/регрессии). Находки доказаны запуском на `.venv`.

## Решение 1: хранение в `workspace/`, JSON-снапшоты (G9)
**Q:** где и в каком формате держать управляемые данные?
**A:** всё в `workspace/` (данные отделены от кода, переживают рестарт — [G9]); снапшоты/очередь — JSON (human-readable, без БД на MVP).
**Alt:** хранить в `core/` (ломает две вселенные) / БД (избыточно) — отброшены.
**Регрессия:** запись за пределы `workspace/` = нарушение [G9] = [D1]/[D29].
**Связь:** [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные).

## Решение 2: D9 — атомарная запись + in-process lock (ЗАКРЫТ, доказано)
**Q:** как не получить «рваный» `read.json`/повреждённую очередь при обрыве/гонке?
**A:** `_atomic_write_json` — temp-файл `{pid}` + `flush`+`fsync` + `os.replace` (атомарен в пределах ФС: читатель видит старый ИЛИ новый файл целиком). `push_to_queue`/`execute_queue` — read-modify-write под `threading.Lock`.
**Alt:** прямой `open("w")` (рваная запись) / `filelock` кросс-процессно — последнее оставлено на будущее (несколько инстансов).
**Регрессия / доказано запуском:** write→read roundtrip корректен; атомарность через `os.replace`. **Открыто:** `threading.Lock` держит только один процесс — мультипроцесс/воркеры → нужен `filelock` (история честно помечает).
**Связь:** [D9 закрыт](../AUDIT.md); SESSIONS.md §Сессия 2 (history_core_state v2.0).

## Решение 3: paths НЕ содержатся в workspace — traversal (D29, D1 неполон) → ЗАКРЫТО (S7)
**Q:** проверяется ли `entity_path` на выход за `workspace/`?
**A (ранее):** НЕТ — traversal доказан запуском.
**A (сейчас, D29 закрыт):** `safe_resolve(path, workspace)` из `core/paths.py` (единая точка, G17). Все 5 методов проверяют путь ДО I/O. `json_read_snapshot` ловит `ValueError` → `PATH_ESCAPE`.
**Доказательство:** интеграционные тесты — `read_snapshot("../etc/passwd")` → `ValueError`, `video/scene1` → OK.
**Связь:** [D29 закрыт](../AUDIT.md#-d29), [G17](../global.md#g17-containment-workspace--единая-точка-а-не-проверка-в-каждом-хендлере), [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные).

### 🔧 Инженерный план фикса D29 (РЕАЛИЗОВАН, S7)
- **Где:** `core/paths.py` (новый), `state_manager.py` (5 методов), `server.py` (json_read_snapshot)
- **Что сделано:** `safe_resolve(path, workspace)` — единая точка containment (G17)
- **Доказано:** интеграционные тесты — traversal блокируется, легитимные пути проходят

## Решение 4: `log_event` — владелец `_SESSION_LOG`, но не проведён (D24) и non-atomic
**Q:** как логируются действия?
**A:** `log_event(type, data)` аппендит markdown в `workspace/_SESSION_LOG.md`.
**Регрессия / доказано:** [D24] живёт здесь — `log_event` имеет **0 вызовов** (facts→лог не проведены). Плюс это `open("a")` **без `_lock` и без атомарности** — при появлении конкурентных вызовов записи могут переплестись (в отличие от очереди под локом). Латентно (0 вызовов).
**Связь:** [D24](../AUDIT.md#-d24), [core_contracts_fact.md].

## Открытые вопросы файла
- **🟡 D24 (log_event не проведён):** 0 вызовов; при проводке — сделать атомарным/под локом. См. [../AUDIT.md#-d24](../AUDIT.md#-d24).
- **⚪ мультипроцесс:** `threading.Lock` не защищает несколько инстансов → `filelock` (история).
- **⚪ рост очереди:** `push_to_queue` аппендит без лимита до `execute_queue` — потенциальный cache_overflow в пределах workspace.

## Что улучшить (тесты — security-reviewer / test-master)
- Тест D9-атомарности: параллельные `push_to_queue` → очередь не теряет элементов и не бьётся (лок сериализует).
- Тест containment для write-методов ДО их подключения к tools (не дать traversal попасть в прод).
- D29 regression test: `read_snapshot("../../etc")` / `json_read_snapshot({"table":"../.."})` → `PATH_ESCAPE` (страж, как D1 для fs_*).
