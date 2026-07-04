# GitHub референсы — привязка к модулям

**Дата:** 2026-07-03
**Цель:** найти сильные паттерны на GitHub для каждого модуля. Не пишем код — только прорабатываем решения через воркфлоу.
**Исключение:** FFmpeg (нюансы, объём и так велик).

---

## Приоритетные репозитории (ТОП-5 по ценности)

| # | Репозиторий | Stars | Ключевая ценность |
|---|---|---|---|
| 1 | [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | 23.5k | Официальный SDK: контракты, тесты, примеры для ВСЕХ модулей |
| 2 | [PrefectHQ/fastmcp](https://github.com/PrefectHQ/fastmcp) | 26k | Самый популярный фреймворк MCP-серверов (70% используют) |
| 3 | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | 88k | Эталонный filesystem MCP + path-validation |
| 4 | [answerlink/MCP-Workspace-Server](https://github.com/answerlink/MCP-Workspace-Server) | 130 | Virtual paths, Excel tools, multi-tenant, security layers |
| 5 | [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) | 90.2k | Каталог всей экосистемы MCP |

---

## Модуль: Filesystem tools (fs_*)

**Вопросы:** A1–A6, P1–P3, D1–D4 из `questions.md`
**Ключевые D#:** D1 (закрыт), D29 (закрыт, S7)
**Ключевые G#:** G9, G17

### Референсы

| Репозиторий | Паттерн | Что заимствовать |
|---|---|---|
| [modelcontextprotocol/servers → filesystem](https://github.com/modelcontextprotocol/servers) | `path-validation.ts`: `validatePath()` с `fs.realpath()` + `allowed_dirs` check | Safe-join: `Path.resolve()` → `realpath()` → compare against whitelist → block traversal/null bytes |
| [answerlink/MCP-Workspace-Server](https://github.com/answerlink/MCP-Workspace-Server) | `PathValidator` с многослойной защитой: resolve + boundary +双重 protection | Virtual path system: LLM видит `/` root, реальные пути скрыты за session isolation |
| [javillegasna/filesystem](https://github.com/javillegasna/filesystem) | Python-порт filesystem MCP: read/write/edit/list/move/search | Directory restrictions через CLI args + `allowed_dirs`, edit с git-style diff |
| [moaaz01/mcp-file-system-server](https://github.com/moaaz01/mcp-file-system-server) | Sandbox-ориентированный: `--sandbox PATH` как корневая | Hidden file blocking, size limits, dual transport (stdio + Streamable HTTP) |

### Решение (привязка к D29)
**Q:** как защитить state_manager от traversal?
**A (из референсов):** вынести `_safe_resolve` в общий `core/paths.py`. Паттерн из #1: `Path.resolve()` → `realpath()` → compare against `workspace/`. Паттерн из #5: virtual paths — LLM видит условные пути, сервер маппит в реальные. **Наш выбор:** простой safe-join (как в #1), без virtual paths (избыточно для одного workspace).

---

## Модуль: Table primitives (table_*)

**Вопросы:** D1–D4 из `questions.md` §7
**Ключевые G#:** G2, G9

### Референсы

| Репозиторий | Паттерн | Что заимствовать |
|---|---|---|
| [modelcontextprotocol/servers → sqlite](https://github.com/modelcontextprotocol/servers-archived/tree/main/src/sqlite) | Pydantic input validation, whitelisted queries (read-only SELECT vs write), `append-insight` resource pattern | Типизированные запросы, разделение read/write |
| [answerlink/MCP-Workspace-Server](https://github.com/answerlink/MCP-Workspace-Server) | `fs_read` с Excel support (sheet/range), `fs_write` с auto-detection | Формат range: "A1:D100", auto-create xlsx from 2D array |

### Решение
**Q:** как организовать table primitives?
**A:** 5 примитивов (get_column, get_row, set, append, delete) — уже решено. Из референсов: паттерн whitelisted queries (#1) подтверждает наш подход «поведение задаётся конфигом, а не кодом». Range-формат (#2) полезен для `table_get_column` с фильтрацией.

---

## Модуль: Excel engine (excel_*)

**Вопросы:** D1–D4 из `questions.md` §7
**Ключевые G#:** G9

### Референсы

| Репозиторий | Паттерн | Что заимствовать |
|---|---|---|
| [mort-lab/excel-mcp](https://github.com/mort-lab/excel-mcp) | MCP + openpyxl: create, read, edit Excel | Прямая аналогия: CRUD через openpyxl |
| [MaximeSwagel/Excel-MCP](https://github.com/MaximeSwagel/Excel-MCP) | FastMCP + openpyxl: browse, edit, create spreadsheets | FastMCP-декораторы для tool registration |
| [ramvalicharla/xlflow](https://github.com/ramvalicharla/xlflow) | Analytics поверх Excel: reconciliation, forecasting, anomaly detection | Расширение для ANALYTICS листов |
| [walter-flowo/microapple-sheet](https://github.com/walter-flowo/microapple-sheet) | "Never drop cached formulas" — защита структуры | Паттерн для `excel_insert_formula` — не перезаписывать критические формулы |

### Решение
**Q:** как защитить формулы при записи?
**A:** паттерн из #4: `excel_insert_formula` проверяет, не перезаписывает ли критическую формулу. Паттерн из #1: CRUD операции через openpyxl — подтверждает наш 14-инструментный подход. **Наш выбор:** жёсткая граница `excel_*` = структура, `queue` = данные (уже решено).

---

## Модуль: Reactions / Error mapping

**Вопросы:** C6–C17 из `questions.md` §3, R1–R6 из `questions.md` §5
**Ключевые D#:** D4, D27
**Ключевые G#:** G5, G15

### Референсы

| Репозиторий | Паттерн | Что заимствовать |
|---|---|---|
| [langgenius/dify](https://github.com/langgenius/dify) | Agentic workflows: error classification (transient/permanent/recoverable), retry/fallback pipelines | Классификация: transient → retry, permanent → escalate, recoverable → LLM исправит |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | `McpError` + `ErrorData` — стандартная структура ошибок MCP | Стандартный формат для `tool_result.error` |
| [AccelateAI/multi-agent-orchestration](https://github.com/AccelateAI/multi-agent-orchestration) | Supervisor-based error routing между агентами | Паттерн для `ai_recoverable` — Claude как supervisor |
| [microsoft/agent-framework](https://github.com/microsoft/agent-framework) | Structured error handling: retry strategies, error classification | Конкретные retry-стратегии для `server_recoverable` |

### Решение (привязка к D4, D27)
**Q:** как сделать реестр реакций единым источником истины?
**A (из референсов):**
1. **Dify-паттерн:** классифицировать ошибки на transient (retry), permanent (escalate), recoverable (LLM). Это подтверждает наши 5 классов.
2. **MCP SDK:** `ErrorData` = `{code, message}` — стандарт. Наш `ErrorDetail` богаче (recovery, raw_response) — правильно, но `class` не доходит (D27).
3. **Инженерный план:** `code: str` → `Literal` из ключей `server_reactions.yaml` (единый источник). Добавить `reaction_class` в `ErrorDetail`. Провести fs_*/table_* через `Engine._error`.

---

## Модуль: State management

**Вопросы:** P4–P8 из `questions.md` §4
**Ключевые D#:** D9 (закрыт), D24, D28, D29
**Ключевые G#:** G9, G10, G17

### Референсы

| Репозиторий | Паттерн | Что заимствовать |
|---|---|---|
| [MemMachine/MemMachine](https://github.com/MemMachine/MemMachine) | Session/global memory separation: `_SESSION_LOG` = сессия, `project_memory` = долгосрочная | Прямая аналогия с нашим разделением |
| [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | Persistent knowledge graph: атомарные записи + snapshot | Паттерн для `_atomic_write_json` |
| [agent-memory](https://github.com/molchanovartem/agent-memory) | State-based memory: profile + notes, session/global, LLM consolidation | Паттерн для `project_memory.md` |

### Решение
**Q:** как обеспечить целостность данных?
**A:** атомарная запись (temp + fsync + os.replace) — уже реализовано (D9 закрыт). Из референсов: MemMachine подтверждает разделение session/global. **Остаётся:** D24 (facts → _SESSION_LOG не проведены), D29 (traversal через state_manager).

---

## Модуль: Pipeline / Workflow

**Вопросы:** A1–A6 из `questions.md` §1
**Ключевые G#:** G1, G4

### Референсы

| Репозиторий | Паттерн | Что заимствовать |
|---|---|---|
| [langgenius/dify](https://github.com/langgenius/dify) | Visual workflow: entry point → condition-check → step-sequence, reusable nodes | Entry points = condition checks, steps = reusable library |
| [deepset-ai/haystack](https://github.com/deepset-ai/haystack) | Pipeline = DAG компонентов, conditional routing | Паттерн: каждый шаг = компонент с input/output |
| [apache/airflow](https://github.com/apache/airflow) | DAG-оркестрация: sensors (entry points) → operators (steps) | Икона паттерна "entry point + step library" |
| [flyteorg/flyte](https://github.com/flyteorg/flyte) | Entry point → steps с checkpointing и retry | Checkpointing для долгих операций |

### Решение
**Q:** как спроектировать пайплайн?
**A:** паттерн из Dify/Airflow: entry point = sensor (проверяет предусловия), step = operator (выполняет действие). **Наш выбор:** 4 точки входа (cold_start, project_ready, competitor_ready, assets_only) как condition-checks, которые роняют Claude в нужную точку общей библиотеки шагов. Проверено: Haystack/Dify/Airflow используют тот же подход.

---

## Модуль: Media tools (TTS/STT/IMG)

**Вопросы:** A3–A4 из `questions.md` §1
**Ключевые G#:** G4, G5, G16

### Референсы

| Репозиторий | Паттерн | Что заимствовать |
|---|---|---|
| [Hakanai-AI/agent-voice-mcp](https://github.com/Hakanai-AI/agent-voice-mcp) | MCP + Whisper.cpp + Kokoro TTS: file-based speech I/O | Ближайшая аналогия: MCP-native медиа-сервер |
| [wynandw87/claude-code-openai-mcp](https://github.com/wynandw87/claude-code-openai-mcp) | Мульти-инструментный MCP: TTS + STT + IMG в одном сервере | Паттерн: единый сервер → несколько медиа-инструментов |
| [modelscope/FunASR](https://github.com/modelscope/FunASR) | Production STT: OpenAI-compatible API, MCP-server tagged, 170x realtime | Паттерн: production-grade STT с fallback |
| [babula-cpu/stt-server](https://github.com/babula-cpu/stt-server) | WebSocket + pluggable ASR backends | Паттерн: плагинные бэкены → деградация large→medium→base |

### Решение
**Q:** как спроектировать медиа-провайдеры?
**A:** паттерн из #1/#2: единый MCP-сервер → несколько инструментов (TTS/STT/IMG). Паттерн из #4: pluggable backends → деградация модели. **Наш выбор:** LiteLLM как шлюз (TTS/IMG), stable-ts локально (STT), trigger/poll/download для async. Подтверждено референсами.

---

## Модуль: Testing

**Вопросы:** Q1–Q6 из `questions.md` §8
**Ключевые G#:** G8

### Референсы

| Репозиторий | Паттерн | Что заимствовать |
|---|---|---|
| [modelcontextprotocol/python-sdk → tests/](https://github.com/modelcontextprotocol/python-sdk) | pytest + mock transport + client integration | Эталон тестирования MCP-серверов |
| [aryanjp1/mcp-test-framework](https://github.com/aryanjp1/mcp-test-framework) | pytest plugin: schema validation, tool execution, error scenarios | Автоматизация schema validation |
| [jagguvarma15/MCP-lab](https://github.com/jagguvarma15/MCP-lab) | Stress testing + security boundaries: prompt injection через tool descriptions | Паттерн для security-тестов |
| [microsoft/mcp-for-beginners](https://github.com/microsoft/mcp-for-beginners) | Best practices + тестирование MCP | Curriculum для обучения |

### Решение
**Q:** как тестировать MCP-сервер?
**A:** паттерн из #1: pytest + mock transport (без реального сервера) + integration tests (с реальным). Паттерн из #3: security testing через prompt injection. **Наш выбор:** быстрые тесты (удаляемые) + постоянные (security/system) + 5 вопросов перед тестом. Подтверждено.

---

## Модуль: Rate limiting / Firewall

**Вопросы:** S1–S20 из `questions.md` §2
**Ключевые D#:** D6 (закрыт), D7 (закрыт), D14, D15, D16, D17, D18, D19
**Ключевые G#:** G3, G12, G17, G18

### Референсы

| Репозиторий | Паттерн | Что заимствовать |
|---|---|---|
| [hybridindie/comfyui_mcp](https://github.com/hybridindie/comfyui_mcp) | 5 security layers: Workflow Inspector + Path Sanitizer + Rate Limiter + Audit Logger + Selective API Surface | Token-bucket per tool category (workflow: 10/min, read_only: 60/min) |
| [breezykalama/mpesa-mcp-server](https://github.com/breezykalama/mpesa-mcp-server) | Redis rate limiting, approvals, audit logging | Redis-based rate limiting для масштабирования |

### Решение
**Q:** как спроектировать rate limiting?
**A:** паттерн из #1: per-category limits (token-bucket), in-memory. **Наш выбор:** in-process state (G12) — достаточно для одного процесса за туннелем. Но D14 (один IP туннеля) показывает, что per-IP rate limiting бесполезен. **Направление:** per-session/token granularity, не per-IP.

---

## Модуль: Auth / Transport

**Вопросы:** T1–T9, I1–I7 из `questions.md` §6, §9
**Ключевые D#:** D3, D12, D13, D30, D31
**Ключевые G#:** G7, G11, G18

### Референсы

| Репозиторий | Паттерн | Что заимствовать |
|---|---|---|
| [answerlink/MCP-Workspace-Server](https://github.com/answerlink/MCP-Workspace-Server) | Bearer Token auth + multi-tenant session isolation (X-User-ID + X-Chat-ID headers) | App-auth через headers, не через IP |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | Streamable HTTP transport, SSE, stdio | Транспортные опции |
| [kenily/mcp-server-templates](https://github.com/kenily/mcp-server-templates) | Auth + Rate Limiting + monitoring patterns | Шаблон production-ready auth |

### Решение
**Q:** как реализовать auth?
**A:** паттерн из #1: Bearer Token через headers (не IP). Паттерн из #3: auth ДО firewall + rate limiting. **Наш выбор:** `MCP_AUTH_TOKEN` через env, constant-time сравнение (`secrets.compare_digest`), ДО firewall.check. Токен туннеля — через env подпроцесса, НЕ argv (D31).

---

## Общие паттерны для заимствования

| Паттерн | Источник | Наш модуль |
|---|---|---|
| **Safe path validation** | #1 (path-validation.ts), #5 (PathValidator) | fs_*, state_manager (G17) |
| **Token-bucket rate limiting** | #7 (comfyui_mcp) | Firewall (G12) |
| **Pydantic tool schemas** | #2 (SDK), #3 (FastMCP) | Все контракты (G13) |
| **ToolAnnotations** | #1 (servers) | Все инструменты |
| **Virtual path mapping** | #5 (Workspace-Server) | Рассмотреть для G17 |
| **Structured audit logging** | #7 (comfyui_mcp) | Firewall, reactions |
| **Error classification** | Dify, agent-framework | Reactions (G5) |
| **Session/global memory** | MemMachine, agent-memory | State, project_memory (G9) |
| **Pipeline = DAG** | Haystack, Airflow, Dify | Pipeline (G1, G4) |
| **Pluggable backends** | stt-server, FunASR | Providers (G16) |
| **Never drop formulas** | microapple-sheet | Excel engine |
| **pytest + mock transport** | MCP Python SDK | Testing (G8) |

---

## Следующие шаги

1. **Критические (🔴):** D3 закрыт (S6), D29 закрыт (S7), D31 (secret) — паттерн найден, готов к реализации
2. **Важные (🟡):** D14 (IP granularity), D20+D27+D30 (facts/error/class), D4 (реестр кодов) — паттерны подтверждены
3. **Реализация:** каждый паттерн → вопрос из questions.md → решение → SESSIONS.md
