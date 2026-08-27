---
name: code-navigation
description: Use when you need to FIND something in the video_pipeline_mcp repository before changing it — which file implements a tool, where a config value is actually read, who calls a function, when a line appeared and why, whether a claim in the docs still matches disk. Gives the entry-point map (server.py, 8 tool groups, core subsystems, config declarations, the roadmap registries), grep and git recipes tuned to THIS repo, and the rule that a statement is verified by RUNNING it, not by reading a neighbouring document. Locating and reading only; where to PUT a new file is project-conventions, judging the code you found is code-quality, diagnosing a failure is systematic-debugging.
allowed-tools: Read, Grep, Glob, Bash
metadata:
  version: "1.0.0-vpm1"
  domain: navigation
  project: video_pipeline_mcp
  role: specialist
  scope: read
  related-skills: project-conventions, code-quality, systematic-debugging, anti-hardcode
---

# Code Navigation — video_pipeline_mcp

Найти нужное место за минуту, а не прочитать полрепозитория. Скилл про **поиск и чтение**;
куда класть новое — `project-conventions`, как судить найденное — `code-quality`.

Счётных величин (сколько инструментов, групп, строк) в этом скиле нет намеренно: они
устаревают молча. Считай на месте — рецептом, проверенным на этом репозитории:
группы `ls tools/*/__init__.py | wc -l` (не `ls -d tools/*/` — он считает `__pycache__`),
инструменты `len(json.load(open('tests/quick/tools_inventory.golden.json')))`.

---

## Куда идти по симптому

| Вопрос | Первый шаг |
|---|---|
| «где инструмент `X`?» | `grep -rn "name=\"X\"" tools/` → группа; хендлер рядом, в том же `__init__.py` |
| «какие вообще есть инструменты?» | `tests/quick/tools_inventory.golden.json` — весь манифест одним файлом |
| «откуда это значение?» | `grep -rn "<значение>" config/*.yaml` — у нас правила живут в декларациях |
| «читает ли кто эту декларацию?» | `grep -rn "<ключ>" --include=*.py .` → **ноль вызывающих = мёртвая декларация** (F99) |
| «какой код ошибки тут возможен?» | `config/server_reactions.yaml` + `KNOWN_ERROR_CODES` в `core/contracts/error_detail.py` |
| «какой КОМПОНЕНТ рисует вот это место экрана?» | атрибут `data-component` на примитиве (`20` §4б) — читается прямо из DOM, работает и в продакшене. Карты студии здесь пока нет: фронтенд-кода в проекте ноль файлов, каталог решением `20` §0 п. 4 объявлен, но не назван — не гадай имя, спроси |
| «кто вызывает функцию?» | `grep -rn "имя(" --include=*.py .` — и смотри, не только ли тесты (F86) |
| «когда и зачем появилась строка?» | `git log -S"<строка>" --oneline` · `git blame -L n,m <файл>` · `git show <sha>` |
| «это уже находили?» | `docs/roadmap/02_findings.md` (`F#`/`D#`/`G#`); закрытые зачёркнуты, счёт — в шапке файла, а не в этом скиле |
| «есть ли на это тест?» | `tests/CATALOG.md` — зоны ответственности + реестр мутаций `M#` |
| «что решали в прошлой сессии?» | `docs/roadmap/_sessions.md` (порядок записей смешанный — ищи `### Сессия N`) |
| «на чём остановился проход?» | `docs/roadmap/18_full_pass.md` — первый пункт со статусом ⬜/🔨 |

## Карта в одну экранную высоту

```
server.py              вход, сборка: create_server() → engine + transport + firewall
tools/<группа>/        8 групп: excel · filesystem · media · memory · search ·
                       structure · tables · uniqueness — обёртки + engine.register
core/engine/           реестр инструментов, валидатор схемы, диспетчер + TemplateEngine
core/contracts/        ToolResult · ErrorDetail · Fact (KNOWN_FACT_TYPES) · TaskStatus
                       ContractError (errors.py) — база ВСЕХ исключений зон ядра
core/reactions/        загрузчик реестра реакций (сам реестр — config/server_reactions.yaml)
core/ids/              Taxonomy (читает шаблоны) · LinkRegistry · IDGenerator · ChainResolver
core/firewall/         правила входа; порядок: IP → rate → injection → anomaly
core/transport/        HTTP/JSON-RPC + cloudflared tunnel
core/{state,tables,excel,search,providers,runner,uniqueness}/  предметные подсистемы
config/*.yaml          ПРАВИЛА: firewall · server_reactions · providers · model_specs ·
                       recommendations · uniqueness · media_tasks + templates/
workspace/             ДАННЫЕ пользователя. Кода тут нет, и класть его сюда нельзя
```

Подробная матрица размещения — memory `project-rules`; здесь только «куда смотреть».

---

## Приёмы, которые экономят время именно тут

**Ищи объявление, а не строку.** Значения у нас приходят из `config/*.yaml`. Поиск литерала по
`.py` часто даёт ноль при живом поведении — значит, правило в декларации. И наоборот: нашёл
литерал в коде — проверь, нет ли рядом декларации о том же (два источника правды = дефект).

**У декларации ищи ЧИТАТЕЛЯ.** Объявление без вызывающих не работает, сколько бы его ни правили.
`grep -rn "<ключ>" --include=*.py .` — только определение в списке = находка, а не «всё в порядке».

