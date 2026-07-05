# 08 — Честное закрытие проходов: файлы, требования, ремап, готовые решения

> `docs/dev/` (приватный, gitignored) **удалён** владельцем — потерял ценность. Это не потеря, а **форсирующее событие**: единственный источник истины теперь git-tracked `docs/roadmap/` + память + коммиты. Этот файл: (1) ремап удалённого `docs/dev`, (2) список файлов+требований для честного закрытия проходов, (3) привязка сильных GitHub-репо к уровням зрелости/стилю/структуре/правилам.
>
> «Проход» = гейт зрелости из `07_maturity_rubric.md`. Закрыть проход **честно** = выполнить его exit-чеклист по ДОКАЗАННОМУ состоянию (не «запланировано»).

## 0. Принцип: GIT-NATIVE история (владелец 2026-07-05)

**Git И ЕСТЬ история версий — файлов И кода — и это лучше текстовых `history_*.md`.** Используем на полную:
- **История файла** = `git log --follow <file>` · `git blame` · `git show <commit>` · `git diff` (не рукописный `history_*.md`, который гниёт и дублирует).
- **Решение→факт (почему)** = **commit-сообщение** (тело: что/почему/возможные регрессии/`F#`). Атомарные коммиты + дисциплина сообщений = теперь ЧАСТЬ процесса, не опция.
- **Нарратив сессии** (много файлов) = `_sessions.md`. **Кросс-сессия/резюме/решения владельца** (чего в git нет) = память.
- **Обзор/ревью** = PR + `git blame`; **версии** = tags.

Следствие: `history_*.md` больше НЕ ведём. Скилы перепрошиты git-native баннером (все 9). Авторитет процесса = память `project-workflow-canonical`.

## 1. Ремап удалённого `docs/dev/` (source-of-truth)

Всё, что скилы/процесс писали и читали в `docs/dev/`, переезжает в git-tracked дома. **Скилы обязаны обновить пути** (см. §5).

