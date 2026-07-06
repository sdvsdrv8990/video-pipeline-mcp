# 04 — GitHub-решения под нереализованные системы

> Кандидаты для «не изобретать заново». **Иерархия доверия** (из skill-curator): официальная дока →
> зрелые/звёздные репо → случайный gist. **Подсмотреть ≠ вставить**: берём паттерн, переписываем под наш
> стиль/безопасность (Pydantic v2, ToolResult-контракт, Russian-comments).
> Сессия 1 = **скелет-список кандидатов**; глубокая per-repo оценка + план адаптации — отдельным проходом (по воркстриму).

## Продукт (ось P)

| Система | Кандидат-направление | Что взять | → |
|---|---|---|---|
| P1 FFmpeg | внешний FFmpeg MCP-сервер (проект уже проектировался как адаптер к нему) | протокол trigger/poll/download; НЕ тащить рендер к себе | P1 |
| P1 FFmpeg (fallback) | `ffmpeg-python` / прямой subprocess-обёртка | если внешний MCP не готов — локальный рендер за адаптером | P1 |
| P2/P4 TTS/IMG | **LiteLLM** (уже в дизайне) — единый прокси к облачным провайдерам | routing/fallback-цепочки, retry, стоимость | P2, P4, A4 |
| P3 STT | **stable-ts** / faster-whisper (локально) | локальная транскрибация; device-выбор в конфиг (не хардкод cuda, F10) | P3 |
| P5/P6 Pipeline | паттерны step-оркестрации (напр. лёгкие DAG-раннеры) — идея, не тяжёлый фреймворк | контракт «шаг = чистая функция над ToolResult» | P5, P6 |

## Инфра (ось I) — тут решения зрелые и стандартные

| Система | Кандидат | Что взять | → |
|---|---|---|---|
| I2 Packaging | `pyproject.toml` (PEP 621) + `uv`/`pip-tools` | пиннинг, extras, entry-points | I2 |
| I3 CI/CD | GitHub Actions matrix | lint+mypy+pytest+coverage; security: `bandit`, `gitleaks`, `pip-audit` | I3 |
| I4 Lint/типы | `ruff` (линт+формат) + `mypy` + `pre-commit` | конфиги под наш стиль | I4 |
| I5 Observability | `structlog` + Prometheus client / OpenTelemetry | структурные логи, метрики, `/health`, trace-id по запросу | I5 |
| I6 Auth/secrets | app-level токен (за туннелем) + `python-dotenv`/vault-паттерн | заголовок-аутентификация MCP; секреты вне репо | I6 |

## Скилы (инструментарий разработки)

| Потребность | Кандидат-репо (подсмотреть) | Решено |
|---|---|---|
| Навигация по коду | — | нативные Grep/Glob/Explore/LSP; внешний скил = дубль (см. сессию про чистку) |
| Реакции/ошибки как система | наш `server_reactions.yaml`-подход + MCP error-handling best-practices | создаётся выделенный скил (Сессия 1) |
| Code hygiene с ИИ | наш guard «таски в код» + code-quality/project-conventions | покрыто |

## MVP-пул готовых решений (после обмера — ступень B; БЕЗ ffmpeg)

> Консолидирует кандидатов из этого файла + `08 §4` + `11` (R-#-кандидаты) в **один пул под первый зрелый
> MVP**. Цель MVP: продукт-петля видео-препродакшена работает e2e на зрелом уровне **до рендера** — структура/
> таблицы/патерны-уникальность + генерация media-ассетов (tts/stt/img). **FFmpeg-рендер ВНЕ MVP** (P1 — на
> серьёзном продакшен-уровне, тогда же в оценку зрелости). Порядок = путь к L3 из `11`.
>
> Правило: **подсмотреть паттерн → переписать под наш контракт/стиль → тесты** (§ниже). Deep per-repo research
> (WebFetch офиц.дока→репо) — в момент адаптации воркстрима, не заранее.

| # | Воркстрим | R-# | Готовое решение (адаптировать) | Поднимает | В MVP? |
|---|---|---|---|---|---|
| 1 | **I4** линт/типы | R-DECL2/3 | `ruff`+`mypy`+`pre-commit` (конфиг под стиль) — закрывает static F38/F44/F45/F10 | DIM-13/14 | ✅ |
| 2 | **I3** CI | R-TEST | GitHub Actions matrix: ruff+mypy+pytest+`bandit`+`gitleaks`+`pip-audit`; firewall/tunnel=integration→skip | DIM-6/8 | ✅ (гейт под C-тесты) |
| 3 | **A2** распил монолита | R-ARCH1 | layout `github/github-mcp-server` (структура групп `tools/<g>/`) — паттерн, не копия | DIM-13/15 | ✅ |
| 4 | **A3** ops-слой | R-ARCH2 | декларативные tool-манифесты MCP SDK → `config/ops/*.yaml` заменяет inline спек-списки | DIM-14 | ✅ |
| 5 | **A6** ядро реакций | R-CONTRACT1/2 | НЕТ внешнего (наш `server_reactions.yaml`); паттерн «errors-as-data» + MCP error-handling — все пути через `get_error` | DIM-10 | ✅ (закрывает F43/F5/F40) |
| 6 | **A5** харден search | R-DECL1 | `rapidfuzz`/`rank-bm25` для relevance (F31); фикс F38–F42 — наш код | DIM-6 | ✅ |
| 7 | **A-tables** формулы | R-PROD2 | `formulas`/`pycel` (реальный пересчёт → убить театр F29) + `table_materializer` (loader F30) | DIM-12 | ✅ (ядро продукта) |
| 8 | **A7** уникальность | R-PROD3 | `datasketch`(MinHash)/`rapidfuzz` — n-gram/shingling локально (скрипт по `read.json`, `10 §2`) | DIM-12 | ✅ |
| 9 | **P3/P2/P4** media | R-PROD1 | **stable-ts**/faster-whisper (STT локально, device в конфиг) · **LiteLLM** (TTS/IMG прокси, routing/fallback) | DIM-12 | ✅ (генерация ассетов) |
| 10 | **I5** observability | R-CFG/sec | `structlog`+`/health`+OpenTelemetry + audit-trail | DIM-7 | ✅ (для «зрелого») |
| 11 | **I6** auth | R-AUTH | MCP SDK OAuth 2.1 Resource Server (из `06`/M3) → `core/auth/` | DIM-2 | ✅ (за туннелем app-auth) |
| 12 | **DIM-1** conformance | R-CONTRACT3 | `modelcontextprotocol/conformance` как CI-гейт | DIM-1 | 🟡 желательно |
| — | **P1** FFmpeg | R-PROD1 | внешний FFmpeg-MCP / `ffmpeg-python` | DIM-12 | ❌ **ВНЕ MVP** (продакшен-уровень, последним) |

**Безопасность (R-SEC-IN/OUT):** решения и паттерны фиксов — **из `06`**, GitHub повторно НЕ ищем (правило `08 §6 B`).

## Правило адаптации

1. Официальная дока библиотеки (Context7/сайт) → 2. звёздный референс-репо → 3. переписать под наш контракт/стиль → 4. тесты (honest-stub → реальные) → 5. цитировать источник в `history_*.md`. **Никакого слепого копипаста и скрытого поведения** (principle of least surprise).
