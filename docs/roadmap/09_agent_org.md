# 09 — Организация агентов: отделы, роли, воркфлоу, назначение задач

> Многоагентная «команда разработки» для исполнения роадмапа. **Честно о механике Claude Code:** агент = ТИП (`.claude/agents/*.md`), спавнится как экземпляр по задаче; он стартует «холодным» и переигрывает контекст из базы знаний. «10 агентов на отдел» = один тип роли, запускаемый N раз, НЕ 30 always-on процессов и не 30 дублей-файлов. Оркестратор спавнит нужные роли под задачу.

## 1. Структура (оргмодель)

| Отдел | Lead (opus) | Инженеры (sonnet, спавн ×N) | Скилы | Ось роадмапа |
|---|---|---|---|---|
| **Разработка** | `dev-lead` | `dev-engineer` (3 старших + 6 IC = 10) | mcp-developer · project-conventions · anti-hardcode · code-quality | A (архитектура) + P (продукт) + Фаза-0 файлы |
| **Тестирование** | `qa-lead` | `qa-engineer` (×10) | test-master · project-conventions | I7 (E-матрица, agent-swarm, conformance) |
| **Безопасность** | `sec-lead` | `sec-engineer` (×10) | security-reviewer · reactions-errors · anti-hardcode | I6 (auth/outbound/allowlist/no-root/hardening) |
| **Оркестрация** | `chief-orchestrator` (opus) | — | engineering-questions (kickoff) | распределение по проходам/гейтам |

«Старший инженер» vs «IC» — не отдельные типы, а РОЛЬ в задаче: lead даёт старшему крупный воркстрим (тот дробит и спавнит IC-экземпляров), IC берёт атомарную задачу. Один тип `*-engineer`, разный scope.

## 2. База знаний (обязательна каждому агенту)

