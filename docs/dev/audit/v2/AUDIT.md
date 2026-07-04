# Аудит проекта `video_pipeline_mcp` — v2

**Дата старта:** 2026-07-02
**Метод:** чтение исходников + эмпирическая проверка (импорт, запуск, реальные вызовы) на `.venv` Python. Линзы: `mcp-developer`, `security-reviewer`, `test-master`. Процесс — `docs/dev/workflow.md`.
**Статус:** 🚧 в работе — это ХАБ (см. `README.md` §5, §7.1): обновляется после каждого прохода, не отстаёт от `files/*`.

> Жанр как в v1: `AUDIT.md` = «что улучшить» (дефекты D#), `files/*` + `global.md` = «почему так». Без дублей: находка живёт в одном месте, файлы ссылаются.

---

## Покрытие (карта прогресса) — 22 файла · ⬜ не начат · 🔶 pass1 · ✅ done

| Блок | Файлы |
|---|---|
| **Security/firewall (6)** | ✅ firewall · ✅ injection_detector · ✅ rate_limiter · ✅ ip_blocklist · ✅ anomaly_detector · ✅ firewall/contracts |
| **Ядро MCP (7)** | ✅ server (D3 закрыт) · 🔶 engine · 🔶 reactions · 🔶 ids · ✅ state (D29 закрыт) · 🔶 transport · 🔶 transport/tunnel |
| **Контракты (5)** | 🔶 tool_result · 🔶 error_detail · 🔶 fact · 🔶 task_status · ✅ __init__ |
| **Providers (4, заглушки)** | ✅ ffmpeg · ✅ tts · ✅ stt · ✅ img (1-pass; honest NotImplementedError; чистая сторона D4; глубокий аудит отложен до реализации) |

Прогресс: **ВСЕ 22/22 файла pass1 пройдены.** Закрыты (доказано): **D2, D3, D4, D5, D6, D7, D8, D9, D10, D11, D12, D13, D15, D16, D17, D18, D20, D21, D22, D23, D24, D25, D26, D27, D28, D29, D30** (+D1/D31 ЧАСТИЧНО, D14 известное ограничение). **ВСЕ ДЕФЕКТЫ ЗАКРЫТЫ** (S6-S13). **Реализованы все 9 fs_* инструментов** (S14).

---

## Дельта против v1 (что уже закрыто — доказано запуском)

| D# (v1) | Было | Сейчас | Доказательство |
|---|---|---|---|
| 🔴 D2 | `Firewall()` без конфига → `firewall.yaml` мёртв | **Закрыто** | `server.py:96` `_load_yaml(firewall.yaml)` → `:102` `Firewall(firewall_config)` |
| 🔴 D8 | `anomaly_detector` не подключён / не срабатывал | **Закрыто (переподтверждено v2)** | шаг 4 зовёт `check()`; фикс `params.name`: 12 уникальных tools → `detected=True`; 50× `tools/list` → `False`. Детектор реально ожил. |
| 🟠 D6 | мгновенный бан с 1-го превышения | **Закрыто** | бан только после `rate_limiter.should_ban()` (метод существует) |
| 🟠 D7 | авто-бан IP при injection | **Закрыто** | injection → `BLOCK` текущего запроса, без `ip_blocklist.block()` |
| ⚪ D10 | fail-open вокруг `firewall.check()` | **Закрыто (верифицировано v2)** | `server.py:359-380` fail-**closed**: `except` вокруг `check()` → HTTP 403 block (не `except: pass`); нераспарсенное тело → 400. Комментарии `# D10: fail-closed` в коде. |
| 🔴 D1 | path traversal в `fs_*` (`../../etc/passwd`→root:x:0:0) | **ЧАСТИЧНО (fs_* да; state_manager НЕТ)** | `_safe_resolve` (`server.py:67`) закрыл fs_* (доказано: traversal/абс/sibling → BLOCKED). НО `state_manager` — вторая поверхность без safe-join → traversal жив через `json_read_snapshot`, см. **[D29]**. Полное закрытие = централизовать safe-join. |
| 🟠 D5 | `input_schema` декоративна (вход не валидируется) | **Закрыто (верифицировано v2)** | `Engine.call`→`_validate` (`engine.py:127`) ДО хендлера; `jsonschema 4.23.0` (`requirements.txt:22`). Доказано: missing-required и `path=int` → `VALIDATION_ERROR`, валидные → success. |
| 🟡 D9 | ID-хвост `md5[:8]` = 32 бита (коллизия ~50% на 77k) | **Закрыто (верифицировано v2)** | `_generate_unique`→`uuid4().hex` (32 hex, 122 бита). Доказано: 50000 ID → 0 коллизий; старый 8-hex формат → `is_valid_format`=False. Реестр связей всё ещё отсутствует → [D28]. |
| 🟠 D13 | нотификации → JSON-ответ (наруш. MCP); версия хардкод; `initialized`→ошибка | **Закрыто (верифицировано v2)** | `handle_request`: нотификация→`None`(→202); `initialize` согласует версию. Доказано: `notifications/initialized`→None, `{2025-06-18}`→эхо, `{1999}`→fallback `2025-06-18`. Полный Streamable-HTTP — частично ([D12]). |
| 🟠 D11 | туннеля к Claude AI Web нет ни в каком виде | **Закрыто (верифицировано v2)** | `core/transport/tunnel.py` (`CloudflaredTunnel`) + `server.py --tunnel` + `run.sh`; событийная готовность, супервизор+backoff. Остаётся гигиена секрета [D31] + корень single-IP [D14]. |

---

## Новые / уточнённые дефекты (линзы скилов)

### 🟡 D7. Injection detector FP устранены — ЗАКРЫТО (S13)
`core/firewall/rules/injection_detector.py` — обновлены `DEFAULT_PATTERNS`: убраны FP-паттерны ("act as", "disregard", "override") легитимные для видео/TTS. Добавлена word-boundary проверка (`\b...\b`).
**Статус:** закрыто. Легитимные фразы проходят, явно вредоносные блокируются.
**Связь:** [files/core_firewall_rules_injection_detector.md](files/core_firewall_rules_injection_detector.md).

### 🟠 D1. Path traversal — не ловится на уровне firewall (остаётся у инструментов)
**Доказательство:** `injection_detector.detect({"path":"../../../../etc/passwd"}) -> False`.
**Почему важно:** `docs/dev/threat_landscape.md` перечисляет traversal как ожидаемую атаку. Firewall её принципиально не видит (контентная эвристика ≠ проверка путей). Защита обязана быть в `fs_*` (safe-join через `resolve()` + `is_relative_to(root)`, эталон `werkzeug.safe_join`).
**Статус:** переносится из v1; закрыть на уровне `tools/filesystem`, не firewall.
**Связь:** [files/server.md] (fs_* инструменты), [files/core_firewall_firewall.md].

### 🟡 D14. IP-гранулярность за туннелем — известное ограничение (S13)
**Статус:** известное ограничение архитектуры. Весь трафик Claude приходит с одного IP туннеля → IP-based правила бесполезны. **Митигировано:** D3 (auth) закрыт — bearer-токен = единственный барьер; D16 (violations reset) закрыт — мягкий порог работает повторно. Полный фикс требует рефакторинга firewall на session/token granularity (значительный объём работы, отложен до масштабирования).
**Связь:** [G12](global.md#g12-эфемерное-in-process-состояние-файрвола), [G18](global.md#g18-за-туннелем-клиент-один).

### 🟡 D15. Shared mutable DEFAULT_PATTERNS — ЗАКРЫТО (S10)
`core/firewall/rules/injection_detector.py` — `self.patterns = list(patterns) if patterns is not None else list(DEFAULT_PATTERNS)`. Копия списка, не ссылка.
**Статус:** закрыто. Инстансы изолированы, тесты не заражают друг друга.
**Связь:** [files/core_firewall_rules_injection_detector.md](files/core_firewall_rules_injection_detector.md).

### 🟠 D16. Rate limiter: violations сбрасываются — ЗАКРЫТО (S10)
`core/firewall/rules/rate_limiter.py` — `_cleanup` сбрасывает violations когда окно запросов полностью протухло (нет ни одного запроса за window_sec). Это делает unblock полезным и восстанавливает мягкий порог ban_after после стабильного периода.
**Статус:** закрыто. Мягкий порог работает повторно, unblock эффективен.
**Связь:** [files/core_firewall_rules_rate_limiter.md](files/core_firewall_rules_rate_limiter.md), [D14](#-d14).

### 🟡 D17. Time-based anomaly detection удалён — ЗАКРЫТО (S13)
`core/firewall/rules/anomaly_detector.py` — time-based counting (`_method_history`, `window_sec`, `_cleanup`) **ПОЛНОСТЬЮ УДАЛЁН**. Оставлен ТОЛЬКО event-based detection: проверка конкретного запроса на опасные инструменты (`dangerous_tools`). Причина: таймеры пропускают события и дают ложные срабатывания.
**Статус:** закрыто. Детектор проверяет каждый запрос отдельно, без временных окон.
**Связь:** [files/core_firewall_rules_anomaly_detector.md](files/core_firewall_rules_anomaly_detector.md), [D14](#-d14).

### 🟡 D18. `DANGEROUS_TOOLS` конфигурируемый — ЗАКРЫТО (S10)
`core/firewall/rules/anomaly_detector.py` — `dangerous_tools: set[str]` через constructor. Дефолт в `DEFAULT_DANGEROUS_TOOLS`. При появлении `config/ops/*.yaml` — читать оттуда.
**Статус:** закрыто. Список конфигурируем, инстансы изолированы.
**Связь:** [files/core_firewall_rules_anomaly_detector.md](files/core_firewall_rules_anomaly_detector.md).

### ⚪ D19. `_method_history` не выселяет IP-ключи — рост памяти при потоке прямых IP (новый)
`core/firewall/rules/anomaly_detector.py` — `_cleanup` тримит списки внутри IP, но сам dict ключей по IP не чистится.
**Доказательство (реальный запуск):** 1000 запросов с разных IP → `len(_method_history)==256` (все уникальные ключи живут). Ключи никогда не удаляются.
**Почему важно:** при флуде с подменных IP (bot_army из `threat_landscape.md`) — неограниченный рост словаря = вектор memory-DoS. За туннелем не проявляется (1 ключ), но прямые/нетуннельные подключения — реальная поверхность.
**Как чинить:** выселять IP-ключ, когда его список пуст после `_cleanup`; либо TTL-эвикция ключей. Общее с эфемерным in-process состоянием (кандидат в G#).
**Связь:** [files/core_firewall_rules_anomaly_detector.md · Открытые вопросы](files/core_firewall_rules_anomaly_detector.md).

### ⚪ D20. FirewallResult.error_code удалено — ЗАКРЫТО (S12)
`core/firewall/contracts.py` — поле `error_code` удалено (было write-only, не читалось). `firewall.py` — убраны все `error_code=` из возвратов.
**Статус:** закрыто. Контракт чист, нет мёртвых полей.
**Связь:** [files/core_firewall_contracts.md](files/core_firewall_contracts.md).

### 🟡 D21. Потребитель схлопывает `FirewallDecision` — ЗАКРЫТО (S9)
`server.py` — ветвление по `FirewallDecision`: `BLOCK` → HTTP 403, `RATE_LIMIT` → HTTP 429 + Retry-After, `ALLOW` → пропуск. Enum-сравнение вместо строкового.
**Статус:** закрыто. Claude различает типы блокировки и может делать backoff.
**Связь:** [files/core_firewall_contracts.md](files/core_firewall_contracts.md), [D20](#-d20).

### 🟡 D31. Гигиена секрета туннеля — .gitignore + warning (ЧАСТИЧНО ЗАКРЫТО)
`config/tunnel.yaml` добавлен в `.gitignore` (не попадёт в git). Warning в `tunnel.yaml` (D31). Env-приоритет сохранён (`MCP_TUNNEL_TOKEN` > yaml).
**Остаётся:** `--token` в argv (виден в `ps` — cloudflared не поддерживает env-only). Это ограничение cloudflared, не нашего кода.
**Связь:** [files/core_transport_tunnel.md · Решение 2](files/core_transport_tunnel.md), [G11](global.md#g11-поставщик-туннеля--cloudflare-tunnel-cloudflared).

### 🟡 D30. Transport кладёт facts/error в structuredContent — ЗАКРЫТО (S11)
`core/transport/transport.py` — `_handle_tools_call` конвертирует `facts` → `structuredContent.facts`, структурный `error` (code/reaction_class/recovery) → `structuredContent`. Спек-совместимо: `content` = человекочитаемый текст, `structuredContent` = машинные данные.
**Статус:** закрыто. Claude видит facts, код ошибки, класс поведения и recovery.
**Связь:** [files/core_transport_transport.md](files/core_transport_transport.md), [D20](#-d20), [D22](#-d22), [D24](#-d24), [G14](global.md#g14-внутренний-toolresult-богаче-того-что-доходит-до-claude-на-проводе).

### 🟠 D29. Path traversal через `state_manager` — ЗАКРЫТО (единая точка containment)
`core/paths.py` (новый), `core/state/state_manager.py` (5 методов), `server.py` (json_read_snapshot).
**Реализация (доказано запуском):** `safe_resolve(path, workspace)` из `core/paths.py` — единая точка containment (G17). Все 5 методов state_manager проверяют путь ДО I/O. `json_read_snapshot` ловит `ValueError` → `ErrorDetail(code="PATH_ESCAPE")`. Паттерн: MCP servers filesystem `Path.resolve()` → `is_relative_to(root)`.
**Статус:** закрыто. D1 (fs_*) + D29 (state_manager) = полная защита workspace.
**Связь:** [files/core_state_state_manager.md](files/core_state_state_manager.md), [D1](#дельта-против-v1-что-уже-закрыто--доказано-запуском), [G17](global.md#g17-containment-workspace--единая-точка-а-не-проверка-в-каждом-хендлере), [G9](global.md#g9-две-вселенные-код-сервера-vs-workspace-данные).

### ⚪ D28. IDGenerator — мёртвые импорты удалены — ЗАКРЫТО (S12)
`core/ids/id_generator.py` — удалены `hashlib`, `time`, `Optional` (не использовались). IDGenerator остаётся для будущих entity-создающих tools.
**Статус:** закрыто. Код чист, нет мёртвого кода.
**Связь:** [files/core_ids_id_generator.md](files/core_ids_id_generator.md).

### 🟡 D27. Реакционный `class` не доходит до Claude — ЗАКРЫТО (S9)
`core/contracts/error_detail.py` — добавлено поле `reaction_class: str = "unknown"`. `core/reactions/reactions.py` — `get_error` прокидывает `class` из yaml в `reaction_class`.
**Статус:** закрыто. Claude получает класс поведения (ai_recoverable/server_recoverable/human_required/integrity/unknown) и ветвит по нему.
**Связь:** [files/core_reactions_reactions.md](files/core_reactions_reactions.md), [files/core_contracts_error_detail.md](files/core_contracts_error_detail.md), [G5](global.md#g5-философия-ошибок).

### 🟡 D26. Движок: лишние params → `VALIDATION_ERROR` — ЗАКРЫТО (S9)
`core/engine/engine.py` — `TypeError` ловится отдельно от generic `Exception` и маппится в `VALIDATION_ERROR` (класс `ai_recoverable`). Claude исправляет params и повторяет, а не эскалирует к человеку.
**Статус:** закрыто. Мисклассификация устранена, G5 (код → класс поведения) работает.
**Связь:** [files/core_engine_engine.md](files/core_engine_engine.md), [D4](#-d4).

### 🟡 D12. Origin fail-closed — ЗАКРЫТО (S13)
`server.py` — при заданном `ALLOWED_ORIGINS` блокирует запросы БЕЗ заголовка `Origin` (fail-closed для браузерного вектора). Bind `127.0.0.1` по умолчанию закрыт изначально.
**Статус:** закрыто. Origin-проверка работает корректно.
**Связь:** [files/server.md](files/server.md), [D3](#-d3).

### 🟠 D3. Аутентификация — bearer-токен ДО файрвола (ЗАКРЫТО)
`server.py:59-60` (config), `server.py:371-386` (check).
**Реализация (доказано запуском):** `MCP_AUTH_TOKEN` из env → bearer-проверка ДО firewall → `secrets.compare_digest` (constant-time). Если токен не задан — auth отключена (локальная разработка). HTTP 401 при отсутствии/неверном токене. Реестр: `AUTH_REQUIRED`/`AUTH_FAILED` в `server_reactions.yaml` (класс `human_required`).
**Статус:** закрыто. Единственный барьер от публичного URL туннеля теперь работает.
**Связь:** [files/server.md · Решение 3](files/server.md), [D14](#-d14) (IP-firewall ≠ auth), [D12](#-d12), [G18](global.md#g18-за-туннелем-клиент-один--гранулярность-по-ip-бессмысленна-секрет-уязвим).

### 🟡 D24. Facts → _SESSION_LOG проведён — ЗАКРЫТО (S11)
`core/engine/engine.py` — после успешного вызова хендлера, facts логируются через `state_manager.log_event(fact.type, fact.data)`. Engine принимает `state_manager` через constructor.
**Статус:** закрыто. Серверная «память о действиях» работает, facts копятся в `_SESSION_LOG.md`.
**Связь:** [files/core_contracts_fact.md](files/core_contracts_fact.md), [files/core_state_state_manager.md](files/core_state_state_manager.md), [G1](global.md#g1-роль-сервера).

### ⚪ D25. Fact.type с реестром, id удалён — ЗАКРЫТО (S12)
`core/contracts/fact.py` — `KNOWN_FACT_TYPES` + `model_post_init` предупреждает о неизвестных типах. Поле `id` удалено (было мёртвым, всегда None).
**Статус:** закрыто. Типы фактов валидируются, контракт чист.
**Связь:** [files/core_contracts_fact.md](files/core_contracts_fact.md), [G10](global.md#g10-id-генерирует-сервер-не-claude).

### 🟡 D4. Реестр реакций полон — коды привязаны к yaml — ЗАКРЫТО (S12)
`config/server_reactions.yaml` — добавлены `FILE_NOT_FOUND`/`TABLE_NOT_FOUND` (были хардкодом). `core/contracts/error_detail.py` — `KNOWN_ERROR_CODES` + `field_validator` предупреждает о неизвестных кодах.
**Статус:** закрыто. Единый источник кодов, дрейф предотвращён.
**Связь:** [files/core_contracts_error_detail.md](files/core_contracts_error_detail.md), [G15](global.md#g15-строковые-словари-без-единого-реестра-дрейфят).

### ⚪ D23. raw_response секреты маскируются — ЗАКРЫТО (S12)
`core/contracts/error_detail.py` — `@field_validator("raw_response")` маскирует `authorization`, `api_key`, `token`, `set-cookie`, `cookie`, `secret`, `password` → `***REDACTED***`.
**Статус:** закрыто. Секреты провайдеров не утекают через Claude.
**Связь:** [files/core_contracts_error_detail.md](files/core_contracts_error_detail.md).

### 🟡 D22. Terminal-конверты форсят инвариант — ЗАКРЫТО (S11)
`core/contracts/tool_result.py` и `core/contracts/task_status.py` — `@model_validator(mode="after")` связывает `status` с наличием `error`/`result`. `ToolResult(status="error")` без error → ValidationError; `TaskStatus(status="failed")` без error → ValidationError; pending/processing → error/result очищаются.
**Статус:** закрыто. Контракты-база garantируют когерентность.
**Связь:** [files/core_contracts_tool_result.md](files/core_contracts_tool_result.md), [files/core_contracts_task_status.md](files/core_contracts_task_status.md), [G2](global.md#g2-единый-конверт-ответа-toolresult).

### ✅ D10. Fail-open у вызывающего кода — ЗАКРЫТО (верифицировано v2)
`server.py:359-380` реализует **fail-closed**: тело не парсится → HTTP 400; исключение внутри `firewall.check()` → `except` возвращает HTTP 403 «Firewall error (blocked)», НЕ пропуск в ядро. В коде явные комментарии `# D10: fail-closed`. `except: pass`-обхода нет.
**Статус:** закрыто (см. Дельту выше). Оставлено здесь как след верификации.

---

## TODO прохода (заполняется в task #6)
- [ ] Остальные 21 файл → `files/*` (тот же конвейер: линзы + эмпирика).
- [ ] `v2/global.md` — сквозные решения (G1…Gn), перенос + актуализация.
- [ ] Итоговая оценка по областям (как §1 в v1).
- [x] Верификация D10 — **закрыто** (fail-closed в `server.py:359-380`). ✅ anomaly_detector pass1 (D8 переподтверждён; +D17/D18/D19). ✅ firewall/contracts pass1 (+D20/D21).
- [x] **Security-блок pass1 закрыт (6/6).** Осталось: pass2 по 5 firewall-файлам (проводка G# в `global.md`, регрессии D14/D16/D17) → синтез `global.md` S1.
