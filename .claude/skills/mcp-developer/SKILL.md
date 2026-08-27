---
name: mcp-developer
description: Use when building, debugging, or extending MCP server tools in the video_pipeline_mcp project — implementing Bounded-Context tool groups under tools/<group>/, wiring the generic core/engine and the config/*.yaml declarations, returning ToolResult/ErrorDetail contracts, emitting server_reactions codes, or working on the cloudflared transport tunnel to Claude AI Web. Python + Pydantic only.
license: MIT
allowed-tools: Read, Grep, Glob, Edit, Write, Bash
metadata:
  author: adapted from https://github.com/Jeffallan/claude-skills (mcp-developer 1.1.0)
  version: "1.1.0-vpm1"
  domain: mcp-architecture
  project: video_pipeline_mcp
  triggers: MCP, MCP tool, tool handler, input_schema, ToolResult, ErrorDetail, server.py, core/engine, transport, tunnel, server_reactions, JSON-RPC, Pydantic contract
  role: specialist
  scope: implementation
  output-format: code
  related-skills: security-reviewer, test-master
---

# MCP Developer — video_pipeline_mcp

Специалист по MCP-инструментам **этого** проекта: инструменты по группам (`tools/<группа>/`), generic-движок (`core/engine`), декларации в `config/*.yaml`, контракты (`core/contracts`), коды реакций (`server_reactions`), туннель к Claude AI Web (`core/transport`).

Стек — **только Python + Pydantic v2**. TypeScript/Zod/npx в этом проекте НЕ используются.

---

## Порядок работы (перед любым изменением)

Процесс здесь **не дублируется** — он один, в памяти `project-workflow-canonical` (типы задач, 5 вопросов, 4 уровня). Домен-специфика MCP:

- **История файла = git**, отдельных `history_*.md` в проекте нет: `git log --follow <файл>`, `git blame`, `git show <sha>`. Решение→факт пишется в **commit-сообщение** (что / почему / возможные регрессии / `F#`) + `docs/roadmap/_sessions.md` (нарратив сессии) + память проекта (кросс-сессия).
- **Что уже находили** — `docs/roadmap/02_findings.md` (словарь `F#`/`D#`/`G#`). Закрытое не переоткрывай.
- **Куда смотреть по областям:** вход `server.py` (монолит распилен) · инструменты `tools/<группа>/` (число групп и инструментов сверяется запуском, а не переписыванием: `ls tools/*/__init__.py | wc -l` — `ls -d tools/*/` соврёт на `__pycache__`) · контракты `core/contracts` · движок `core/engine` · транспорт и туннель `core/transport` · реакции `core/reactions` + `config/server_reactions.yaml` · ID `core/ids` · чтение/запись `core/state` · провайдеры `core/providers`.

---

## Конвенции проекта (жёстко)

Любой MCP-инструмент подчиняется архитектуре: **обёртка → декларация → движок → контракт → реакция**.
⚠️ Звено «декларация → движок» тут ЖЕЛАЕМОЕ, а не сущее: `core/engine` — реестр инструментов,
валидатор схемы и диспетчер, а `core/reactions` — реестр и маппер к нему. Отдельного
«движка реакций» в проекте нет (F83); не ищи его и не пиши так, будто он есть.

- **Контракт результата.** Инструмент ВСЕГДА возвращает `ToolResult` (`core/contracts/tool_result.py`). Ошибка — `ErrorDetail` внутри `ToolResult`, НЕ исключение наружу и НЕ сырой текст. Факты — `Fact`, статус — `TaskStatus`.
- **Декларативность — цель, а не факт. Проверь глазами, прежде чем поверить.** `config/ops/` **больше нет вовсе** (слой снят S22, пустой каталог снесён S24 по F83 — он читался как «слой есть, просто не заполнен»), `core/engine` generic и почти никем не зовётся: логика инструментов живёт в `tools/<группа>/`. Новый инструмент кладётся туда — не в несуществующий ops-реестр. Заводить `config/ops/*.yaml` — отдельное решение, а не «как принято»; пока его нет, «толстая обёртка» здесь норма, и дефект качества — только **недокументированное** дублирование логики между группами. Декларативность реально работает в другом месте: `config/*.yaml` (провайдеры, реакции, firewall, шаблоны) — вот там значение в коде вместо декларации это дефект (`anti-hardcode`).
- **Валидация входа.** `params` проверяются против `input_schema` инструмента (Pydantic + `jsonschema`) ДО исполнения. Нет схемы — нет инструмента. Схема отвечает не только «какие поля»: у КАЖДОГО свойства обязаны быть объявленный тип (`type`/`enum`/`anyOf`) и непустой `description` — клиент здесь LLM, и свойство без описания это потерянный контракт (`F98`, инварианты в `test_tools_inventory`).
- **🔴 Каждый вызов ядра — через `ctx.safe`, иначе объявленный код отказа не доедет до клиента.** Ядро кидает исключения-наследники `ContractError` (`core/contracts/errors.py`: `code`, `message`, `reason`, `suggested_tool`), и `ctx.safe` ловит **базу**, превращая их в `ErrorDetail` с кодом из реестра. **Новая зона обязана наследовать `ContractError`** — иначе её отказ приезжает клиенту как `INTERNAL_ERROR`, без кода, класса и рецепта; инвариант держит проверка в `tests/quick/test_audit_fixes.py`, не дисциплина. Осознанные исключения из правила: `PathEscapeError`/`SecretAccessError` (`core/paths`) — подтипы `ValueError` с отдельными ветками в `ctx.safe`, и `TunnelError` (подъём сервера, до клиента не доходит). Голое исключение из хендлера ловит общий catch движка и отдаёт `INTERNAL_ERROR`/`human_required` — то есть объявленный `ai_recoverable` с готовым рецептом подменяется на «нужна диагностика человеком». Дефект незаметен обычным тестом: он воспроизводится только через `engine.call(...)`, а не прямым вызовом хендлера (`F95`). Пиши `ok, res = ctx.safe(lambda: …)` / `if not ok: return res` даже там, где «упасть не может».  <!-- снимок ок: поля контракта названы рядом с файлом, который их задаёт -->
- **Новый тип факта заводится в `KNOWN_FACT_TYPES`** (`core/contracts/fact.py`). Не внесённый тип даёт `UserWarning`, который в тестах поднят до ошибки (D25) — то есть узнаешь ты об этом падением набора, а не в ревью.
- **Пустое в конверте — ОТСУТСТВИЕ поля, а не `null`.** `structuredContent` по схеме спеки — объект; `"structuredContent": None` формально ломает провод, и наши тесты этого не видят, потому что проверяют факты, а не соответствие схеме (`F97`). То же правило для любого нового поля ответа.
- **Реакции.** Любой ответ маппится на `server_reactions` (`core/reactions/`): словарь классов (какие есть сейчас — `grep -h '^  class:' config/server_reactions.yaml | sort -u`); у записи поля `class` / `message_template` / `recovery`. Не выдумывай коды и не переписывай сюда их число: бери и считай в `config/server_reactions.yaml`. Класс из словаря, которым не пользуется ни один код, — это норма, а не дефект.  <!-- снимок ок: поля записи реестра — форма контракта, названа вместе с файлом-источником -->
- **Безопасность.** Вход проходит `core/firewall` (см. `config/firewall.yaml`). Инструмент не обходит firewall. Детали угроз — `docs/roadmap/06_threat_catalog.md`. Security-ревью — скил `security-reviewer`.
- **Bounded Context.** Инструмент живёт в своём `tools/{filesystem,tables,excel_engine,media,video}` и не лезет в чужой контекст.
- **Транспорт.** Связь с Claude AI Web — через `core/transport` + `tunnel.py` (cloudflared, `config/tunnel.yaml`). Синхронный код не мешать с async-транспортом.
- **Размещение (свод — memory `project-rules`, `~/.claude/projects/-home-admin-projects-video-pipeline-mcp/memory/project-rules.md`):** две вселенные — код/конфиг в дереве сервера (git), данные только в `workspace/` (НЕ git); `config/` = декларации, не логика; логика в `core/`, обёртки в `tools/`, тесты только в `tests/` по структуре. Сверяйся с матрицей §2 перед созданием файла.

---

## Подсистема структуры: правила, которые иначе выясняются отладкой

Самая нагруженная область (`tools/structure/`, `core/ids/`, `core/engine/template_engine.py`).

- **Адрес узла считает ОДИН вычислитель** — `TemplateEngine.address_for(type, parent_type, parent_path,
  ancestors)` поверх `fill_container`. Не считай путь заново в новом инструменте: правило §4 живёт в
  шаблонах (`container: "competitors/{parent:channel}/"`), а не в коде (`F27`).
- **Якорь группировки берётся из декларации** — `Taxonomy.anchor_type(node_type)` читает
  `role: owner_channel` у предка. Строка `"channel"` в коде — дефект, а не «и так понятно».
- **Имя, попавшее в ПУТЬ, обязано стать связью.** Сгруппировал каталог по имени канала — проставь
  этот канал в `parent_ids`, иначе ФС и реестр расскажут о паре разное (`F101`).
- **Кандидаты ищутся в СВОЕЙ ветке.** Имена в иерархии повторяются: `find(type="channel")` по всей
  области предложит канал из чужой ниши (`F102`). Ограничивай поддеревом родителя-контейнера.
- **Создание вложенного типа требует ЗАРЕГИСТРИРОВАННОЙ цепочки предков**, а не просто существующих
  каталогов: иначе `CHAIN_UNRESOLVED`. Сценарий «создать канал в готовом дереве» начинается с того,
  что дерево создано инструментами, а не `mkdir`.
- **ORPHAN-политика намеренно уже объявления предков** (`REQUIRED_PARENT_TYPE` — единственный
  источник, решение владельца `F99`): висящим объявляется только тот, кого без родителя не найти.
- **Пакетная операция откатывается целиком.** `structure_reconcile` — образец: план до действия,
  неоднозначность возвращается клиенту (`needs_decision`), падение на любом шаге откатывает
  переносы в обратном порядке и снимает проставленные связи.

## Локальные провайдеры: три правила, которые иначе выясняются отладкой

**Новая возможность подключается ДЕКЛАРАЦИЕЙ, а не новым инструментом.** Группа `media` уже несёт
родовые шесть: `media_models` (что стоит и что можно поставить), `media_model_install` (фоновая
установка), `media_install_status` (ход загрузки), `media_provider_status` (кто исполняет и совет),
`media_generate`, `media_runner`. Новый вид ресурса оживает пятью строками в `config/providers.yaml`
(`adapters.by_provider` · `resources.by_resource` — файл или текст на входе · `assets.by_resource` —
куда лечь · `local.sources.<вид>` — откуда ставить веса · `local.runner.by_resource`). Написать
седьмой инструмент под свой вид — продублировать все шесть.

**Формат весов решает выбор БИБЛИОТЕКИ, а не только файла.** `install.deny_suffixes` запрещает
`.pt`/`.bin`/`.ckpt`: это pickle, его загрузка исполняет код из файла. Поэтому библиотека, чей
рекомендованный путь ведёт к таким весам, нам не подходит целиком — искать надо ту, что читает
safetensors/onnx, а не способ обойти запрет. Проверять до выбора, а не после (регламент —
`dependency-hygiene`).

**Чужой текст возвращается ФАЙЛОМ, а не в `meta`.** Конверт провенанса (`as_untrusted`, S3)
накладывает `fs_read` при чтении из рабочей области; строка, положенная в `MediaOutcome.meta`,
уезжает в `ToolResult.data` мимо конверта. Транскрипт, распознанный текст, содержимое чужого
документа — всё это адаптер кладёт на диск и отдаёт путь.

## Значение из книги, уезжающее в ФАЙЛ или КОМАНДУ

Ячейку заполняет ИИ или человек, а сервер кладёт её в питон-файл, строку фильтров ffmpeg или имя
файла. Там она перестаёт быть данными: тройная кавычка закрывает докстринг (
описание скрипта становилось исполняемым кодом), запятая делит фильтрограф надвое, слэш уводит файл
из объявленного каталога.

Три приёма, все дешёвые:
- **литерал — через `repr`**, а не подстановкой в кавычки: враждебный текст доезжает данными;
- **проза — чисткой** (кавычки, слэши, переводы строк) перед вставкой в докстринг или комментарий;
- **имя файла — только из безопасных символов**: `re.sub(r"[^A-Za-z0-9_-]+", "_", ...)`.

Проверяется ЗАПУСКОМ враждебного значения: сгенерировать файл, исполнить его и убедиться, что
побочного действия не случилось, — а не чтением кода подстановки.

## Core Workflow (под архитектуру проекта)

1. **Analyze** — какой Bounded Context, какая операция, какой контракт на выходе.
2. **Declare** — объявить `input_schema` инструмента и проверить антидубль (нет ли уже такого инструмента/операции в соседней группе).
3. **Wrap** — тонкая обёртка в `tools/…`: валидация `params` → вызов `core/engine` → возврат `ToolResult`.
4. **React** — смаппить исходы на коды `server_reactions`.
5. **Test** — прогнать протокол-совместимость и контракты. **Feedback loop:** валидация схемы упала → смотреть Pydantic-ошибку → чинить схему/декларацию → перезапуск. Малформед-ответ → проверить сериализацию `ToolResult` в транспорте → чинить → ретест. (Тест-стратегия — скил `test-master` + `docs/roadmap/03_testing_plan.md`.)
6. **Record** — решение→факт в commit-сообщение + `docs/roadmap/_sessions.md`; новая находка → `F#` в `docs/roadmap/02_findings.md`.

---

## Reference Guide

| Тема | Референс | Когда грузить |
|---|---|---|
| Протокол MCP | `references/protocol.md` | типы сообщений, lifecycle, JSON-RPC 2.0 |
| Python SDK | `references/python-sdk.md` | паттерны серверов/клиентов на Python |
| Tools | `references/tools.md` | определения инструментов, схемы, исполнение |
| Resources | `references/resources.md` | ресурс-провайдеры, URI, шаблоны |

Проектные детали читаются из кода и `docs/roadmap/` (`11_system_requirements.md`, `01_master_roadmap.md`); реестр реакций — `config/server_reactions.yaml`; история — git.

---

## Minimal Example — инструмент по конвенциям проекта

```python
# tools/<context>/<name>.py — тонкая обёртка (Bounded Context)
from core.contracts import ToolResult, ErrorDetail
from core.engine import engine
from core.reactions import react

async def fs_read(params: dict) -> ToolResult:
    """Тонкая обёртка: валидация params → engine → ToolResult.
    Логика группы — в tools/filesystem/, декларации — в config/*.yaml.
    """
    # 1) валидация params против input_schema (Pydantic + jsonschema) — до исполнения
    # 2) исполнение через декларативный движок
    result = await engine.run("filesystem", "fs_read", params)   # либо прямая реализация группы
    # 3) исход → код server_reactions; ошибка → ErrorDetail внутри ToolResult
    return react(result)   # ToolResult с корректным кодом реакции
```

**Ожидаемый поток вызова (JSON-RPC 2.0):**
```
Client → {"method":"tools/call","params":{"name":"fs_read","arguments":{...}}}
Server → {"result": <ToolResult, сериализованный, с кодом реакции>}
```

---

## Constraints

### MUST DO
- Возвращать `ToolResult`; ошибки — `ErrorDetail` внутри него.
- Валидировать все `params` против `input_schema` (Pydantic + jsonschema) до исполнения.
- Держать значения и правила в `config/*.yaml`, а не в коде; логику группы — в `tools/<группа>/`, не размазывать по соседям.
- Маппить исходы на коды `server_reactions` из `config/server_reactions.yaml`.
- Пропускать вход через `core/firewall`.
- Проходить «Порядок работы» и писать решение→факт в commit + `docs/roadmap/_sessions.md`.
- Проверять протокол-совместимость (JSON-RPC 2.0) и сериализацию `ToolResult`.

### MUST NOT DO
- Дублировать логику между группами вместо общего места в `core/`.
- Кидать сырые исключения/текст клиенту вместо `ErrorDetail`.
- Выдумывать коды реакций мимо `server_reactions.yaml`.
- Обходить `core/firewall`.
- Смешивать sync-код с async-транспортом.
- Хардкодить креды/секреты (см. скил `security-reviewer` / `insecure-defaults`).
- Лезть из одного Bounded Context в другой.
- Начинать правку без git-истории файла и без `docs/roadmap/02_findings.md`.

---
_Адаптировано из Jeffallan/claude-skills · mcp-developer 1.1.0 (MIT). TypeScript-часть удалена: проект — Python-only. Процесс-первоисточник: `the project workflow (memory project-workflow-canonical)`._