| Было (`docs/dev/`, удалён) | Стало (git-tracked / память) |
|---|---|
| `workflow.md` (канон процесса, 5 вопросов/4 уровня) | память `project-workflow-canonical` + `project-rules` |
| `threat_landscape.md` (угрозы) | **`06_threat_catalog.md`** (IN/OUT/§F/§G/§H) |
| `testing_strategy.md` | **`03_testing_plan.md`** |
| `audit/AUDIT.md`, `audit/global.md` (D#/G#) | **`02_findings.md`** (F#) — единый реестр; X1 закрыт этим |
| `history_*.md` (решение→факт по модулям) | **`_sessions.md`** (журнал сессий) + commit-сообщения + память |
| `architecture.md` | `README.md` + `docs/roadmap/` |
| `docs/dev/tools/<X>` (зеркало кода) | **новый** `docs/<...>` при I8 (публикуемые доки) — пока НЕ существует |
| `session_*_pipeline_tests.md` | `03_testing_plan.md` + `_sessions.md` |

**Следствие для «history-first»:** решение→факт теперь пишем в `_sessions.md` + commit, НЕ в `docs/dev/history_*.md`. Правило процесса переопределено (обновить память `project-workflow-canonical`).

## 2. Файлы, которых НЕТ и которые нужны для честного закрытия

Ничего из ниже на диске нет (проверено). Сгруппировано по гейту зрелости.

### Гейт L2 (Beta — repo стоит сам, воспроизводим)
| Файл | Зачем / DIM | Воркстрим |
|---|---|---|
| `LICENSE` | публичный репо без лицензии = «all rights reserved» (F19); DIM-9 | I2/I8 (ждёт выбора владельца) |
| `SECURITY.md` | канал репорта уязвимостей [M8]; DIM-9 | I8 |
| `CONTRIBUTING.md` | как контрибьютить [M8]; DIM-9 | I8 |
| `.github/workflows/ci.yml` | линт+типы+тесты+security-scan на PR (F13); DIM-6/8 | I3 |
| `.pre-commit-config.yaml` | ruff/mypy/bandit локально до пуша; DIM-6 | I4 |
| `conftest.py` + pytest-раннер | канонический раннер (сейчас часть тестов = скрипты); DIM-6 | I7 |
| `ruff`/`mypy` секции (в `pyproject`) | типы/линт (I4); DIM-6 | I4 |
| `CHANGELOG.md` | история релизов; DIM-9 | I8 |

### Гейт L3 (Production-candidate)
| Файл | Зачем / DIM | Воркстрим |
|---|---|---|
| `Dockerfile` + `.dockerignore` | портируемость, non-root USER, read-only (DIM-8/5) [M8] | I3 |
| `RELEASING.md` + OIDC-publish | supply-chain, без registry-токенов [M8]; DIM-9 | I8 |
| OAuth 2.1 модуль (`core/auth/`?) | Resource Server + PKCE (DIM-2, D3) [M3] | I6 |
| `core/paths.py` write-allowlist-конфиг (`config/write_policy.yaml`) | default-deny (F34, §F); DIM-5 | I6 |
| provenance-обёртка вывода | недоверенный workspace (F33/OUT1); DIM-4 | I6 |
| observability (`structlog` + `/health` + audit-trail) | DIM-7 [M2] | I5 |
| `tests/agent_swarm/test_agent_swarm.py` | раннер по `patterns.yaml` (F32); DIM-6 | I7 |
| systemd unit / deploy-hardening (`06 §G.1`) | seccomp/Landlock/cap-drop; DIM-5 | I6 |

### Гейт L4 (Enterprise)
| Файл/условие | Зачем / DIM |
|---|---|
| conformance в CI (`modelcontextprotocol/conformance`) | DIM-1 [M4] |
| quotas/budgets (`config/*` + governors) | эконом-контейнмент (DIM-11) [M2] |
| продукт e2e (media→pipeline→video) | DIM-12 (P1–P7) |
| `docs/` публикуемые (architecture/API, зеркало tools) | DIM-9 (I8, замена удалённого docs/dev/tools) |

## 3. Exit-чеклист по проходам (гейтам)

**Проход-0 (Фундамент → repo самодостаточен без docs/dev):** ✅ ремап §1 зафиксирован · LICENSE выбран · `.github/workflows/ci.yml` гоняет линт+тесты · pre-commit · pytest-раннер · README=факт (✅). **Критерий: клон репо собирается и тестируется без единой ссылки на удалённый `docs/dev`.**

**Проход-L2:** DIM-3/8/10 держат L2 · repo-гигиена (LICENSE/SECURITY/CONTRIBUTING) · тесты в CI зелёные · baseline-наборы (structure 35/35 и пр.) в CI.

**Проход-L3:** 3 самых красных измерения подняты — **auth (DIM-2 L0→L3)**, **outbound (DIM-4 L0→L2+)**, **observability (DIM-7 L0→L3)** · write-allowlist+deploy-hardening (DIM-5) · agent-swarm-раннер + E-матрица зелёные · Docker.

**Проход-L4:** conformance-gate · quotas · продукт e2e · публикуемые доки.

## 4. Сильные GitHub-репо → уровень / стиль / структура / правила

Готовые решения, привязанные к нашему (Python+Pydantic, thin-wrapper→ops→engine, декларатив-by-design, русские комменты, `tools/<group>`+`core/providers/<p>`, контракт ToolResult/ErrorDetail+реестр реакций). Дополняет `04_github_adoption.md`.

| Репо | Берём | Поднимает | Как ложится на наш стиль/структуру/правила |
|---|---|---|---|
| **`modelcontextprotocol/conformance`** [M4] | conformance-тесты как CI-гейт | DIM-1 | добавить job в `ci.yml`; ассертить наш MCP-boundary (parity G14/D30) |
| **`github/github-mcp-server`** [M9] | эталон структуры продакшн-MCP: auth, layout, тесты, релиз | DIM-2/6/8/9 | сверить наш `tools/<group>` layout (A2) и auth-модуль (I6) с их; адаптировать, не копировать |
| **`modelcontextprotocol/servers`** (filesystem/др.) [M1] | паттерны safe-path, permission-scoping, аннотации | DIM-3/5 | усилить `core/paths.safe_resolve` + `destructiveHint`; наш firewall — сверх их |
| **MCP Python SDK** (official) | OAuth 2.1 Resource Server + PKCE helper'ы [M3] | DIM-2 | обернуть в `core/auth/`; сохранить контракт ToolResult |
| **awesome-lists** (appcypher/punkpeye/wong2), `best-of-mcp-servers` [M1] | точечно найти сильный репо по категории (FS/tables/media) | все | брать 1 эталон на измерение, сверять с нашими правилами размещения |
| **75-point security checklist / MCP-38** [M5] | пункты аудита → тест-паттерны | DIM-3/4 | уже влиты в `06` (IN/OUT) + `patterns.yaml` |

**Правило адаптации (наше):** любое готовое решение проходит через `project-conventions` (размещение/стиль) + `anti-hardcode` (декларатив, не хардкод) + `security-reviewer` (обе стороны) ДО вливания. Не «скопировать репо», а «взять паттерн и уложить в закон размещения §5».

## 5. Реконсиляция скилов (docs/dev-ссылки протухли)

Все 9 скилов ссылались на удалённый `docs/dev/`. **✅ РЕКОНСИЛИРОВАНО 2026-07-05:** во все 9 вставлен **git-native баннер** (перекрывает ссылки ниже: история=git, write-back=commit+`_sessions.md`, угрозы→`06`, тесты→`03`, findings→`02`, процесс→память); «источниковые» пути в описаниях/телах свопнуты (`threat_landscape`→`06`, `testing_strategy`→`03`, `workflow`→память, `audit`→`02`). Остаточные `history_*`/`session_*`/`architecture` упоминания перенаправляет баннер на git. Первый пункт Прохода-0 закрыт.

**Источники [M#]** — см. `07_maturity_rubric.md` (M1–M9): MCP conformance/servers/github-mcp-server, OAuth 2.1 spec, 75-point checklist, repo-гигиена best practices.
