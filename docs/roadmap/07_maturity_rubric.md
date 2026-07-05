# 07 — Рубрика зрелости MCP-сервера + честная само-оценка

> Большой список требований, по которому МОЖНО ЧЕСТНО судить, на какой стадии репозиторий, и строить карту развития. Собран из сильных источников (официальный MCP + отраслевые чеклисты, ссылки [M#] внизу), спроецирован на `video_pipeline_mcp`. Это «линейка», а `01_master_roadmap.md` — маршрут; каждое требование привязано к воркстриму/находке.
>
> **Честность — принцип.** Ставим уровень по ДОКАЗАННОМУ состоянию (как аудит: не «есть auth», а «есть OAuth 2.1 Resource Server с per-server scoped token»). Ноль баллов там, где механизма нет, даже если «запланировано».

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
- **Наш уровень: L1** — data/FS-сервер (44 инстр.) работает; ВИДЕО-пайплайн (суть) = заглушки (media/video пусты).

## Сводный вердикт (честно)

**Общий уровень: L1 (Alpha) с карманами L2.** Проект — крепкий data/FS-MCP на L2-контрактах и packaging, но: **product-core на L1** (видео = заглушки), а **три enterprise-измерения на L0** — auth (DIM-2), outbound-защита (DIM-4), observability/экон-контейнмент (DIM-7/11). Reference-серверы MCP сами L0 [M1] — мы уже выше среднего demo, но до production-candidate (L3) далеко именно из-за auth+observability+outbound.

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
| DIM-12 продукт | | ● | | | |

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
| DIM-12 продукт L1→L3 | **P1–P7**: media-провайдеры → pipeline → video e2e | L3 |
| DIM-10 контракты L2→L3 | **A6**: D4/D27 (F5) — реакции полны | L3 |

**Порядок к L3 (enterprise-candidate):** Фаза 0 (I1✅→I2✅→I4→I3) даёт CI-фундамент → затем **I6 (auth+outbound+allowlist — 3 самых красных измерения)** параллельно **I5 (observability)** → I7 (тесты как непрерывное) → P-ось (продукт). Enterprise (L4) = после продукта: conformance-gate + quotas + supply-chain.

## Сильные MCP-репозитории (эталоны для сверки)

| Репо | Чему учит |
|---|---|
| `modelcontextprotocol/servers` [M1] | reference-инструменты/SDK (но сами L0 — не эталон продакшена) |
| `modelcontextprotocol/conformance` [M4] | **conformance-тесты** — DIM-1 gate |
| `github/github-mcp-server` [M9] | официальный ПРОДАКШН-MCP: структура, auth, тесты |
| `appcypher`/`punkpeye`/`wong2` awesome-lists, `best-of-mcp-servers` [M1] | найти сильные репо по категориям для точечной сверки |

**Источники [M#]:** [M1] awesome-mcp-servers (appcypher/punkpeye/wong2) + modelcontextprotocol/servers · [M2] MCP production-readiness (trust-class/auth/param-boundaries/эконом-контейнмент) + security-spec 6 векторов/5 authz-паттернов + observability-события · [M3] OAuth 2.1+PKCE mandatory HTTP-MCP (июнь-2025 spec), server=Resource Server, per-server scoped token · [M4] modelcontextprotocol/conformance · [M5] MCP security checklist (75 пунктов, 30+ CVE, 43% command-injection, 78.3% attack-success @5 серверов) · [M6] strict-schema/MCP Inspector · [M7] «5 Gates demo→production» тестирование · [M8] repo-гигиена (CONTRIBUTING/SECURITY/RELEASING, OIDC-publish, Docker) · [M9] github/github-mcp-server. URL — в ответе сессии.
