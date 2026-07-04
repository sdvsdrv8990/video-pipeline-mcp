# История файла core/reactions/reactions.py

> **Роль:** чтение и маппинг реакций из server_reactions.yaml.
> **Последнее обновление:** 2026-07-03

---

## v2.2 — 2026-07-03 — D27: reaction_class

### Решение
- get_error прокидывает `class` из yaml в reaction_class ErrorDetail
- reaction_class: str = "unknown" (по умолчанию)

### Регрессия
- Claude получает класс поведения (ai_recoverable/server_recoverable/...)

### Связь
- D27 закрыт, G5 (код → класс поведения)

---

## v2.1 — 2026-07-03 — D4: реестр подключён

### Решение
- reactions читает server_reactions.yaml
- get_error возвращает ErrorDetail по коду из реестра
- Fallback → UNKNOWN_ERROR → DEFAULT

### Регрессия
- Коды вне реестра → warning (D4)

### Связь
- D4 частично закрыт, G5

---

## v2.0 — 2026-07-01 — Инициализация

### Решение
- Reactions читает YAML, возвращает ErrorDetail по коду
- get_error: code → reaction → ErrorDetail
- get_reaction: code → raw dict
- list_codes: все коды кроме DEFAULT

### Регрессия
- code: str без валидации (D4)
- reaction_class не прокидывается (D27)

### Связь
- G5, SESSIONS.md §Сессия 2
