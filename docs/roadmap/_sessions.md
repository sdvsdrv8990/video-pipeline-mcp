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

## RESUME (следующая сессия)

**Ждёт решения владельца:** **OQ5** (порядок продукта: A1′ таблично-схемный слой vs media Фаза 1) · **OQ1** (README truth-up, A4) · **F19** (LICENSE, отложено).

**Варианты старта:**
- **A1′** — таблично-схемный слой: `scripts/introspect_tables.py` → `config/templates/tables/*.schema.yaml` (вход — `spec/schemas/*.schema.md`) + лист SCENES/статусы. Закрывает «yaml-генерацию таблиц с листами».
- **media Фаза 1** — P2 TTS + P3 STT по `spec/media_tools_deployment.md §4`.
- **Фаза 0 `I4`** (default инфра) — ruff/mypy/pre-commit → I3 CI.

Метод: воркстрим → `engineering-questions` → домен-скил → правки → тесты зелёные → `02_findings.md`+журнал → коммит+push → память.

NB для I3: firewall 1/4 + tunnel 19/20 = integration (нужен живой сервер/cloudflared) → в CI skip/mark, гонять in-process (audit/search/structure/tables).

Метод: воркстрим → `engineering-questions` → домен-скил → правки → тесты зелёные → обновить `02_findings.md` + журнал → коммит с отчётом → память.
