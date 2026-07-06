# Журнал сессий

> Один RESUME-указатель внизу. Каждая сессия: что сделано → коммит → что дальше.

## Baseline тестов (держать зелёным)
`tests/quick/` как скрипты: audit 30/30 · search 24/24 · structure 35/35 · tables 33/33 ·
firewall 1/4 (env) · tunnel 19/20 (env). Замер 2026-07-05.

---

## Сессия 1 — 2026-07-05 — Каркас программы

**Сделано:**
- Reality-check (`00_reality_check.md`): диск vs README/память проверены на фактах. Ключевое: проект сегодня = spreadsheet/FS-сервер (44 data-инструмента), видео-пайплайн = заглушки; декларативный слой/обёртки/pipeline пусты; аудит D#/G# без артефактов; tests/ и docs/dev в .gitignore; нет CI/packaging/типов/логов/auth.
- Мастер-roadmap (`01`): 3 оси (P продукт / A архитектура / I инфра), 21 воркстрим, фазовая раскладка, ~20–30 сессий.
- Реестр находок (`02`): 18 находок (F1–F18), посев тремя прогонами (quality / style-structure / systems-security).
- План тестирования (`03`) и GitHub-кандидаты (`04`) — скелет.
- Создан выделенный скил **reactions/errors** в `~/.claude/skills/` (в стиле проекта).

**Тесты:** без изменений кода в этой сессии (только доки + скил). Baseline держится.

**Коммит:** `docs: enterprise roadmap framework (session 1) + reactions skill` (см. git log).

---

## Сессия 2 — 2026-07-05 — Фаза 0 / I1: VCS-гигиена

**Сделано:**
- Разгитигнорен `tests/` (F12 🔴→✅). Был свален в секцию «ДАННЫЕ» вместе с `workspace/` — но тесты это код. Убрал из `.gitignore`; в git добавлены 23 файла: 6 quick-сьютов + 6 симуляций (virus/bot_army/cache_injection/cache_overflow/config_change/render_draft_final) + `tests/config/scenarios.yaml`. Артефакты (`__pycache__`, `.pytest_cache`) остаются игнорированы явными паттернами.
- Секретов в `tests/` нет (проверено grep). `docs/dev/` оставлен приватным (OQ3 — lean владельца «отозвать позже»).
- Бонус: F18 🟡→🟢 — симуляционные сьюты подтверждены на диске и теперь версионируются.

**Тесты:** `.gitignore`-правка не влияет на рантайм; код не тронут → baseline держится (30/30·24/24·35/35·33/33).

**Коммит:** `chore(vcs): version tests/ — un-gitignore test suites (I1, F12)`.

---

## Сессия 3 — 2026-07-05 — Фаза 0 / I2: Packaging

**Сделано:**
- Создан `pyproject.toml` (PEP 621, setuptools-backend): метаданные, `requires-python>=3.11`, 11 runtime-зависимостей (полы + кап `pydantic<3`), `[project.optional-dependencies].dev` (pytest/ruff/mypy/pre-commit/bandit/pip-audit — задел под I4/I3), console-entry `video-pipeline-mcp = server:main`, явный packages.find (core/tools/pipeline; server.py как py-module).
- `requirements.txt` → делегирует в pyproject (`-e .`) — единый источник истины, `install.sh` работает без правок.
- `requirements.lock` — точные пины (`pip freeze`, 90 пакетов) для репро/CI.
- Push всех коммитов на GitHub выполнен (владелец разрешил).
- Новая находка **F19** 🟠 — нет `LICENSE` (публичный репо без лицензии); ждёт решения владельца.

**Верификация:** `pyproject` парсится; `pip install -e . --no-deps` OK; `video-pipeline-mcp --help` работает; baseline держится (audit 30/30·search 24/24·structure 35/35·tables 33/33). `*.egg-info` игнорируется.

**Коммит:** `build(packaging): pyproject.toml + lock + delegate requirements (I2)`.

---

## Сессия 4 — 2026-07-05 — Спеки ↔ код (по указанию владельца)

