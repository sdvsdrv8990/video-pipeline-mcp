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

**Коммит:** `docs(roadmap): target-structure conformance + F23 spec conflict (session 8)`.

---

## RESUME (следующая сессия) — A1′ часть 2

**Два трека, оба готовы:**
1. **Код (loader):** `core/engine/table_materializer.py` — по `tables_pending` грузит `*.schema.yaml`, материализует книгу через `core/excel` (таблица маппинга в `spec/TABLE_SCHEMA_FORMAT.md`). Контракт ToolResult + факты. Подключить как фаза ТАБЛИЦЫ (отдельный вызов после structure_create — НЕ ломать 35/35). Тесты новые + structure зелёные.
2. **Авторинг схем:** остальные 6 книг из `spec/schemas/*.schema.md` → `config/templates/tables/*.schema.yaml` (competitor_channel_data малая — следующей; video_data с SCENES🆕/статусами — руками по `ИНСТРУКЦИЯ_шаблоны §5.2/5.3`).

Рекомендация: сперва loader на proof-файле network_config (докажет цикл end-to-end), потом массовый авторинг. **Уточнить у владельца:** где реальные .xlsx-книги (для интроспектора вместо ручного авторинга ~90 листов)?

Открыто: **F19** (LICENSE). Метод: `mcp-developer`/`project-conventions` → тесты → findings+журнал → коммит+push → память.

NB для I3: firewall 1/4 + tunnel 19/20 = integration (нужен живой сервер/cloudflared) → в CI skip/mark, гонять in-process (audit/search/structure/tables).

Метод: воркстрим → `engineering-questions` → домен-скил → правки → тесты зелёные → обновить `02_findings.md` + журнал → коммит с отчётом → память.
