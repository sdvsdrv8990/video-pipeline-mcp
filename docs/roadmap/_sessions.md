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

## RESUME (следующая сессия)

**Фаза 0, воркстрим `I2`** — Packaging: `pyproject.toml` (PEP 621), пиннинг зависимостей из `requirements.txt`, entry-points, `install.sh`/`run.sh` truth-up. Затем `I4` (ruff/mypy/pre-commit) → `I3` (CI: lint+типы+pytest+security-scan, теперь есть что гонять — тесты в git).

Метод: воркстрим → `engineering-questions` → домен-скил → правки → тесты зелёные → обновить `02_findings.md` + журнал → коммит с отчётом → память.
NB для I3: часть сьютов (firewall 1/4, tunnel 19/20) требует живого сервера/cloudflared — в CI помечать как integration/skip, гонять in-process (audit/search/structure/tables).