**Сделано (планирование, кода не трогал):**
- Прочитаны спек-файлы владельца (`media_tools_deployment.md`, `ИНСТРУКЦИЯ_шаблоны.md`, 7 `*.schema.md` — заголовки). Создан **`05_data_template_media_system.md`**: целевая архитектура (niche→network→channel→video; консолидированный `channel_config.yaml`; workspace-tpl + table-схемы; интроспектор; media-пайплайн), матрица DONE/STUB/MISSING, инвентарь спек-файлов, воркстримы, принцип «синхронизация не ломая».
- **Крупная ревизия reality-check:** `config/ops`/`model_routing` **намеренно упразднены** (консолидация в channel_config) — F2 ОТОЗВАНА, F7 понижена (🔴→🟠). Workspace-шаблоны (6 tpl) + `structure_*` (35/35) РЕАЛИЗОВАНЫ — мой S1-пессимизм был неверен дважды.
- Новые находки: **F20** (table-схемы отсутствуют), **F21** (нет `introspect_tables.py`), **F22** (спек-файлы вне репо).
- Master-roadmap A-ось исправлена: ~~A1 populate ops~~ → **A1′ таблично-схемный слой**; A3 пересмотрен; A4 = README под консолидированную архитектуру.
- Новые открытые вопросы: **OQ4** (импортировать спеки в репо?), **OQ5** (сначала A1′ таблицы или media P2–P4?).

**Тесты:** кода не трогал → baseline держится.

**Коммит:** `docs(roadmap): spec↔code sync — data/template/media system (session 4)`.

---

## Сессия 5 — 2026-07-05 — Импорт спек-файлов в репо (OQ4)

