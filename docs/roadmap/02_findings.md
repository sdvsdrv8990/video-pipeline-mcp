# 02 — Реестр находок (F#)

> Пополняется каждым прогоном. Severity: 🔴 блокер enterprise · 🟠 важно · 🟡 желательно · ⚪ мелочь.
> «Пруф» — как проверено. «→» — воркстрим-получатель из `01_master_roadmap.md`.
> Сессия 1: посев тремя прогонами на **скелетной** глубине (структурные находки). Глубина по коду — в фазовых сессиях.

## Прогон 1 — Качество (skill: code-quality)

| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| F1 | 🔴 | `server.py` — 1521-строчный монолит: вся регистрация/логика инструментов в одном файле вместо тонких обёрток | `wc -l server.py` | A2 |
| ~~F2~~ | ⚪ | **ОТОЗВАНО S4:** «config/ops пуст = архитектура сломана» — НЕВЕРНО. ops/model_routing **намеренно упразднены**, консолидированы в `channel_config.yaml → resource_limits` (`media_tools_deployment.md §0`). Не дефект — устаревший README. См. `05` §0 | design-решение владельца | A4 (README), не A1 |
| F3 | 🟠 | Провайдеры ffmpeg/tts/stt/img = `NotImplementedError` (честные стабы — G16 соблюдён, но продукта нет) | grep `NotImplementedError` | P1–P4 |
| F4 | 🟠 | `core/search` (MiMo): 0 тестов, не ревьюен — незрелый код в проде | память `core-search-subsystem` | A5, I7 |
| F5 | 🟠 | Система реакций (проверено по коду `reactions.py`): DEFAULT-fallback хардкодит `UNKNOWN_ERROR` (не из реестра), **не** ставит `reaction_class`, игнорит `DEFAULT.message_template` (берёт только `recovery.reason`). NB: `ErrorDetail.reaction_class` существует — memory-D27 «класс теряется» частично устарел | grep `get_error` в `core/reactions/reactions.py` | A6 |
| F6 | 🟡 | `id_generator` / `IDGenerator` — D28: dead inject-param / недёрнутые пути (проверить актуальность после table-tools) | память `table-tools-implemented` (D28 частично закрыт) | A6 |

## Прогон 2 — Стиль / структура (skills: project-conventions + anti-hardcode)

| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| F7 | 🟠 | README устарел относительно консолидированной архитектуры (S4): показывает `config/ops`/`paths.yaml`/`model_routing.yaml` (упразднены by design), И **умалчивает** о реальном — `core/firewall` (зрелейший), `core/search/excel/tables`, `config/{firewall,channel_config,tunnel}.yaml`. Врёт в обе стороны | сверка README ↔ диск | A4 (truth-up под факт) |
| F20 | 🟠 | Таблично-схемный слой отсутствует: `config/templates/tables/*.schema.yaml` пуст → `structure_create` фаза ТАБЛИЦЫ не имеет схем книг. Блокирует «yaml-генерацию таблиц с листами» | `config/templates/tables/` ∅ | A1′ |
| F21 | 🟠 | `scripts/introspect_tables.py` не существует → нет генерации схем из ~90 готовых Excel-книг (руками = недели). Причина пустого `scripts/` | `find introspect_tables.py` ∅ | A1′ |
| ~~F22~~ | ✅ | **РЕШЕНО S5 (OQ4):** 15 спек-файлов импортированы в трекаемый `docs/roadmap/spec/` (schemas/ + instructions/ + Бриф/project_memory.spec/media_tools_deployment) + индекс `spec/README.md` + ledger улучшений `spec/IMPROVEMENTS.md`. Оригиналы в `/home/admin/projects/` оставлены | `find docs/roadmap/spec` = 17 файлов | OQ4 ✔ |
| F8 | 🟠 | Пустые заскаффолженные каталоги: `tools/{5}`, `pipeline/{entry_points,steps}`, `scripts/` — структура-обещание без наполнения | `ls -R` | A1/A2/P5/P6 |
| F9 | 🟠 | `docs/dev/audit/` отсутствует целиком — словарь D#/G# без артефактов, невоспроизводим/нешарибелен | `ls docs/dev/audit` → нет | X1/I8 |
| F10 | 🟡 | Хардкод-хуки (память audit-v2): stt `device=cuda` захардкожен; ffmpeg `render_full_pipeline` busy-loop без sleep | память `audit-v2-task` deferred-hooks | P1, P3, anti-hardcode |
| F11 | 🟡 | Провайдеры: api_key-гигиена tts/img; `raw_response` может течь в `_map_error` (D23) | память audit-v2 deferred | I6, P2/P4 |
| F19 | 🟠 | Нет файла `LICENSE` — публичный репо без лицензии = юридически «all rights reserved», блокирует внешний вклад/использование. Владелец не выбрал лицензию (в pyproject помечено `Private :: Do Not Upload`) | `ls LICENSE*` → нет (S3) | I2/I8 (ждёт решения владельца) |

## Прогон 3 — Системы / безопасность (skills: security-reviewer + test-master)

| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| ~~F12~~ | ✅ | **РЕШЕНО S2 (I1):** `tests/` был свален в секцию «ДАННЫЕ» с `workspace/`. Убран из `.gitignore` (тесты = код); 23 файла (6 quick + 6 симуляций + scenarios.yaml) в git; `__pycache__`/`.pytest_cache` остаются игнорированы. Секретов в tests/ нет (проверено) | `git diff --cached` чист | I1 ✔ |
| F13 | 🔴 | Нет CI/CD — ни линта, ни типов, ни прогона тестов, ни security-scan на PR | `.github/workflows` нет | I3 |
| F14 | 🔴 | Нет app-level auth: за туннелем один клиент, IP-гранулярность бесполезна (G18); нет аутентификации на уровне приложения | память `firewall-audit`, G18 | I6 |
| F15 | 🟠 | D3 (открыт) — предполагаемый дефект из v2-аудита, не закрыт; D29 (открыт) — traversal через `state_manager`, D1 закрыт лишь частично | память `audit-v2-task` (D3 OPEN, D29 🟠) | I6 |
| F16 | 🟠 | `threat_landscape.md` отсутствует на диске (заявлен в скиле security-reviewer как источник угроз) | `find threat_landscape*` → ∅ | I8, security |
| F17 | 🟠 | Секрет-гигиена: `tunnel.yaml` (D31) — уже в .gitignore, но проверить отсутствие ключей в трекнутых файлах; `.env`-стратегия | grep `token/api_key`, gitleaks | I3, I6 |
| F18 | 🟡→🟢 | Симуляционные тесты **существуют на диске и теперь в git** (S2): `virus_injection`, `bot_army`, `cache_injection`, `cache_overflow`, `config_change`, `render_draft_final`. Остаётся: подтвердить, что зелёные в CI (часть требует живого сервера, как firewall 1/4) | `find tests/` после I1 | I7 |

## Открытые вопросы (решить в фазах)

- **OQ1:** README переписать под факт (быстро) или догнать код до README (долго)? → влияет на A2/A4 vs пересмотр дизайна.
- **OQ2:** Аудит D#/G# — реконструировать `docs/dev/audit/` или единый источник = этот реестр F#? → X1.
- **OQ3:** `docs/dev` разгитигнорить (версионировать доки) или оставить приватным? → I1/I8 + пожелание владельца «отозвать позже».