- **Роадмап** `docs/roadmap/`: `00`(реальность) · `01`(мастер, воркстримы P/A/I) · `02`(находки F#) · `03`(тест-план E-матрица) · `06`(каталог угроз IN/OUT/§F/§G/§H) · `07`(рубрика зрелости L0–L4) · `08`(закрытие проходов, ремап, git-native) · `spec/`(канон намерения владельца).
- **Скилы** (`~/.claude/skills/`, git-native баннер) — по набору роли.
- **Сильные источники** (из `07 §M`/`08 §4`): MCP conformance, github-mcp-server, MCP-SDK (OAuth 2.1), OWASP LLM, 75-point checklist, Cloudflare/PortSwigger, Anthropic hardening baseline `06 §G.1`.
- **Правила проекта:** память `project-rules` (размещение/тесты), закон §5 (`spec/ИНСТРУКЦИЯ_структура_и_ядро.md`).

## 3. Воркфлоу (git-native, `08 §0`)

```
chief-orchestrator
  ├─ выбирает гейт (07): сейчас → Фаза-0, затем L2, L3(auth/outbound/observability), L4
  ├─ раздаёт воркстримы по отделам (матрица §6), проверяет зависимости (01)
  └─ для задачи → спавнит {dept}-lead
        ├─ engineering-questions (kickoff) → дробит на атомарные
        ├─ спавнит {dept}-engineer(ы) на ветке/worktree
        │     ├─ читает git-историю файла (git log/blame/show), не history_*.md
        │     ├─ доменный скил → правка → тесты зелёные
        │     └─ commit (что/почему/регрессии/F#) → PR
        ├─ кросс-отдел ревью: sec-lead (если outputs/destruct) + qa-lead (тесты)
        └─ merge → orchestrator обновляет 02_findings + _sessions
```

**Git-native обязателен:** история = git; write-back решение→факт = commit-сообщение + `_sessions.md` + память. `history_*.md` НЕ ведём.

## 4. Правила порядка и разрешение конфликтов (оркестратор)

1. **Один файл — один владелец в задаче** (закон §0); параллельные правки одного файла = orchestrator сериализует или даёт worktree.
2. **Зависимости из `01`** — не стартовать воркстрим, пока не закрыт его `Зависит от` (напр. CI-код после I1/I2).
3. **Гейт-порядок `07`:** Фаза-0 (фундамент) → L2 → L3 (3 красных: auth/outbound/observability) → L4. Не перепрыгивать.
4. **Спор отделов** (напр. dev «быстро захардкодить» vs sec «allowlist»): решает принцип каталога/рубрики; при тупике — `AskUserQuestion` владельцу.
5. **Definition of Done задачи:** тесты зелёные (baseline держится) + находка→`02` + решение→commit/`_sessions` + гейт-измерение `07` не просело.
6. **Честность (G16):** незавершённое КРИЧИТ (xfail-spec/NotImplementedError), не фейкает success.

## 5. Назначение ВСЕХ задач по проходам/сессиям

**Проход-0 (Фундамент, разблокирует всё):**
| Задача | Отдел | Воркстрим |
|---|---|---|
| ~~разгитигнорить tests~~ ✅ · ~~docs/dev ремап + git-native~~ ✅ | — | I1/сессия-11 |
| LICENSE (ждёт владельца), SECURITY/CONTRIBUTING | Разработка | I8/I2 |
| `.github/workflows/ci.yml` (линт+типы+тесты+scan) | Тестирование ∥ Разработка | I3 |
| ruff/mypy/pre-commit | Разработка | I4 |
| pytest-раннер + conftest | Тестирование | I7 |

**Проход-L2 (Beta):** repo-гигиена (Разработка/I8) · тесты в CI зелёные (Тестирование/I7) · A2 распил монолита + A3 config/ops (Разработка) · A6 реакции D4/D27 (Безопасность+reactions-errors).

**Проход-L3 (3 самых красных):**
| Задача | Отдел |
|---|---|
| OAuth 2.1+PKCE Resource Server (DIM-2/D3) | **Безопасность** (I6) |
| Провенанс workspace-вывода (OUT1/F33) + containment write/move/delete + destructiveHint (OUT5) | **Безопасность** (I6) |
| Write-type allowlist default-deny (F34/§F) + deploy-hardening `06 §G.1` | **Безопасность** (I6) |
| identity-rate + slowloris-таймауты + payload-лимит (F36/§H) | Безопасность ∥ Разработка |
| observability: structlog + /health + audit-trail + quotas (DIM-7/11) | Разработка (I5) |
| agent-swarm-раннер `tests/agent_swarm/test_agent_swarm.py` по `patterns.yaml` | **Тестирование** (I7) |
| E-матрица E-A/E-I (готовы сейчас) · E-B/C/E · E-D/F после Ф3 | Тестирование |
| A1′ table-loader + A5 search-харденинг | Разработка (A) |

**Проход-L4 (Enterprise):** conformance-gate CI (Тестирование/DIM-1) · quotas/budgets (Разработка/DIM-11) · продукт P1–P7 media→pipeline→video (Разработка/DIM-12) · публикуемые доки (Разработка/I8).

**Продукт (P-ось, порядок владельца OQ5):** A1′ таблицы → P3 STT → P2/P4 TTS/IMG → P1 FFmpeg → P5 steps → P6 entry_points → P7 video. Все через закон §5 (провайдер `core/providers/<p>` + обёртка `tools/<cat>` + `config/ops`).

## 6. Матрица «ось → отдел» (быстрая)

- **Разработка** ← вся ось **A** (A1′/A2/A3/A5/A6) + вся ось **P** (P1–P7) + инфра-код I2/I4/I5.
- **Тестирование** ← **I7** (сквозная): E-матрица, agent-swarm, conformance, coverage каждого воркстрима; CI-гейт I3 (совместно).
- **Безопасность** ← **I6** целиком (auth/secrets/outbound/allowlist/no-root/hardening) + аудит каждого воркстрима с outputs/destruct + reactions (A6).
- **Оркестратор** ← последовательность гейтов `07`, зависимости `01`, конфликты, `_sessions`/`02` апдейт, эскалация владельцу.