**Сделано:**
- Импортированы **15 спек-файлов** владельца из `/home/admin/projects/` в трекаемый `docs/roadmap/spec/`: `schemas/` (7 `*.schema.md`), `instructions/` (5 `ИНСТРУКЦИЯ_*` + `media_tools_deployment.md`), `Бриф_табличные_инструменты.md`, `project_memory.spec.md`. Оригиналы оставлены.
- `spec/README.md` — индекс (что задаёт каждый файл + код-зона). `spec/IMPROVEMENTS.md` — ledger предложений улучшений функций (IMP#), засеян 3 реальными: IMP1 reactions DEFAULT-fallback (F5), IMP2 stt device-хардкод (F10), IMP3 ffmpeg busy-loop (F10).
- **F22 🟠→✅** (OQ4 закрыт). Ссылка на spec/ в roadmap README.

**Тесты:** кода не трогал → baseline держится.

**Коммит:** `docs(spec): import owner design specs into tracked docs/roadmap/spec/ (session 5)`.

---

## Сессия 6 — 2026-07-05 — README truth-up (A4 / OQ1)

**Сделано:**
- README переписан под **фактическую консолидированную** архитектуру (решение владельца: убрать чего нет, чтобы не ловить ложные сигналы). Убрано: `config/ops/*.ops.yaml`, `model_routing.yaml`, `paths.yaml`, `.xlsx`-конфиг. Добавлено реальное: `core/{firewall,search,excel,tables,paths}`, `config/{firewall,channel_config,tunnel,server_reactions}`, `templates/workspace` (6). Маркеры статуса ✅/🟠/🔲, секция зависимостей под pyproject, ссылка на `docs/roadmap/`.
- **F7 🟠→✅**, **A4 ✅** (OQ1 закрыт).

**Тесты:** кода не трогал (доки) → baseline держится.

**Коммит:** `docs(readme): truth-up to actual consolidated architecture (A4, session 6)`.

---

## Сессия 7 — 2026-07-05 — A1′ часть 1: формат таблично-схемного слоя

**Сделано (безопасно, код не тронут):**
- Разобрал точку интеграции: `structure_create`/`template_engine.py:155-163` для `kind: table` **откладывает** книгу (`file_id` + `tables_pending[table_template]`, `.xlsx` не создаёт — честная заглушка Ф3). Loader'а `config/templates/tables/*.schema.yaml` в коде НЕТ (ед. ссылка — прокид имени).
- Свёл API `core/excel`: `create_workbook`/`add_sheet`/`add_column(formula=)`/`set_validation(allowed=)`/`apply_formatting` — примитивы материализации есть, схема мапится 1:1.
- Определил **формат `config/templates/tables/*.schema.yaml`** (`spec/TABLE_SCHEMA_FORMAT.md`): мост `spec/schemas/*.schema.md` → YAML → `core/excel`; флаги id/W/F/fk, тип enum→set_validation; + дизайн loader'а (фаза ТАБЛИЦЫ) для след. сессии.
- **Proof:** `config/templates/tables/network_config.schema.yaml` (4 листа, все флаги) — из спеки, валиден.
- **F20** 🟠→🔨 (в работе).

**Тесты:** код не тронут → structure 35/35 (проверено), baseline держится.

**Коммит:** `feat(tables): A1′ schema format + network_config proof (session 7)`.

---

## Сессия 8 — 2026-07-05 — Конформность целевой структуре (закон размещения)

**Сделано (планирование, кода не трогал):**
- Владелец прислал каноническую структуру (`ИНСТРУКЦИЯ_структура_и_ядро.md` — «карта и закон»). Встроил **конформность** в `01_master_roadmap.md` (не плодя файлов): маппинг «целевой каталог → воркстрим» (`tools/<group>/`→A2, `core/providers/<provider>/`→P1–P4, `tools/media`→P4′, `tools/video`→P7, `pipeline/{entry_points,steps}`→P5/P6, `scripts/introspect`→A1′, `docs/dev/tools/` зеркало→I8) + «закон размещения способностей» (§5).
- **Вскрыл конфликт спек (F23):** «закон» (§2/§5) требует `config/ops/*.ops.yaml`+`model_routing.yaml`+`paths.yaml`; `media_tools_deployment.md §0` их упраздняет. Противоречие влияет на A1/A3 — до решения владельца (OQ6) не финализирую. Отметил в `01`, `02` (F23+F24), `05 §0`.
- README оставлен как факт (не цель) — владелец подтвердил «убрали чего нет — нормально».

**Тесты:** кода не трогал → baseline держится.

**OQ6 решён (в этой же сессии):** reconcile-by-purpose — `config/ops/{filesystem,tables,excel}.ops.yaml` строим (A3, реестр операций tool-обёрток, закон §3); media остаётся в `resource_limits`. **F23 ✅**. A3 возвращён в план (пара к A2).

**Design-уточнение media (владелец пояснил эволюцию замысла):** выбор media-модели — **per-channel**. Модель/голос/параметры из `resource_limits` конкретного канала; `model_routing.yaml` **не строим вообще** (не «минорно»). Механизм: адаптер провайдера подхватывает `channel_config` канала при работе с ним (контекст канала из параметров инструмента). `config/channel_config.yaml` = шаблон → материализуется per-channel. Открыто для P2–P4: пломбировка «активного канала» + связь шаблон↔per-channel. Записано в `05 §1`, `01` (model_routing → не строим).

**Коммиты:** `docs(roadmap): target-structure conformance + F23 resolved (session 8)` + `docs(roadmap): media model-selection is per-channel, drop model_routing (session 8)`.

---

## Сессия 9 — 2026-07-05 — Тест-видение + каталог угроз (planning, кода не трогал)

**Триггер:** живой `structure_check_integrity` вернул `total_entities:0` → разбор → каскад требований владельца по тест-эмуляции и защите.

**Сделано:**
- **F25** — вскрыто integrity-слепое пятно ФС↔реестр (`check_integrity` аудирует только реестр; персист-бага НЕТ — эмпирика venv). **F26/F27** — cold-start advisory отсутствует (пустая ниша = холодный старт, не «нечего рекомендовать»); reconcile-оркестрация ручная. **F28/F29** — delete/move_column молча ломают формулы, `validate_formulas` слеп (grep токенов, openpyxl не пересчитывает). **F30** — устойчивость формул к неполным данным (scene_profile-gating) не в loader. **F31** — нет eval-слоя качества (промпт↔поток-данных). **F32** — симуляции одновариантны, нет agent-swarm. **F33** — outbound-защита клиента = сплошной gap. **F34** — нет write-type allowlist. **F35** — no-root инвариант (эмпирика ЧИСТО, держать). **F36** — DDoS/пакеты двухуровневые (Cloudflare edge), origin-gaps.
- **`03_testing_plan.md`** — E-матрица: E-A (конфиг-матрица ниши/рекомендации) · E-B (4 точки входа×depth-control) · E-C (проходы 3/2/1 метаморфно) · E-D (деструктив таблиц) · E-E (тайминг ID) · E-F (устойчивость формул) · E-G/H/I (eval: фикстуры/качество-контекста/поиск) · **agent-swarm** харнесс (честные+злоумышленники, обе стороны).
- **`06_threat_catalog.md`** — НОВЫЙ. Восстанавливает threat_landscape (F16🔨). Inbound IN1–IN10, Outbound OUT1–OUT8, §C принципы, §D приоритет, §F write-allowlist (default-deny), §G no-root + §G.1 deploy-hardening (systemd/seccomp/Landlock/Anthropic-baseline/gVisor-Kata, ист. S1–S7), §H кэш/DDoS/пакеты (двухуровнево edge+origin, ист. C1–C7). Research: MCP-38, «When MCP Servers Attack», NSA/CISA MCP, OWASP LLM Top-10, Cloudflare/PortSwigger.
- **`tests/agent_swarm/patterns.yaml`** — ВСЕ паттерны каталога как декларативные behavior-модели (36 шт: 5 честных + 10 in + 8 out + 5 escalation + 8 cache/ddos/packet), каждый со `status: implemented|xfail-spec`, `ref`/`surface`/`expect`/`mitigation`/`extends`. + `history.md`.

**Тесты:** кода сервера не трогал → baseline держится (structure 35/35 и пр.).

**Коммит:** `docs(roadmap): testing vision + threat catalog + agent-swarm patterns (session 9)`.

---

## Сессия 10 — 2026-07-05 — Консолидация решений + рубрика зрелости (planning)

**Сделано:**
- **`07_maturity_rubric.md`** — НОВЫЙ. 12 измерений (conformance/auth/sec-in/sec-out/изоляция/тесты/observability/packaging/repo-гигиена/контракты/эконом-контейнмент/продукт) × уровни L0–L4, с ЧЕСТНОЙ само-оценкой по доказанному состоянию + матрица + карта развития (какой воркстрим поднимает какое измерение). **Вердикт: L1 с карманами L2**; три измерения на L0 — auth (D3), outbound (F33), observability/эконом (I5). Research: MCP conformance-репо, OAuth 2.1+PKCE mandatory (spec июнь-2025), 75-point security checklist, «5 Gates», github-mcp-server. Эталоны-репо для сверки.
- **Консолидация в `01_master_roadmap.md`** (решения легли в фазы): I6 расширен (OAuth 2.1 Resource Server + P0-митигации `06 §D`: провенанс/containment/destructiveHint/write-allowlist/deploy-hardening/identity-rate); I5 (+audit-trail principal/scope/context + quotas/budgets); I7 (+E-матрица + agent-swarm-раннер + conformance). «Definition of Enterprise-ready» теперь измерим через рубрику `07` (L4=цель, порядок к L3).

**Тесты:** кода не трогал → baseline держится.

**Коммит:** `docs(roadmap): maturity rubric + consolidate session-9 decisions into phases (session 10)`.

---

## Сессия 11 — 2026-07-05 — docs/dev удалён: ремап + закрытие проходов + push

**Сделано:**
- **Push:** сессии 9–10 на GitHub (`4da7ca1..6704c8a`).
- **`docs/dev/` удалён владельцем** (был gitignored — не в git, коммитить нечего). Форсирующее событие: git-tracked `docs/roadmap/` = единственный источник.
- **`08_pass_closure.md`** — НОВЫЙ: (1) ремап source-of-truth удалённого `docs/dev` (workflow→память, threat_landscape→`06`, testing_strategy→`03`, audit/D#/G#→`02`, history_*→`_sessions`+commit); (2) список недостающих файлов по гейтам L2/L3/L4 (LICENSE/SECURITY/CONTRIBUTING/CI/pre-commit/conftest/Dockerfile/OAuth/allowlist/observability — НИ ОДНОГО нет); (3) exit-чеклисты проходов; (4) сильные репо→уровень/стиль/структура/правила (conformance/github-mcp-server/MCP-SDK/servers/awesome-lists); (5) реконсиляция скилов.
- **Правило истории обновлено** (README роадмапа + память `project-workflow-canonical`): write-back → `_sessions.md`+commit, не в `docs/dev/history_*`.

**GIT-NATIVE принцип (владелец):** git = история версий файлов И кода, лучше `history_*.md`. Закреплён `08 §0` + память `project-workflow-canonical` (переписана): read = `git log --follow`/`blame`/`show`; write-back решение→факт = commit-сообщение + `_sessions.md` + память; `history_*.md` не ведём. **✅ 9 скилов реконсилированы** — git-native баннер вставлен во все + «источниковые» пути свопнуты (первый пункт Прохода-0 ЗАКРЫТ).

**Тесты:** кода не трогал → baseline держится.

**Коммит:** `docs(roadmap): docs/dev remap + pass-closure requirements + repo maturity mapping (session 11)`.

---

## Сессия 12 — 2026-07-05 — Оргструктура агентов (отделы + оркестратор)

**Сделано:** построена многоагентная команда для исполнения роадмапа.
- **`09_agent_org.md`** — оргмодель: 3 отдела (Разработка=ось A/P, Тестирование=I7, Безопасность=I6) + оркестратор; роли (lead opus + engineer sonnet ×N), база знаний (роадмап+скилы+сильные репо), git-native воркфлоу, правила порядка/конфликтов, **назначение ВСЕХ задач по проходам** (Фаза-0/L2/L3/L4) и осям.
- **`.claude/agents/` (7 типов):** `chief-orchestrator` (раздаёт/конфликты/эскалация), `dev-lead`+`dev-engineer` (mcp-developer/project-conventions/anti-hardcode/code-quality), `qa-lead`+`qa-engineer` (test-master), `sec-lead`+`sec-engineer` (security-reviewer/reactions-errors). Каждый: скилы+KB+git-native+scope.
- **Честность механики:** «10/отдел» = спавн-экземпляры одного типа, не 30 файлов/процессов; оркестратор спавнит роли под задачу.

**Тесты:** кода не трогал → baseline держится.

**Коммит:** `feat(agents): department org (dev/qa/sec) + chief-orchestrator + task assignment (session 12)`.

---

## Сессия 13 — 2026-07-05 — Каталог тестов (не плодить, развивать)

**Сделано:** `tests/CATALOG.md` — карта зон ответственности. Разделены классы: **простые** (`tests/quick/`: audit_fixes=дом D#-регрессий · firewall · search · structure · tables · tunnel) и **симуляции** (`bot_army`/`cache_injection`/`cache_overflow`/`config_change`/`virus_injection`/`render_draft_final`) + рой (`agent_swarm/patterns.yaml`, раннер TODO). Для каждого: зона ответственности + зачем + **запас расширения** (какие векторы каталога `06`/E-матрицы впитывает) + когда оправдан новый. **Правило «не плодить»:** расширяй хозяина; новый — только вне зон ВСЕХ И когда лимит расширения исчерпан. Маршрут «куда класть сценарий» (§D). Реальные новые наборы на горизонте: только `agent_swarm/test_agent_swarm.py` + (при утяжелении) `tests/structure_emulation/`. README пропатчен (ссылка+docs/dev-ремап). QA-агенты (`qa-lead`/`qa-engineer`) получили правило+CATALOG.

**Тесты:** кода не трогал → baseline держится.

**Коммит:** `docs(tests): test catalog — responsibility zones + no-proliferation rule (session 13)`.

---

## Сессия 14 — 2026-07-06 — Патерн/уникальность-слой в план (A7)

**Сделано:** по брифу владельца (таблицы/потоки данных с учётом работы через Claude AI Web) занёс подсистему в роадмап.
- **`10_pattern_uniqueness_system.md`** — новый док. Свёл разрозненные упоминания уникальности в спеках в единую подсистему + достроил недостающее (🆕): **патерн = структура + отслеживаемые повторяемые слова/предложения**; две библиотеки **конкуренты vs наши** + порядок чтения ИИ (конкуренты→наши); **отсутствие патернов** = сигнал (конкур. — особый приоритет, наши — меньший минус); **фоновый расчёт %** сервером → сигнал в петлю памяти `project_memory §3` → флаг (ложное) / переработка (структура-для-тестов ИЛИ борьба с повторами); сервер через Claude AI Web **не навязывает поток** — только рекомендует реакциями; **сцена = 2 критерия** (патерн сцены: порядок/число SVG/анимаций/слоёв/переходов — 🆕; уникальность сцены: ассеты+вариации — уже есть `SCENES.scene_uniqueness`); **конфиг-шаблоны ФС/таблиц и поиска правятся ИИ через инструменты**; структуры патернов → память проекта.
- **Реконсиляция:** явно опёрто на уже существующее в спеках (`UNIQUENESS`/`SCENES`/`*_PATTERNS_USED`/`ASSETS_USED`/`UNIQUENESS_ALERTS` + петля `project_memory.spec §3`) — расширяем, не пересобираем. §0 = таблица «что уже есть → наш вклад».
- **Воркстрим A7** (A7.1–A7.5) добавлен в `01_master_roadmap` (ось A, зав. A1′/A6/A5/I5, 3–4 сессии) + в Фазу 1. Кросс-линк из `05` §4. Ссылка в `README`.
- **5 открытых вопросов владельцу** (OQ-A7-1…5): пороги, алгоритм совпадения слов (локально/внешний → где живёт расчёт), чем тикать фон (планировщика нет), лист/колонки для крит.1 сцены, размещение пользовательских шаблонов.
- **OQ-A7-5/2/3 РЕШЕНЫ тут же (владелец):**
  - **A7-5 (шаблоны):** механизм провайдеров (`05` §1) — серверный `config/`=дефолт, per-project override в `workspace/`, инструменты правят копию-данные; предпочтительно на том же `channel_config`. §5.0. A7-1 (пороги) склонён туда же.
  - **A7-2 (алгоритм):** локальный скрипт (n-gram, без внешнего API) + `.yaml`-конфиг, который ИИ настраивает сам; расчёт в `core/`/`scripts/`, пишет ячейку (не формула Excel). §2.
  - **A7-3 (фон):** планировщика НЕТ — все фоновые проверки (уникальность, HP/статус файлов) дёргает сам Клод on-demand, сервер рекомендует момент реакцией. §3 переписан. A7 больше не зависит от I5.
  - **A7-4 (сцены) РЕШЁН дизайном (§4.2/4.3):** спроектированы недостающие листы — `SCENE_ASSET_SEQUENCE` (упорядоченный лог ассетов → «порядок появления»/счётчики), обе оценки сцены колонками в `SCENES` (крит.1 `scene_pattern_uniqueness` 🆕 + крит.2 `scene_uniqueness` — проверить на .xlsx), **сводный `SCENE_UNIQUENESS_ROLLUP`** (сценарный патерн+патерн сцены+сцена в одну строку = полная уникальность сцены наглядно, предложение владельца). **Границы сцены** (`scene boundaries`) — сцена привязана к полному патерну ИЛИ его части, юзер+ИИ решают вместе → в память; в таблице связка по ID (`bound_pattern_id`/`boundary_ref`). §4.3.
  - **A7-1 (пороги) РЕШЁН:** пороги + веса `full_scene_uniqueness` — per-project override (§5.0). **Все 5 OQ закрыты, дизайн A7 готов; остаётся исполнение на A7.1.**

**Рубрика зрелости (`07`) обновлена под A7 + указания владельца:**
- **DIM-13…16 добавлены** — внутренняя инженерная дисциплина (проектные «паттерны»-скилы): DIM-13 качество кода (`code-quality`), DIM-14 анти-хардкод (`anti-hardcode`), DIM-15 конвенции/структура (`project-conventions`), DIM-16 безопасность-как-паттерн (`security-reviewer`, отличие от DIM-3/4/5 = структурный паттерн vs покрытие угроз). Опора: «что есть сейчас» vs «что должно стать на серьёзной зрелости».
- **Общий узел четырёх — монолит A2:** пока `tools/` пусты и логика в `server.py`, адгезия DIM-13/15 (+частично 14) упёрта в L1 → вывод: A2 = разблокировщик 4 измерений, не только архитектура.
- **FFmpeg ИСКЛЮЧЁН из оценки пока** (владелец): внешняя MCP-интеграция с особенностями → честно не оценить, на последок; P1 в карте развития помечен «последним, тогда же вводим в оценку». DIM-12 меряем по data/pattern-поверхности.
- **A7 в карте развития:** расширяет DIM-12 (data-продукт), обязан строиться ПО DIM-13/14/15 как эталон (per-project override/тонкие обёртки/git-native). Матрица вердикта + карта развития + вердикт-абзац обновлены.

**Закрытие проходов (`08`) — Definition-of-Closed + привязка к зрелости (указание владельца):**
- **§3.1** — ревью-прогоны сессии привязаны к измерениям: `quality`→DIM-13(+10/1), `style/structure`→DIM-14/15, `systems/security`→DIM-3/4/5/16(+6/1/10).
- **§6 Definition-of-Closed** — проход честно закрыт только через 3 ступени: **A** полный обмер ВСЕХ файлов (архитектурные аномалии + нарушения паттернов `D#/G#`/DIM-13…16) + «что есть vs что должно быть» по системам → F# в `02`; **B** адаптировать сильные готовые решения с GitHub (правило §4), **КРОМЕ безопасности** — паттерны+механизмы фиксов уже собраны в `06`, повторно не ищем; **C** двухступенчатое подтверждение: C1 простые тесты (`tests/quick/`) = код-пруф теории → C2 симуляции (реальные инструменты, живой сервер) = финал-пруф, **обязателен доступ на чтение консоли** (иначе эффект не наблюдаем). Обе ступени зелёные = закрыто.
- **Проводка:** DIM поднимается в `07` только после ступени C (баннер `07`); README-механика ссылается на `08 §6`; console-read занесён в `03` («особенности, которые нельзя потерять»).

**Тесты:** кода не трогал (только `docs/roadmap/`) → baseline держится.

**Коммиты:** `50e634a` (A7 подсистема) · `f631156` (рубрика DIM-13..16) · `docs(roadmap): pass closure — pass↔maturity binding + Definition-of-Closed (session 14)`.

---

## RESUME (следующая сессия) — два открытых трека

### Трек Б (новый, из сессии 9) — реализация защиты + тестов
P0-митигации из `06 §D`: (1) write-type allowlist (F34, декларативно + choke-point + `FILE_TYPE_FORBIDDEN`); (2) провенанс-маркировка workspace-вывода (F33/OUT1); (3) containment на write/move/delete + destructiveHint (OUT5); (4) app-level auth (D3/G18) → identity-rate. Затем раннер `tests/agent_swarm/test_agent_swarm.py` по `patterns.yaml`. Готовые сейчас тесты (структура готова): **E-A** (циклы рекомендаций) / **E-I** (поиск). Метод: `security-reviewer`/`mcp-developer` → `test-master` → findings+журнал → коммит.

## RESUME (альтернативный трек) — A1′ часть 2

**Два трека, оба готовы:**
1. **Код (loader):** `core/engine/table_materializer.py` — по `tables_pending` грузит `*.schema.yaml`, материализует книгу через `core/excel` (таблица маппинга в `spec/TABLE_SCHEMA_FORMAT.md`). Контракт ToolResult + факты. Подключить как фаза ТАБЛИЦЫ (отдельный вызов после structure_create — НЕ ломать 35/35). Тесты новые + structure зелёные.
2. **Авторинг схем:** остальные 6 книг из `spec/schemas/*.schema.md` → `config/templates/tables/*.schema.yaml` (competitor_channel_data малая — следующей; video_data с SCENES🆕/статусами — руками по `ИНСТРУКЦИЯ_шаблоны §5.2/5.3`).

Рекомендация: сперва loader на proof-файле network_config (докажет цикл end-to-end), потом массовый авторинг. **Уточнить у владельца:** где реальные .xlsx-книги (для интроспектора вместо ручного авторинга ~90 листов)?

Открыто: **F19** (LICENSE). Метод: `mcp-developer`/`project-conventions` → тесты → findings+журнал → коммит+push → память.

NB для I3: firewall 1/4 + tunnel 19/20 = integration (нужен живой сервер/cloudflared) → в CI skip/mark, гонять in-process (audit/search/structure/tables).

Метод: воркстрим → `engineering-questions` → домен-скил → правки → тесты зелёные → обновить `02_findings.md` + журнал → коммит с отчётом → память.
