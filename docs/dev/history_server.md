# История файла server.py

> **Роль:** точка входа MCP-сервера, конвейер обработки запросов.
> **Последнее обновление:** 2026-07-03

---

## v2.9 — 2026-07-04 — Хот-релоад config без рестарта (firewall.yaml + server_reactions.yaml)

### Решение
- **Задача:** сервер подхватывает изменения декларативного config БЕЗ рестарта. Scope (выбран владельцем) = **только config**, не код (код handlers/core = честный рестарт).
- **Механизм:** в уже существующий монитор-цикл (10с) добавлено слежение за `mtime` файлов. Изменился → перечитать и применить: `firewall.yaml` → `firewall.reload(_load_yaml(p))` (см. history_core_firewall.md v2.5, fail-closed); `server_reactions.yaml` → `reactions.load(p)` (объект достаётся `getattr(engine, "reactions", None)`). Ноль зависимостей (mtime-poll, не watchdog).
- **Границы (честно):** `tunnel.yaml` НЕ входит — смена режима/порта требует рестарта cloudflared. `config/ops/*.yaml` нет в проекте (инструменты регистрируются инлайн) → ops-reload неприменим.
- **Анти-спам + fail-safe:** mtime фиксируется ДО применения → битый конфиг не ретрайдится каждые 10с (повтор только после следующей правки). Весь per-file блок в try/except: ошибка парсинга yaml или reload → печать `⚠️ … держим прежнее`, старое поведение сохраняется. Работает и без туннеля (проверка выше `if not tunnel`).
- **Проверено live:** валидная правка firewall → `♻️ firewall.yaml перезагружен`; битый yaml → `⚠️ НЕ применён: while parsing a flow sequence`; восстановление → `♻️`; reactions → `♻️`. Реальные config-файлы бэкапились и восстановлены (diff чист).

### Регрессия
- Монитор-цикл теперь делает и tunnel-diff (v2.7), и config-watch — оба дёшевы.
- Печать `♻️/⚠️` через `print()` (как весь UX server.py; см. v2.8 про line-buffering — сообщения видны и в файле-логе).
- Правки самого `server.py`/`core/*.py` по-прежнему требуют рестарта — это НЕ покрывается (осознанно, честно SHOUT вместо тихой полу-перезагрузки).

### Связь
- history_core_firewall.md v2.5: `Firewall.reload()` fail-closed — сердце фичи.
- v2.8: без построчной буферизации сообщения reload были бы невидимы при редиректе.
- Память: [[config-hot-reload]] (что перезагружается, что нет).

---

## v2.8 — 2026-07-03 — Построчная буферизация stdout/stderr (логи не зависают в буфере)

### Решение
- **Проблема (поймана при верификации v2.7):** при выводе в файл/пайп (не tty) Python БЛОЧНО буферизует stdout → консольные статусы (URL туннеля, `✅ Claude подключился`, переходы туннеля) зависают в буфере и не видны, пока буфер не заполнится/процесс не завершится. Проявилось как «пустой лог» при фоновом запуске.
- **Лекарство:** в начале `main()`, до любого вывода — `sys.stdout/stderr.reconfigure(line_buffering=True)`. НЕ отключаем буфер (он нужен), а делаем построчным: флаш на каждой `\n`. Обёрнуто в try/except (AttributeError/ValueError) — на случай заглушек stdout в тестах/встраивании.
- **Проверено:** инстанс на :8091 без `PYTHONUNBUFFERED` — стартовый баннер и `initialize`-строка появились в файле-логе сразу (≤3с), а не после завершения процесса.

### Регрессия
- Больше не нужен костыль `PYTHONUNBUFFERED=1` при перенаправлении вывода (фоновый запуск, systemd, `> log`).
- В tty поведение не меняется (там и так строчная буферизация).
- Микро-оверхед: флаш на каждую строку вместо блока — для консольных логов пренебрежимо.

### Связь
- Обеспечивает наблюдаемость из v2.7 (без построчного флаша те сообщения были бы невидимы при редиректе).

---

## v2.7 — 2026-07-03 — Консольный статус: подключение Claude AI Web + переходы туннеля

### Решение
- **Мотив:** `mode: quick` даёт эфемерный `*.trycloudflare.com`, который меняется при каждом респавне cloudflared → в коннекторе Claude остаётся мёртвый URL, инструменты «пропадают». Стартовый вывод URL был разовым (server.py:489); о смене URL и о факте подключения клиента консоль молчала.
- **Feature 1 — факт подключения Claude AI Web.** В `handle_jsonrpc` (после auth+firewall, перед `transport.handle_request`) ловим MCP-метод `initialize` — авторитетный сигнал нового сеанса — и печатаем `✅ Claude AI Web подключился: <clientInfo.name> <version> (MCP protocol <pv>, ip=<remote>)`. Проверено вживую: временный инстанс на :8090 без туннеля, POST initialize → строка в консоли (client=claude-ai 1.2.3).
- **Feature 2 — мониторинг туннеля по ПЕРЕХОДАМ.** Переписан healthcheck-цикл (был: раз в 60с, печать только при «нездоров»). Теперь раз в 10с диффит `tunnel.status()` против прошлого снимка и печатает только изменения: (1) 🌐 смена `public_url` → крупно новый URL + напоминание обновить коннектор; (2) 🔴 потеря соединения; (3) 🟢 восстановление; (4) ⚠️ новая `last_error`. Тихо, когда всё стабильно.
- **Границы:** весь код в `server.py`. `core/transport/tunnel.py` НЕ тронут — используется его публичный контракт `status()` (докстринг прямо декларирует «для мониторинга/логов сервера»). Восстановление соединения по-прежнему делает супервизор внутри туннеля; server.py только наблюдает и печатает.

