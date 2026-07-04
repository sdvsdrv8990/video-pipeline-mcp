# История файла core/transport/

> **Роль:** транспорт к Claude AI Web (JSON-RPC 2.0).
> **Последнее обновление:** 2026-07-03

---

## v2.2 — 2026-07-03 — D30: structuredContent

### Решение
- Facts → structuredContent.facts (было: нестандартный ключ "facts")
- Структурный error (code/reaction_class/recovery) → structuredContent (было: нестандартный ключ "error")
- content = человекочитаемый текст, structuredContent = машинные данные

### Регрессия
- Claude видит facts, код ошибки, класс поведения и recovery
- Спек-совместимо с MCP CallToolResult

### Связь
- D30 закрыт, G14 (ToolResult богаче провода)

---

## v2.1 — 2026-07-03 — MCP формат

### Решение
- tools/list отдаёт плоский список (list_tools), не grouped
- Grouped доступен через list_tools_grouped() для отображения

### Регрессия
- Claude AI Web видит инструменты

### Связь
- G7 (JSON-RPC)

---

## v2.0 — 2026-07-01 — D13: lifecycle/версия

### Решение
- Нотификации → HTTP 202 (было: JSON-ответ)
- Согласование версии протокола
- Исправлен баг: "Unregistered..." ложно матчился как "connected"

### Регрессия
- Полный Streamable-HTTP не реализован (D12)

### Связь
- D13 закрыт, SESSIONS.md §Сессия 5
