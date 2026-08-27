---
name: reactions-errors
description: Use when working on the REACTIONS / ERRORS system of the video_pipeline_mcp server — the server_reactions.yaml registry (5 classes ai_recoverable/server_recoverable/human_required/integrity/unknown, its codes, message_template, recovery{suggested_tool,suggested_params,reason}), the core/reactions loader+mapper (get_error / DEFAULT fallback), and the ErrorDetail+Recovery contract. Fires on: adding or changing a reaction code, an error path returning raw text instead of a registry code, DEFAULT-fallback correctness (UNKNOWN_ERROR hardcode, dropped reaction_class, ignored message_template), a declared code left unreachable because the call bypasses ctx.safe, unactionable recovery, and reaction-as-contract parity (class and recovery must survive to the MCP CallToolResult boundary — G14/D30). Owns the reaction SYSTEM's design and integrity; a tool that merely emits a code is mcp-developer, hardcoded error strings are anti-hardcode, asserting codes in tests is test-master. Python + Pydantic v2.
license: MIT
allowed-tools: Read, Grep, Glob, Edit, Bash
metadata:
  version: "1.0.0-vpm1"
  domain: reactions
  project: video_pipeline_mcp
  triggers: reaction, reactions, server_reactions.yaml, ErrorDetail, Recovery, error code, reaction_class, error mapping, recovery strategy, DEFAULT fallback, UNKNOWN_ERROR, error handling, error contract
  role: specialist
  scope: build-review
  output-format: report
  related-skills: mcp-developer, code-quality, anti-hardcode, security-reviewer, test-master
---

# Reactions / Errors — video_pipeline_mcp

Владелец **системы реакций и ошибок**: реестр `config/server_reactions.yaml` → читалка/маппер
`core/reactions/reactions.py` → контракт `ErrorDetail`+`Recovery` (`core/contracts/`) → то, что видит Claude
после ошибки. Не «баг вообще» и не «строю инструмент» — а **целостность и дизайн того, как сервер
превращает сбой в структурированную, восстановимую реакцию**.

Стек — **Python + Pydantic v2**. Декларативный принцип проекта: *добавить реакцию = строка в YAML, НЕ код.*

---

## Модель системы (как устроено на самом деле)

```
ошибка в хендлере/провайдере
   → Reactions.get_error(code, raw_message, raw_response)
       ├── code ∈ реестр → ErrorDetail(code, reaction_class=class, message=raw|template, recovery=..., raw_response)
       └── code ∉ реестр → DEFAULT-fallback  ← зона дефектов (см. ниже)
   → ErrorDetail внутри ToolResult(status="error") → MCP-граница → Claude
```

- **5 классов** (`class`): `ai_recoverable` (Claude сам чинит: сверь схему, создай файл), `server_recoverable`,
  `human_required` (нужен человек), `integrity` (нарушена целостность), `unknown`.
- **Реакция** = `{class, message_template, recovery:{suggested_tool, suggested_params, reason}}`.
- **Коды — публичный контракт** для Claude: по ним оркестратор решает следующий шаг. Менять код = менять API.
- **Вход в эту схему — ровно один: наследник `ContractError`** (`core/contracts/errors.py`, поля
  `code`/`message`/`reason`/`suggested_tool`). `ctx.safe` ловит БАЗУ, а не перечень зон: перечень  <!-- снимок ок: поля рецепта — контракт, названы вместе с источником -->
  раньше и был причиной раскола — четыре зоны в него не попали, не имели `suggested_tool` и
  ловились рукописными `except` мимо общего пути. Инвариант «каждое исключение зоны наследует
  базу» проверяет тест в `tests/quick/test_audit_fixes.py`; два исключения из него осознанны:
  подтипы `ValueError` в `core/paths` (свои ветки в `ctx.safe`) и `TunnelError` (подъём сервера).
- **Код есть в ДВУХ источниках** — `config/server_reactions.yaml` и `KNOWN_ERROR_CODES`
  (`core/contracts/error_detail.py`). Добавляешь код — вписывай в оба, иначе `ErrorDetail`
  предупреждает «кода нет в реестре» на штатном отказе. Инвариант держит тест `F100`, не память.

---

## Оси ревью (по чему проверять)