### Регрессия
- `initialize` теперь логируется на каждый новый сеанс (в т.ч. реконнект Claude) — ожидаемо, полезно; шума нет (initialize редок).
- Интервал healthcheck 60с → 10с (пренебрежимая нагрузка; всё под `status_lock` внутри туннеля).
- Печать из async-цикла и из `handle_jsonrpc` может чередоваться в stdout — для консольных логов приемлемо.
- **Замечание для будущего:** вывод — через `print()`, как и весь UX server.py; при переходе на structured logging всё это стоит перевести на logger разом (не смешивать).

### Связь
- D11: туннель поднимается вместе с сервером — здесь добавлена наблюдаемость его жизненного цикла.
- Операционное правило (в памяти [[claude-ai-quick-tunnel-ephemeral-url]]): quick-URL меняется на каждом рестарте cloudflared → обнови коннектор; сервер теперь сам об этом кричит.

---

## v2.6 — 2026-07-03 — fs_delete: destructiveHint → MODIFY (auth-гейт коннектора)

### Решение
- **Симптом:** в Claude AI Web `fs_delete` не вызывался — клиент показывал «Authentication required to use this tool / This connector requires additional permissions. Reconnect it» + кнопку Connect. Все остальные 10 инструментов работали.
- **Root cause (доказан):** `fs_delete` — единственный инструмент с `ANNOTATIONS_DESTRUCTIVE` (`destructiveHint: True`, server.py:333/342). Строки ошибки в коде нет (grep по всему дереву чист) — её генерирует **клиент Claude.ai**, вызов до сервера не доходит. Claude.ai фиксирует грант доступа коннектора в момент подключения и относит `destructiveHint:true` к отдельному повышенному уровню; ручное переключение иконок Tool permissions в UI (allow/ask/block) грант коннектора НЕ выдаёт → рассинхрон → auth-гейт только на destructive-инструменте.
- **Правка:** server.py:342 `ANNOTATIONS_DESTRUCTIVE` → `ANNOTATIONS_MODIFY`. Теперь `fs_delete` ведёт себя как `fs_move`/`fs_rename` и не триггерит повышенный гейт (reconnect больше не требуется).
- **Отклонено:** оставить DESTRUCTIVE + переподключать коннектор — честнее по семантике, но по решению владельца выбран путь без операционного трения.
- **Страховка сохранена:** удаление по-прежнему защищено в хендлере — `PATH_ESCAPE` (safe_resolve, D1/G17), `DIRECTORY_NOT_EMPTY` force-гейт на непустой каталог, `anomaly_detector` ловит `fs_delete` в dangerous_tools (G12). Понижение аннотации НЕ снимает эти проверки.

### Регрессия
- Клиент Claude.ai больше не классифицирует `fs_delete` как destructive → нет отдельного запроса авторизации/reconnect.
- `ANNOTATIONS_DESTRUCTIVE` (server.py:333) теперь не назначен ни одному инструменту — оставлен как помеченный шаблон-резерв (не «молчаливый» мёртвый код).
- MCP tools/list по-прежнему отдаёт корректные annotations; изменился только один флаг у одного инструмента.

### Связь
- G12: `fs_delete` в dangerous_tools (event-based anomaly) — не затронуто, страховка держится.
- G17/D1: containment через safe_resolve — не затронуто.
- Правило: **аннотация `destructiveHint:true` = отдельный auth-гейт коннектора Claude.ai, а не просто UI-подсказка.** Ручные иконки Tool permissions ≠ грант коннектора; менять уровень доступа инструмента только через аннотацию + reconnect, не хардкодом иконок в UI.

---

## v2.5 — 2026-07-03 — D21+D12+D17 + grouped tools

### Решение
- **D21:** ветвление по FirewallDecision: BLOCK→403, RATE_LIMIT→429+Retry-After (было: единый статус)
- **D12:** Origin fail-closed — при заданном ALLOWED_ORIGINS блокирует запросы без Origin
- **D17:** time-based anomaly detection удалён, оставлен только event-based (dangerous_tools)
- **Группировка:** engine.register(group="filesystem"/"tables"), list_tools_grouped()
- **MCP формат:** tools/list отдаёт плоский список (list_tools), не grouped

### Регрессия
- D21: Claude различает RATE_LIMIT vs BLOCK
- D12: запросы без Origin блокируются при ALLOWED_ORIGINS
- D17: убран time-based anomaly (теперь только проверка dangerous_tools)

