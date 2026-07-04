# История файла core/state/state_manager.py

> **Роль:** владелец состояния в workspace/ — снапшоты, очередь, лог.
> **Последнее обновление:** 2026-07-03

---

## v2.1 — 2026-07-03 — D29: path traversal containment

### Решение
- Добавлен импорт `safe_resolve` из `core/paths.py` (единая точка, G17)
- Все 5 методов (`read_snapshot`, `write_snapshot`, `push_to_queue`, `execute_queue`, `entity_exists`) проверяют путь ДО I/O
- Нарушение → `ValueError` → вызывающий код маппит в `PATH_ESCAPE`
- `json_read_snapshot` в `server.py` ловит `ValueError` → `ErrorDetail(code="PATH_ESCAPE")`

### Регрессия
- Легитимные вложенные пути (`video/scene1`) проходят (доказано тестами)
- Traversal `../etc/passwd` блокируется (доказано тестами)
- Все 5 методов защищены (доказано интеграционными тестами)

### Связь
- D29 закрыт (SESSIONS.md §Сессия 7)
- `core/paths.py` — единая точка containment (G17)
- D1 (fs_*) + D29 (state_manager) = полная защита workspace
- Паттерн: MCP servers filesystem `Path.resolve()` → `is_relative_to(root)`

---

## v2.0 — 2026-07-01 — D9: атомарная запись

### Решение
- `_atomic_write_json` — temp-файл + fsync + os.replace
- `threading.Lock` для read-modify-write очереди

### Регрессия
- `threading.Lock` не защищает несколько инстансов (история честно помечает)
- `log_event` без атомарности (D24)

### Связь
- D9 закрыт
- SESSIONS.md §Сессия 2

---

## v1.0 — 2026-07-01 — Инициализация

### Решение
- StateManager с workspace_path
- read_snapshot, write_snapshot, push_to_queue, execute_queue, log_event, entity_exists
- JSON-снапшоты, очередь операций

### Регрессия
- Path traversal (D29) — методы без safe-join
- log_event не проведён (D24)

### Связь
- G9 (две вселенные: код vs workspace)
- SESSIONS.md §Сессия 2