1. **Каждый путь ошибки → код из реестра** 🔴 — никакого `raise`/сырого текста/захардкоженной строки в обход
   `get_error`. Хардкод-код вне YAML = дефект (D2/D4; для самих значений зови `anti-hardcode`). Проверка:
   `grep -rn "ErrorDetail(" core tools server.py` → каждый ли собран через реестр, а не вручную.
2. **DEFAULT-fallback честный** 🔴 — известный дефект F5/D4: ветка «код не в реестре» **хардкодит
   `UNKNOWN_ERROR`** (кода нет в реестре → он сам не резолвится), **не ставит `reaction_class`**, и берёт
   только `DEFAULT.recovery.reason`, **игнорируя `DEFAULT.message_template`**. Правильный fallback: тянуть
   класс/шаблон/recovery из записи `DEFAULT` реестра единообразно с обычной веткой.
3. **`reaction_class` присутствует и осмыслен** 🟠 — не `"unknown"` по умолчанию там, где класс известен;
   класс управляет тем, кинется ли Claude чинить сам или позовёт человека — врать классом опасно.
4. **Recovery actionable** 🟠 — `reason` обязателен и конкретен («сверь параметры со схемой», а не «ошибка»);
   `suggested_tool` должен существовать в `tools/list` (иначе Claude уводит в тупик). Проверка: коды с
   `suggested_tool` → сверить с реальным реестром инструментов.
5. **Reaction-as-contract parity** 🟠 (G14/D30) — `reaction_class`/`recovery`/`code` не должны теряться на
   границе внутренний `ToolResult` → MCP `CallToolResult`. То, что богаче внутри, чем в content — дефект.
   **С появлением студии граница становится ВТОРОЙ, а не другой:** второй клиент — не модель, а
   экран (`20` §0 п. 1). Parity тот же: `code`/`reaction_class`/`recovery` обязаны доехать до
   человека действием, которое он может совершить, а не строкой «что-то пошло не так». Особенно
   код про устаревшую версию строки (`20` §0 п. 8) — это не «ошибка», а предписание перечитать.
6. **`raw_response` не течёт** 🟠 (D23, → `security-reviewer`) — оригинальный ответ провайдера может нести
   ключи/PII; не отдавать его Claude сырьём в `ErrorDetail.raw_response` без нужды.
7. **Стаб кричит кодом** 🟡 (G16) — незавершённое = честный код реакции/`NotImplementedError`, НЕ тихий
   `success` и не выдуманный «ок». (Провайдеры сейчас честны — держать так.)
8. **Классы полны и различимы** 🟡 — каждый новый код попадает в один из 5 классов; если код не лезет ни в
   один — сначала обосновать новый класс (это меняет контракт), не плодить синонимы.

---

## Порядок работы (перед правкой)

> Тип задачи — «новый код реакции» (feature) или «дефект реакций» (bug-fix). Процесс: `the project workflow (memory project-workflow-canonical)`.

1. **Читай ДО кода:** `config/server_reactions.yaml` (какие коды/классы уже есть — не дублируй),
   `core/reactions/reactions.py` (loader/fallback), git-история `core/reactions/`,
   `docs/roadmap/02_findings.md` (F5 и связанные). Словарь уже собран — не переоткрывай.
2. **Добавить реакцию** — строка в YAML: `<CODE>: {class, message_template, recovery}`. Код **эмитить** в
   хендлере через `Reactions.get_error(CODE, ...)`, не собирать `ErrorDetail` руками.
3. **Чинить fallback/маппинг** — правка в `reactions.py`, поведение остальных кодов неизменно.
4. **Тесты** (→ `test-master`): каждый код мапится; recovery присутствует; `suggested_tool` существует;
   DEFAULT-ветка ставит класс/шаблон; parity на MCP-границе. Прогони `tests/quick/` зелёным до и после.
5. **Запись:** решение→факт в commit + `docs/roadmap/_sessions.md`; сквозной паттерн → `G#` в `docs/roadmap/02_findings.md`
   (если реконструируется аудит) и/или `docs/roadmap/02_findings.md`.

---

## Пример находки (жанр проекта)

```markdown
### 🔴 R-D4. DEFAULT-fallback не единообразен с обычной веткой
core/reactions/reactions.py:get_error — ветка «код не в реестре» хардкодит code="UNKNOWN_ERROR"
(нет в server_reactions.yaml), не выставляет reaction_class, игнорит DEFAULT.message_template.
**Почему:** класс управляет поведением Claude (сам чинит vs зовёт человека); без него — деградация
до «unknown» молча. Шаблон DEFAULT задан, но не используется → мёртвая декларация (D2).
**Как чинить:** резолвить DEFAULT как обычную реакцию: class/message_template/recovery из записи DEFAULT.
**Связь:** roadmap F5 · git-история `core/reactions/` · G14 (parity), G15 (реестр вместо магии).
```

