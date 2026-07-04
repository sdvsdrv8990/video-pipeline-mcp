# История файла core/firewall/

> **Роль:** файрвол сервера — защита от вредоносных запросов.
> **Последнее обновление:** 2026-07-04

---

## v2.5 — 2026-07-04 — Firewall.reload(): горячая перезагрузка правил (fail-closed)

### Решение
- **Мотив:** hot-reload `firewall.yaml` без рестарта сервера (задача сессии, scope = config-only). Раньше применить новый конфиг = пересоздать `Firewall` и потерять ссылку, которую держит `Transport`.
- **Рефактор:** тело `__init__` вынесено в статический `_make_rules(config) -> tuple` + `_assign(rules)`. Добавлен `reload(config) -> bool`.
- **Атомарность + fail-closed (D10):** `reload` собирает правила во ВРЕМЕННЫЕ объекты; только при успехе делает `_assign`. Битый конфиг (top-level не dict → `.get()` падает, несовместимый тип) → `except` → `return False`, self не тронут, прежние правила держатся, файрвол НЕ выключается. Ссылка на объект `Firewall` сохраняется → `Transport` и все держатели сразу видят новые правила.
- **Проверено:** unit — валидный reload меняет `max_requests` 60→5 (True); non-dict → False; None → дефолты True. Live — правка `firewall.yaml` → применилась, битый yaml → прежние правила.
- **Честная граница:** рантайм-счётчики rate-limit и баны IP при reload сбрасываются. Reload — редкое админ-действие; за туннелем один клиентский IP (G18), потеря банов пренебрежима. Injection-паттерны = литеральные подстроки (`re.escape`), кривой regex невозможен — не источник ошибки.

### Регрессия
- `__init__` теперь через `_make_rules/_assign` — поведение идентично (тот же набор правил из того же конфига).
- `reload()` НЕ сохраняет состояние счётчиков/банов — задокументировано в докстринге как осознанный выбор.
- Вызов reload — из монитор-цикла server.py по mtime (см. history_server.md v2.9).

### Связь
- D10: fail-closed — битый конфиг не выключает файрвол.
- G18: за туннелем один IP → сброс банов при reload не важен.
- history_server.md v2.9: кто и когда дёргает reload.

---

## v2.4 — 2026-07-03 — D15+D16+D17+D18+D20

### Решение
- **D15:** DEFAULT_PATTERNS → list() копия (было: shared mutable)
- **D16:** violations сбрасываются когда окно запросов протухло
- **D17:** time-based anomaly удалён, оставлен только event-based (dangerous_tools)
- **D18:** DANGEROUS_TOOLS через constructor (было: хардкод frozenset)
- **D20:** FirewallResult.error_code удалено (было: write-only)

### Регрессия
- Инстансы injection_detector изолированы
- Violations сбрасываются постепенно
- Anomaly detector проверяет только dangerous_tools (без временных окон)
- Dangerous tools конфигурируемы
- FirewallResult чист без мёртвых полей

### Связь
- G12 (эфемерное in-process состояние): violations + anomaly упрощены
- G15 (дрейф словарей): DANGEROUS_TOOLS конфигурируемый

---

## v2.3 — 2026-07-03 — D7: injection detector

### Решение
- DEFAULT_PATTERNS обновлены: убраны FP-паттерны ("act as", "disregard", "override")
- Оставлены только явно вредоносные связки ("ignore previous instructions", "rm -rf")

### Регрессия
- Легитимные фразы проходят
- Явно вредоносные блокируются

### Связь
- D7 закрыт

---

## v2.2 — 2026-07-03 — D18: dangerous_tools

### Решение
- DANGEROUS_TOOLS через constructor (set)
- DEFAULT_DANGEROUS_TOOLS — дефолтный список

### Регрессия
- Список конфигурируем из config

### Связь
- D18 закрыт

---

## v2.1 — 2026-07-03 — D15+D16

### Решение
- **D15:** self.patterns = list(patterns) if patterns is not None else list(DEFAULT_PATTERNS)
- **D16:** violations сбрасываются в _cleanup когда окно пустое

### Регрессия
- Инстансы изолированы
- Unblock эффективен повторно

### Связь
- D15, D16 закрыты

---

## v2.0 — 2026-07-01 — Инициализация

### Решение
- Firewall: IP blocklist → rate limit → injection → anomaly
- FirewallDecision: ALLOW, BLOCK, RATE_LIMIT
- Fail-closed при сбое (D10)

### Регрессия
- IP-гранулярность бесполезна за туннелем (D14)
- Time-based anomaly (D17 — позже удалён)

### Связь
- G3 (firewall перед ядром), G12, SESSIONS.md §Сессия 2