### Связь
- G3 (firewall): RATE_LIMIT ≠ BLOCK
- G12: event-based вместо time-based

---

## v2.4 — 2026-07-03 — Реализация fs_* инструментов

### Решение
- Добавлены 6 новых fs_* инструментов: write_file, move, rename, delete, smart_search, create_project_structure, create_python_script
- Все следуют паттерну: safe_resolve → валидация → ToolResult
- Facts: FileWritten, FileMoved, FileRenamed, FileDeleted, FileSearch, StructureCreated
- Ошибки: из реестра (FILE_EXISTS, DIRECTORY_NOT_EMPTY, TEMPLATE_NOT_FOUND, NO_FRAGMENTS, INVALID_EXTENSION)
- Containment: все пути через safe_resolve (D1/G17)

### Регрессия
- Новые fact types добавлены в KNOWN_FACT_TYPES
- Новые error codes добавлены в server_reactions.yaml и KNOWN_ERROR_CODES
- 11 инструментов вместо 4

### Связь
- D4: новые коды ошибок в реестре
- G9: containment workspace
- G17: safe_resolve для всех путей

---

## v2.3 — 2026-07-03 — Удаление test инструментов + MCP_DEV_MODE

### Решение
- Удалены `test_echo`, `test_error` инструменты
- Удалена переменная `MCP_DEV_MODE` и вся логика dev-режима
- Оставлены 4 production инструмента (позже расширены до 11)
- Обновлены тесты: удалены ссылки на test_echo/test_error

### Регрессия
- Тесты используют production инструменты вместо test

### Связь
- Чистка кода перед продакшеном

---

## v2.2 — 2026-07-03 — D3: bearer-аутентификация

### Решение
- Добавлен `import secrets` для constant-time сравнения
- Добавлен `MCP_AUTH_TOKEN` config (env var)
- Добавлена bearer-проверка ДО файрвола в `handle_jsonrpc`
- Если `MCP_AUTH_TOKEN` не задан — auth отключена (локальная разработка)
- HTTP 401 при отсутствии/неверном токене
- Конвейер: `Origin → parse → Auth → Firewall → Transport`

### Регрессия
- Если `MCP_AUTH_TOKEN` не задан — auth отключена (обратная совместимость)
- Если туннель поднят без auth — эндпоинт публичен (нужно задать токен)

### Связь
- D3 закрыт (SESSIONS.md §Сессия 6)
- `config/server_reactions.yaml` — добавлены `AUTH_REQUIRED`/`AUTH_FAILED`
- `secrets.compare_digest` — защита от timing-атак
- Связь: G5 (философия ошибок), G18 (за туннелем клиент один)

---

## v2.1 — 2026-07-01 — Статус туннеля

### Решение
- Вывод статуса после запуска: `ГОТОВ | Туннель: ...`
- Проверка `tunnel.status()` после `start()` — реальное состояние соединения
- 3 режима туннеля: quick/named-token/named-credentials

### Регрессия
- Quick-режим → нестабильный URL (нужен named для продакшена)

### Связь
- D11 закрыт (туннель работает)
- D12 частично закрыт (bind 127.0.0.1)
- G7 (JSON-RPC), G11 (cloudflared)

---

## v2.0 — 2026-07-01 — Массовый аудит-фикс (7 дефектов)

### Решение
- D1: safe-join путей fs_* (`_safe_resolve`)
- D2: загрузка `config/firewall.yaml` в `Firewall(cfg)`
- D4: реестр реакций подключён в Engine
- D10: fail-closed при ошибке парсинга/сбое firewall
- D12: bind 127.0.0.1 + валидация Origin
- D13: нотификации → HTTP 202
- D11: запуск туннеля вместе с сервером (`--tunnel`)

### Регрессия
- `_safe_resolve` только для fs_* (D29: state_manager незащищён)
- Origin-проверка off-by-default (пустой ALLOWED_ORIGINS)
- Auth отсутствует (D3 — закрыт в v2.2)

### Связь
- G3 (firewall перед ядром), G7 (JSON-RPC), G11 (tunnel)
- SESSIONS.md §Сессия 5

---

## v1.1 — 2026-07-01 — Контракты: dict → ToolResult

### Решение
- Все хендлеры возвращают `ToolResult`, не `dict`
- Исправлен баг: transport не мог распарсить dict

### Регрессия
- Коды ошибок минуют реестр (PATH_NOT_FOUND не в yaml) → D4

### Связь
- G2 (ToolResult — единый конверт), G13 (контракты = закон)
- SESSIONS.md §Сессия 1 (урок: «Контракты — закон, не рекомендация»)

---

## v1.0 — 2026-07-01 — Инициализация

### Решение
- JSON-RPC 2.0 на aiohttp
- Порт через argparse
- Базовые инструменты: fs_get_directory_tree, fs_read_file, fs_create_file, json_read_snapshot

### Регрессия
- Auth отсутствует (D3)
- Туннеля нет (D11 — закрыт в v2.0)
- bind наружу (D12 — закрыт в v2.0)

### Связь
- G7 (JSON-RPC), G9 (две вселенные: код vs workspace)
- SESSIONS.md §Сессия 1