**Вызывающие только в тестах — это мёртвый механизм** (S24/F86). Отфильтруй: `grep -rn "X(" --include=*.py . | grep -v "^tests/"`.

**Читай контракт с проводом, а не догадывайся.** У живого сервера структурная часть ответа лежит
в `envelope["result"]["structuredContent"]`, уровнем выше её нет. Проверяется дампом конверта в
харнессе (`tests/harness/`, `with live_server()`), не рассуждением.

**История — git, отдельных `history_*.md` в проекте нет.** `git log --follow <файл>` показывает
переезды; `git log -S"<строка>"` находит коммит, где значение появилось или исчезло.

**Команда замера тоже врёт, если её не выбрать.** Три ловушки, каждая уже давала неверное число:

- `ls -d tools/*/` считает `__pycache__` за группу инструментов — отсекай `| grep -v __pycache__`.
  Тот же мусор портит любой обход каталогов ядра.
- **Инструменты считаются по эталону, а не по `grep 'async def'`:** в группах есть внутренние
  хелперы-корутины, и они не зарегистрированы. Источник —
  `tests/quick/tools_inventory.golden.json`, он же под тестом.
- **`bandit` в хвосте печатает МЕТРИКИ, а не отчёт.** Строки `High/Medium` там — счёт по всей базе
  вместе с уровнями ниже порога и с колонкой confidence. Вердикт — длина `results` в JSON
  (`-f json -o <файл>`) и exit-код, а не последние строки вывода.

Общее правило: прежде чем цитировать число, спроси себя, что именно посчитала команда. Каталог,
попавший в счёт, и хелпер, принятый за инструмент, дают правдоподобное число — и оно расходится
с диском ровно так же, как выдуманное.

**Утверждение документа проверяется ЗАПУСКОМ.** Это правило прохода S24 и оно окупилось трижды:
доки говорили «12 групп» при восьми, «стабы» при мёртвом коде, «слой снят» при живом каталоге. <!-- снимок ок: цитата ошибки доков, число и есть урок -->
Порядок: прочитал утверждение → `ls`/`grep`/прогон → и только потом опираешься.

---

## Двери, через которые сервер настраивается снаружи

Знать их нужно раньше, чем начнёшь искать «где это захардкожено»: часть поведения задаётся не кодом.

| Переменная | Что меняет | Где читается |
|---|---|---|
| `MCP_WORKSPACE` | рабочая область (данные) | `server.py`, рядом с `BASE_PATH` |
| `MCP_CONFIG` | каталог деклараций целиком | там же; так conformance идёт по копии боевого конфига |
| `MCP_AUTH_TOKEN` | ключ (иначе сервер выпускает себе сам) | `core/auth.py` |
| `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` | кто вправе называться нашим хостом / источником | `server.py`, проверка `Host` идёт ПЕРВОЙ |
| `MCP_HOST` / `MCP_PORT` | адрес слушателя | `server.py` |
| `MCP_ALLOW_NO_AUTH` | 🟠 калитка `F74`, помечена на снос — в тестах не использовать | `server.py` |

Тестовая инфраструктура: `tests/harness/` (живой сервер, JSON-RPC, консоль, прокси с ключом),
`tests/test_suites.py` (раннер: новый `tests/**/test_*.py` попадает в гейт сам).

## Чего НЕ делать

- **Читать файл целиком, когда хватает `grep -n`.** Контекст — общий ресурс; сотни строк ради
  одной сигнатуры вытесняют то, что понадобится через ход.
- **Верить докстрингу и шапке модуля больше, чем коду.** Шапка описывает намерение автора на
  момент написания; сторож `vpm-comment-guard.py` ограничивает их длину именно потому, что
  длинная шапка расходится с кодом молча.
- **Считать `grep` без совпадений доказательством отсутствия.** Проверь регистр, кириллицу vs
  латиницу в имени, перенос строки внутри вызова, генерацию имени в цикле (`engine.register`
  часто вызывается в цикле — искать по литералу имени бесполезно).
- **Начинать правку, не заглянув в `02_findings.md`.** Половина «новых» дефектов уже описана,
  часть — закрыта осознанно, и переоткрывать их значит спорить с решением владельца.

---

## Constraints

### MUST DO
- Начинать с точки входа по таблице «куда идти», а не с чтения подряд.
- У любой декларации проверять читателя, у любой функции — вызывающих вне тестов.
- Проверять утверждения документов запуском (`ls`/`grep`/прогон), а не соседним документом.
- Смотреть `02_findings.md` и `tests/CATALOG.md` до вывода «это новое».
- Историю брать из git (`log --follow`/`blame`/`log -S`), а не из шапок файлов.

### MUST NOT DO
- Читать целиком то, что находится точечным поиском.
- Выдавать пустой `grep` за отсутствие механизма, не проверив форму записи.
- Дублировать роли: размещение — `project-conventions`, оценка качества — `code-quality`,
  диагностика отказа — `systematic-debugging`, значения-хардкод — `anti-hardcode`.

---
_Проектный скил (S24). Числа и пути проверены запуском в момент написания; при расхождении верь
диску и правь скил — систематически этим занимается сиблинг `docs-governance` (роли документов,
один хозяин факта, зона снятого). Карта размещения — memory `project-rules`; процесс — memory
`project-workflow-canonical`._
