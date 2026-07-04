# История файла core/contracts/

> **Роль:** контракты сервера (ToolResult, ErrorDetail, Fact, TaskStatus, Recovery).
> **Последнее обновление:** 2026-07-03

---

## v2.3 — 2026-07-03 — D22+D23+D25+D4+D27

### Решение
- **D22:** `@model_validator` в ToolResult и TaskStatus — инвариант status↔error/result
- **D23:** `@field_validator("raw_response")` — маскировка секретов (authorization, api_key, token)
- **D25:** Fact.type → KNOWN_FACT_TYPES + model_post_init; Fact.id удалён
- **D4:** KNOWN_ERROR_CODES + field_validator для ErrorDetail.code
- **D27:** ErrorDetail.reaction_class — класс реакции из yaml

### Регрессия
- ToolResult(status="error") без error → ValidationError
- TaskStatus(status="failed") без error → ValidationError
- Unknown fact types/error codes → warning (не ошибка)
- raw_response с секретами → ***REDACTED***

### Связь
- G2 (ToolResult — единый конверт): инвариант форсируется
- G5 (философия ошибок): code + reaction_class + recovery
- G10 (ID сервером): Fact.id удалён (мёртвое поле)
- G15 (дрейф словарей): KNOWN_ERROR_CODES, KNOWN_FACT_TYPES

---

## v2.2 — 2026-07-03 — ErrorDetail.reaction_class

### Решение
- Добавлено поле `reaction_class: str = "unknown"`
- reactions.py прокидывает `class` из yaml в reaction_class

### Регрессия
- Claude получает класс поведения (ai_recoverable/server_recoverable/...)

### Связь
- D27 закрыт, G5

---

## v2.1 — 2026-07-03 — ErrorDetail.code валидация

### Решение
- Добавлен KNOWN_ERROR_CODES (список всех кодов из server_reactions.yaml)
- field_validator предупреждает о неизвестных кодах

### Регрессия
- Неизвестные коды вызывают warning (не ошибку)

### Связь
- D4 закрыт, G15

---

## v2.0 — 2026-07-01 — Инициализация контрактов

### Решение
- Recovery, ErrorDetail, Fact, ToolResult, TaskStatus
- Порядок: Recovery → ErrorDetail → ToolResult/TaskStatus (зависимости снизу вверх)
- Fact: type (str), data (dict), id (str|None)
- ToolResult: status, data, error, facts

### Регрессия
- code: str без валидации (D4)
- raw_response без маскировки (D23)
- Fact.type без реестра (D25)
- Fact.id мёртвое поле (D25)

### Связь
- G2, G5, G13, SESSIONS.md §Сессия 2
