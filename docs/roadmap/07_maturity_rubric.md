# 07 — Рубрика зрелости MCP-сервера + честная само-оценка

> Большой список требований, по которому МОЖНО ЧЕСТНО судить, на какой стадии репозиторий, и строить карту развития. Собран из сильных источников (официальный MCP + отраслевые чеклисты, ссылки [M#] внизу), спроецирован на `video_pipeline_mcp`. Это «линейка», а `01_master_roadmap.md` — маршрут; каждое требование привязано к воркстриму/находке.
>
> **Честность — принцип.** Ставим уровень по ДОКАЗАННОМУ состоянию (как аудит: не «есть auth», а «есть OAuth 2.1 Resource Server с per-server scoped token»). Ноль баллов там, где механизма нет, даже если «запланировано».
>
> **Расширение (Сессия 14):** (1) добавлен блок **DIM-13…16 — внутренняя инженерная дисциплина**
> (проектные «паттерны»-скилы: качество кода, анти-хардкод, безопасность-как-паттерн, конвенции/структура).
> Рубрика была externally-sourced (MCP/OWASP) — зрелость ЭТОГО репо меряется и его собственными планками
> (`D#/G#`). (2) **FFmpeg/видео-рендер ИСКЛЮЧЁН из оценки зрелости пока** (указание владельца): у внешней
> MCP-интеграции особенности → честно оценить нельзя, оставляем **на последок**, после работ ранней
> стадии зрелости. `DIM-12` меряем по data/pattern-поверхности, не по видео-рендеру. (3) учтён воркстрим
> **A7** (патерн/уникальность, `10_*`) в карте развития.

## Уровни зрелости (L0–L4)

| L | Имя | Смысл |
|---|---|---|
| **L0** | Прототип/демо | работает у автора, руками; официальные reference-серверы — это L0 by design [M1] |
| **L1** | Alpha | работает локально, контракт есть, но без hardening/auth/CI |
| **L2** | Beta | контракты+тесты+packaging; дыры в auth/observability/security-покрытии |
| **L3** | Production-candidate | OAuth-auth, CI, observability, security-покрытие обеих сторон, экон-контейнмент |
| **L4** | Enterprise | conformance-проходит, полный audit-trail, supply-chain, quotas/budgets, зрелый продукт |

## Требования по измерениям (D-DIM) + наш уровень

### DIM-1 — Протокол / conformance
- [ ] Проходит `modelcontextprotocol/conformance` [M4]
- [ ] Строгое соответствие схем инструментов (MCP Inspector clean) [M6]
- [ ] Parity: внутренний `ToolResult` не богаче MCP `CallToolResult` без потерь на границе (наш G14/D30)
- **Наш уровень: L1** — инструменты работают, но conformance не гоняли; parity-gap G14/D30 открыт.

### DIM-2 — Аутентификация / авторизация
- [ ] **OAuth 2.1 + PKCE** (обязателен для HTTP-MCP с июня-2025) [M2][M3]
- [ ] Сервер = OAuth Resource Server; отдельный scoped-token на сервер, токены не шарятся [M3]
- [ ] 5 обязательных authz-паттернов спеки [M2]; защита от confused-deputy
- **Наш уровень: L0** 🔴 — auth НЕТ (D3 открыт): только статический bearer, `MCP_AUTH_TOKEN` не задан → сервер ОТКРЫТ. За туннелем один клиент, IP бесполезен (G18). Самый критичный разрыв.

### DIM-3 — Безопасность inbound
- [ ] Firewall подключён, конфиг загружен, нет fail-open (наши D2/D8/D10 закрыты)
- [ ] Traversal/injection/rate/cache покрыты (IN1–IN10 каталога `06`)
- [ ] Секрет-гигиена (gitleaks), нет command-injection (43% серверов уязвимы [M5])
- **Наш уровень: L2** — firewall модульный, часть D# закрыта; но D29 traversal частичен, нет app-auth (тянет вниз). Каталог IN1–IN10 есть, покрытие тестами — план.

### DIM-4 — Безопасность outbound (сервер→клиент)
- [ ] Провенанс недоверенного workspace-контента (OUT1), не эхоить в reason/message
- [ ] Containment на write/move/delete + `destructiveHint` + confirm (OUT5)
- [ ] Нет эксфильтрации через `_SESSION_LOG.md`/параметры (OUT6); error-leak закрыт (OUT7)
- **Наш уровень: L0–L1** 🔴 — каталог OUT1–OUT8 собран (`06 §B`), но НИ ОДНА митигация не реализована (F33). Lethal trifecta на машине клиента открыта.

### DIM-5 — Attack-surface reduction / изоляция
- [ ] Write-type allowlist (default-deny) — `06 §F`/F34
- [ ] No-root инвариант: не исполнять workspace, нет shell, не root — `06 §G`/F35
- [ ] Deploy-hardening: seccomp/Landlock/cap-drop/read-only (Anthropic baseline) — `06 §G.1`
- **Наш уровень: L1** — no-root baseline ЧИСТ эмпирически (F35 ✅ держать), но allowlist не построен (F34), deploy-hardening не применён.

### DIM-6 — Тестирование (5 ворот demo→prod [M7])
- [ ] Unit+contract на каждый инструмент; strict-schema регрессия [M6]
- [ ] Conformance + capability-discovery в CI [M4]
- [ ] Adversarial/security-симуляции подключены и не «театр»
- [ ] E2E сценарии; honest-stub краснеет, не фейкает success (G16)
- **Наш уровень: L1–L2** — quick-тесты + симуляции есть (только что в git, I1); НО нет CI-прогона, conformance, agent-swarm лишь декларативен (`patterns.yaml`), eval-слой не построен.

### DIM-7 — Observability / аудит
- [ ] Audit-trail: auth-события, authz-события, tool-invocations с санитизированными params → principal/scope/context [M2]
- [ ] `structlog`/метрики/`/health`/tracing
- **Наш уровень: L0** 🔴 — нет (I5 не начат). `_SESSION_LOG.md` есть, но это не audit-trail и вдобавок утечка (OUT6).

### DIM-8 — Packaging / deploy
- [ ] `pyproject.toml` + пиннинг (наш I2 ✅)
- [ ] Docker-образ (портируемость, −60% support-тикетов [M8]); digest-pin
- [ ] Non-root `USER`, read-only rootfs
- **Наш уровень: L2** — pyproject+lock есть (I2); Docker нет.

### DIM-9 — Repo-гигиена / supply-chain
- [ ] `LICENSE` (наш F19 — НЕТ), `SECURITY.md`, `CONTRIBUTING.md`, `RELEASING.md` [M8]
- [ ] OIDC trusted publishing (без registry-токенов) [M8]; `pip-audit`/Snyk скан
- **Наш уровень: L1** — README+roadmap сильные; LICENSE/SECURITY/CONTRIBUTING/RELEASING отсутствуют.

### DIM-10 — Контракты / обработка ошибок
- [ ] Единый `ToolResult`/`ErrorDetail` + реестр реакций (у нас ЕСТЬ)
- [ ] Реакции — публичный контракт, полны и не теряются на границе (D4/D27 gaps, A6)
- **Наш уровень: L2–L3** — контракт+реестр реакций зрелые; D4-fallback/D27-класс частичны (F5).

### DIM-11 — Экономический контейнмент
- [ ] Budgets/quotas/side-effect governors (иначе оркестратор делает всю защиту [M2])
- **Наш уровень: L0** — нет квот/бюджетов/лимитов на дорогие операции (media-провайдеры будут внешними/платными).

### DIM-12 — Продуктовая полнота
- [ ] Заявленная функция реально работает e2e
- **Наш уровень: L1** — data/FS-сервер (44 инстр.) работает; **A7** (патерн/уникальность) расширяет
  data-продуктовую поверхность (сценарные+сценовые патерны, уникальность, реакции). ВИДЕО-пайплайн = заглушки.
- **FFmpeg/видео-рендер — ВНЕ оценки пока** (владелец S14): внешняя MCP-интеграция с особенностями,
  честно уровень не поставить → **на последок**, после ранней стадии зрелости. Здесь меряем data/pattern-часть.

---

## Внутренняя инженерная дисциплина (проектные «паттерны»-скилы) — DIM-13…16

> Добавлено (Сессия 14). Зрелость меряется и СОБСТВЕННЫМИ планками проекта — кодифицированными в скилах
> и словаре `D#/G#`. Меряем ДОКАЗАННО: не «скил есть», а «код фактически выдержан». Общий узел этих четырёх —
> **распил монолита `server.py` (A2)**: пока логика в 1521-строчном монолите, адгезия всех четырёх упёрта в L1.

### DIM-13 — Архитектурная адгезия / качество кода (скил `code-quality`)
- [ ] Тонкая обёртка → `config/ops` → `core/engine` → `ToolResult/ErrorDetail` → реакции — на КАЖДОМ инструменте
- [ ] Нет монолита: логика в `core/*_core`, не в `server.py`; altitude выдержан
- [ ] Contract-parity (G14/D30), нет dead-code/dead-inject (D28), honest-stubs кричат (G16), нет stringly-typed дрейфа (G15)
- **Наш уровень: L1–L2** — контракты зрелые, НО `server.py` = монолит (A2 не сделан), G14/D30 и D28 открыты.
  Дисциплина кодифицирована, адгезия неполна. **Цель L3:** A2 + проходы `code-quality` на каждом воркстриме.

### DIM-14 — Декларативность / анти-хардкод (скил `anti-hardcode`)
- [ ] Нет per-entity ветвлений (`if entity==…`), магических литералов (пороги/пути/префиксы) — они в `config/*.yaml`/реестре
- [ ] Конфиг ЗАГРУЖАЕТСЯ, а не игнорируется (нет D2); нет захардоженных секретов (D31)
- [ ] Новая способность = данные+декларации, не ветки в коде — **A7 задаёт эталон** (yaml-конфиг скрипта уникальности + per-project пороги/веса/шаблоны, §5 файла `10`)
- **Наш уровень: L2** — declarative-by-design (channel_config-консолидация), но `config/ops` пуст, точечный
  хардкод (`stt device=cuda`), D2 местами. **Цель L3:** A3 (config/ops) + A7 по эталону.

### DIM-15 — Конвенции размещения / структуры / стиль (скил `project-conventions`)
- [ ] Две вселенные (код/конфиг в git; данные ТОЛЬКО в `workspace/`) соблюдены — A7-override подтвердил границу
- [ ] Каждый файл в своём доме (закон §2/§5): `tools/` тонкие, `core/` логика, `config/` декларации
- [ ] Module-headers терсовые; git-native история (решение→факт в commit+`_sessions`, НЕ process-нарратив в коде); naming по Bounded Context
- **Наш уровень: L2 закон / L1 адгезия** — матрица и стиль определены и соблюдаются в новом коде; НО `tools/`
  пусты (логика в монолите = нарушение thin-wrapper) → адгезия ждёт A2. **Цель L3:** A2 + конвенции при каждом файле.

### DIM-16 — Безопасность как ПАТТЕРН (secure-by-construction, скил `security-reviewer`)
> Отличие от DIM-3/4/5 (те меряют ПОКРЫТИЕ угроз): здесь — насколько защита это СТРУКТУРНЫЙ паттерн, а не россыпь проверок.
- [ ] Единый choke-point контейнмента (G17: `_safe_resolve`→`core/paths.py`), не per-handler
- [ ] Fail-closed по умолчанию (нет fail-open путей); провенанс-маркировка недоверенного вывода как паттерн
- [ ] Ошибки не «текут» сырьём (raw_response leak закрыт); реакция несёт recovery как контракт (G14)
- **Наш уровень: L1** — firewall = императивный пайплайн БЕЗ декларативного ядра (аудит 2026-07-05);
  choke-point не выделен (G17 pending), были fail-open, провенанс не реализован (F33). Паттерн-зрелость
  защиты низкая, хотя точечное покрытие (DIM-3) на L2. **Цель L3:** I6/A6 (choke-point, fail-closed, провенанс).

## Сводный вердикт (честно)

**Общий уровень: L1 (Alpha) с карманами L2.** Проект — крепкий data/FS-MCP на L2-контрактах и packaging, но: **product-core на L1** (видео = заглушки; FFmpeg вне оценки пока), а **три enterprise-измерения на L0** — auth (DIM-2), outbound-защита (DIM-4), observability/экон-контейнмент (DIM-7/11). Reference-серверы MCP сами L0 [M1] — мы уже выше среднего demo, но до production-candidate (L3) далеко именно из-за auth+observability+outbound.

**Внутренняя дисциплина (DIM-13…16):** планки кодифицированы (скилы+`D#/G#`) и держатся в НОВОМ коде, но **доказанная адгезия упёрта в L1** одним узлом — **монолит `server.py` (A2)**: пока `tools/` пусты и логика в монолите, качество (DIM-13), структура-адгезия (DIM-15) и частично анти-хардкод (DIM-14) не поднять. Безопасность-как-паттерн (DIM-16) на L1 отдельно — firewall императивен, choke-point не выделен. **Вывод: A2 — не только архитектурный воркстрим, а разблокировщик четырёх измерений внутренней зрелости.** A7 обязан строиться уже по этим планкам (эталон, не долг).

| Измерение | L0 | L1 | L2 | L3 | L4 |
|---|---|---|---|---|---|
| DIM-1 conformance | | ● | | | |
| DIM-2 auth | ● | | | | |
| DIM-3 sec-inbound | | | ● | | |
| DIM-4 sec-outbound | ●| | | | |
| DIM-5 surface/изоляция | | ● | | | |
| DIM-6 тесты | | ●| | | |
| DIM-7 observability | ● | | | | |
| DIM-8 packaging | | | ● | | |
| DIM-9 repo-гигиена | | ● | | | |
| DIM-10 контракты | | | ● | | |
| DIM-11 экон-контейнмент | ● | | | | |
| DIM-12 продукт (без ffmpeg) | | ● | | | |
| DIM-13 качество кода | | ◐ | ◐ | | |
| DIM-14 анти-хардкод | | | ● | | |
| DIM-15 структура/конвенции | | ◐(адгезия) | ◐(закон) | | |
| DIM-16 безопасность-паттерн | | ● | | | |

> `◐` = split-уровень (закон/дисциплина выше, доказанная адгезия ниже — общий тормоз = монолит A2).

## Карта развития (какой воркстрим поднимает какое измерение)

| Чтобы поднять | Делаем | Целевой уровень |
|---|---|---|
| DIM-2 auth L0→L3 | **I6**: OAuth 2.1+PKCE Resource Server, per-server scoped token | L3 |
| DIM-4 outbound L0→L2 | **I6** P0-митигации `06 §D`: провенанс, containment, destructiveHint | L2→L3 |
| DIM-5 изоляция L1→L3 | **I6**: write-allowlist (F34) + deploy-hardening `06 §G.1` | L3 |
| DIM-7 observability L0→L3 | **I5**: structlog + audit-trail (principal/scope/context) + /health | L3 |
| DIM-6 тесты L1→L3 | **I7**: CI-прогон + conformance + agent-swarm-раннер + E-матрица | L3 |
| DIM-11 экон L0→L2 | **I6/I5**: quotas/budgets на media + side-effect governors | L2 |
| DIM-9 repo L1→L2 | **I8/I2**: LICENSE (F19), SECURITY/CONTRIBUTING/RELEASING | L2 |
| DIM-8 packaging L2→L3 | **I3**: Docker + non-root + digest-pin в CI | L3 |
| DIM-1 conformance L1→L3 | **I7**: `modelcontextprotocol/conformance` в CI; закрыть parity G14/D30 | L3 |
| DIM-12 продукт L1→L3 | **P2–P7** (media→pipeline→video e2e) + **A7** (патерн/уникальность data-поверхность). **P1 FFmpeg — на последок, вне оценки пока** | L3 |
| DIM-10 контракты L2→L3 | **A6**: D4/D27 (F5) — реакции полны | L3 |
| DIM-13 качество кода L1/2→L3 | **A2** (распил монолита) + проходы `code-quality` на каждом воркстриме | L3 |
| DIM-14 анти-хардкод L2→L3 | **A3** (config/ops) + **A7** (yaml-конфиг/per-project override — эталон) | L3 |
| DIM-15 структура L1→L3 | **A2** (tools/ тонкие) + `project-conventions` при каждом новом файле | L3 |
| DIM-16 sec-паттерн L1→L3 | **I6/A6**: choke-point (G17), fail-closed, провенанс как структурный паттерн | L3 |

**Порядок к L3 (enterprise-candidate):** Фаза 0 (I1✅→I2✅→I4→I3) даёт CI-фундамент → **A2 (распил монолита)** разблокирует внутреннюю дисциплину (DIM-13/15) и попутно A3 (DIM-14) → затем **I6 (auth+outbound+allowlist+choke-point — красные DIM-2/4/5/16)** параллельно **I5 (observability)** → I7 (тесты непрерывно) → P-ось продукта (**A7** идёт с data-слоем; **P1 FFmpeg — последним**, тогда же вводим его в оценку). Enterprise (L4) = после продукта: conformance-gate + quotas + supply-chain.

> **A7 строится ПО DIM-13/14/15 как эталон** (декларативный per-project override, тонкие обёртки, git-native история) — новый код обязан задавать планку, а не добавлять долг.

## Сильные MCP-репозитории (эталоны для сверки)

| Репо | Чему учит |
|---|---|
| `modelcontextprotocol/servers` [M1] | reference-инструменты/SDK (но сами L0 — не эталон продакшена) |
| `modelcontextprotocol/conformance` [M4] | **conformance-тесты** — DIM-1 gate |
| `github/github-mcp-server` [M9] | официальный ПРОДАКШН-MCP: структура, auth, тесты |
| `appcypher`/`punkpeye`/`wong2` awesome-lists, `best-of-mcp-servers` [M1] | найти сильные репо по категориям для точечной сверки |

**Источники [M#]:** [M1] awesome-mcp-servers (appcypher/punkpeye/wong2) + modelcontextprotocol/servers · [M2] MCP production-readiness (trust-class/auth/param-boundaries/эконом-контейнмент) + security-spec 6 векторов/5 authz-паттернов + observability-события · [M3] OAuth 2.1+PKCE mandatory HTTP-MCP (июнь-2025 spec), server=Resource Server, per-server scoped token · [M4] modelcontextprotocol/conformance · [M5] MCP security checklist (75 пунктов, 30+ CVE, 43% command-injection, 78.3% attack-success @5 серверов) · [M6] strict-schema/MCP Inspector · [M7] «5 Gates demo→production» тестирование · [M8] repo-гигиена (CONTRIBUTING/SECURITY/RELEASING, OIDC-publish, Docker) · [M9] github/github-mcp-server. URL — в ответе сессии.