## Переиспользованный код тащит ЧУЖУЮ рекавери

Код реакции — это не только класс, но и `recovery`, которую подставляет реестр. Взять «похожий»
код значит отдать клиенту верный класс с советом не по делу, а это хуже отсутствия совета: модель
пойдёт исполнять его.

Проверено на двух случаях S25: `NO_FRAGMENTS` (рекавери зовёт создавать структуру проекта) на
пустой сцене монтажа и `CONTENT_REJECTED` (рекавери про переформулировку промпта модерации) на
варианте, не влезшем в рамку слота. Обоим завели свои коды — `SCENE_EMPTY`, `VARIANT_INCOMPATIBLE`.

**Правило:** перед переиспользованием кода прочитай его `recovery` в `server_reactions.yaml` и
спроси, исполним ли этот совет В ТВОЁМ случае. Нет — заводи свой код (строка в реестре + строка в
`KNOWN_ERROR_CODES`): это дешевле, чем неверный совет на боевом пути.

## Порядок проверок: сначала ЗАПРОС, потом СРЕДА

Отказы внутри одного хендлера конкурируют: сработает первый, и клиент увидит только его.
Поэтому порядок — часть контракта, а не деталь реализации.

Сначала то, что верно независимо от машины: входные данные, формат, объявленность, наличие
объявленного артефакта. Только потом — среда: бинарь в `PATH`, установленный пакет, доступное
устройство. Проверка среды впереди схлопывает все отказы в один на любой машине, где инструмента
нет, — и самый действенный код («формат не поддержан», «весов нет») до клиента не доезжает.

Замер S24, стоивший красного CI: в адаптере распознавания проверка `ffmpeg` стояла перед
проверкой формата и весов. Локально ffmpeg есть — три теста зелёные; у раннера его нет — те же
три отдали `LOCAL_INFERENCE_FAILED` вместо `CONTENT_REJECTED` и `LOCAL_MODEL_MISSING`.

**Тест на это пишется с СИМУЛЯЦИЕЙ отсутствия**, а не в надежде на чужую машину: спрятать бинарь
(подменить `PATH`), заблокировать импорт — и убедиться, что код отказа остался прежним. Приёмка
мутацией: верни проверку среды вперёд — тест обязан покраснеть тем же кодом, что показал CI.

## Constraints

### MUST DO
- Читать `server_reactions.yaml` + `reactions.py` + history реакций до правок; переиспользовать существующие коды/классы.
- Новый код реакции = строка в YAML + эмит через `get_error`; никаких ручных `ErrorDetail`/сырого текста в обход реестра.
- Держать DEFAULT-fallback единообразным с обычной веткой (класс, шаблон, recovery из реестра).
- Recovery.reason конкретный; `suggested_tool` существующий; `reaction_class` осмысленный.
- Тесты на каждый затронутый код + parity; `tests/quick/` зелёные до и после; запись решение→факт в history.

### MUST NOT DO
- Хардкодить код/сообщение ошибки в обход `server_reactions.yaml` (это `anti-hardcode`+D4).
- Терять `reaction_class`/`recovery` на MCP-границе (parity G14/D30).
- Отдавать `raw_response` сырьём при риске утечки ключей/PII (→ `security-reviewer`).
- Тихий `success`/выдуманный «ок» вместо честного кода реакции (G16).
- Дублировать роль `mcp-developer` (строит инструмент) / `test-master` (тесты) / `security-reviewer` (rug-pull, утечки) — направляй туда.

---
_Проектный скил (создан 2026-07-05 по задаче enterprise-roadmap; развилка Q4 — владелец выбрал выделенный скил). Границы vs `mcp-developer`: тот СТРОИТ инструмент и эмитит код; этот владеет ДИЗАЙНОМ и ЦЕЛОСТНОСТЬЮ системы реакций. Источники: `config/server_reactions.yaml`, `core/reactions/reactions.py`, `core/contracts/error_detail.py`; процесс — `the project workflow (memory project-workflow-canonical)`; находки — `docs/roadmap/02_findings.md` (F5)._
