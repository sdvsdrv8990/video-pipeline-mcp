# История файла core/engine/engine.py

> **Роль:** реестр и исполнитель инструментов.
> **Последнее обновление:** 2026-07-03

---

## v2.3 — 2026-07-03 — D26+D24+группы

### Решение
- **D26:** TypeError → VALIDATION_ERROR (было: INTERNAL_ERROR)
- **D24:** facts логируются через state_manager.log_event после успешного вызова
- **Группы:** ToolDefinition.group, register(group=...), list_tools_grouped()

### Регрессия
- Лишние params → VALIDATION_ERROR (ai_recoverable), Claude исправляет и повторяет
- Facts копятся в _SESSION_LOG.md
- Инструменты сгруппированы по group

### Связь
- D26 закрыт: G5 (код → класс поведения)
- D24 закрыт: facts → _SESSION_LOG
- G2 (ToolResult), G9 (две вселенные)

---

## v2.2 — 2026-07-03 — D5: валидация params

### Решение
- jsonschema.validate ДО вызова хендлера
- Отсутствие required → VALIDATION_ERROR
- Невалидные типы → VALIDATION_ERROR

### Регрессия
- Схема перестала быть декоративной

### Связь
- D5 закрыт

---

## v2.0 — 2026-07-01 — Инициализация движка

### Решение
- Engine с методами register, call, list_tools
- ToolDefinition: name, description, input_schema, handler
- _error() через реестр реакций (D4)
- Возврат ToolResult из handler

### Регрессия
- handler(**params) без фильтрации → TypeError при лишних params (D26)

### Связь
- G2, G13, SESSIONS.md §Сессия 2
