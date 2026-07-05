# 02 — Реестр находок (F#)

> Пополняется каждым прогоном. Severity: 🔴 блокер enterprise · 🟠 важно · 🟡 желательно · ⚪ мелочь.
> «Пруф» — как проверено. «→» — воркстрим-получатель из `01_master_roadmap.md`.
> Сессия 1: посев тремя прогонами на **скелетной** глубине (структурные находки). Глубина по коду — в фазовых сессиях.

## Прогон 1 — Качество (skill: code-quality)

| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| F1 | 🔴 | `server.py` — 1521-строчный монолит: вся регистрация/логика инструментов в одном файле вместо тонких обёрток | `wc -l server.py` | A2 |
| F2 | 🔴 | Декларативный слой `config/ops/*.ops.yaml` пуст → архитектура «обёртка→ops→engine» не реализована | `find config/ops -type f` → ∅ | A1, A3 |
| F3 | 🟠 | Провайдеры ffmpeg/tts/stt/img = `NotImplementedError` (честные стабы — G16 соблюдён, но продукта нет) | grep `NotImplementedError` | P1–P4 |
| F4 | 🟠 | `core/search` (MiMo): 0 тестов, не ревьюен — незрелый код в проде | память `core-search-subsystem` | A5, I7 |
| F5 | 🟠 | Система реакций (проверено по коду `reactions.py`): DEFAULT-fallback хардкодит `UNKNOWN_ERROR` (не из реестра), **не** ставит `reaction_class`, игнорит `DEFAULT.message_template` (берёт только `recovery.reason`). NB: `ErrorDetail.reaction_class` существует — memory-D27 «класс теряется» частично устарел | grep `get_error` в `core/reactions/reactions.py` | A6 |
| F6 | 🟡 | `id_generator` / `IDGenerator` — D28: dead inject-param / недёрнутые пути (проверить актуальность после table-tools) | память `table-tools-implemented` (D28 частично закрыт) | A6 |

## Прогон 2 — Стиль / структура (skills: project-conventions + anti-hardcode)

| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| F7 | 🔴 | README описывает несуществующую структуру: `config/ops/*.ops.yaml`, `paths.yaml`, `model_routing.yaml`, тонкие обёртки, pipeline — ничего из этого нет на диске | сверка README ↔ `ls` | A4 (truth-up) |
| F8 | 🟠 | Пустые заскаффолженные каталоги: `tools/{5}`, `pipeline/{entry_points,steps}`, `scripts/` — структура-обещание без наполнения | `ls -R` | A1/A2/P5/P6 |
| F9 | 🟠 | `docs/dev/audit/` отсутствует целиком — словарь D#/G# без артефактов, невоспроизводим/нешарибелен | `ls docs/dev/audit` → нет | X1/I8 |
| F10 | 🟡 | Хардкод-хуки (память audit-v2): stt `device=cuda` захардкожен; ffmpeg `render_full_pipeline` busy-loop без sleep | память `audit-v2-task` deferred-hooks | P1, P3, anti-hardcode |
| F11 | 🟡 | Провайдеры: api_key-гигиена tts/img; `raw_response` может течь в `_map_error` (D23) | память audit-v2 deferred | I6, P2/P4 |

## Прогон 3 — Системы / безопасность (skills: security-reviewer + test-master)

| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| F12 | 🔴 | `tests/` в `.gitignore` — тесты не версионируются; регресс невозможно защитить в CI | `.gitignore` стр. `tests/` | I1 |
| F13 | 🔴 | Нет CI/CD — ни линта, ни типов, ни прогона тестов, ни security-scan на PR | `.github/workflows` нет | I3 |
| F14 | 🔴 | Нет app-level auth: за туннелем один клиент, IP-гранулярность бесполезна (G18); нет аутентификации на уровне приложения | память `firewall-audit`, G18 | I6 |
| F15 | 🟠 | D3 (открыт) — предполагаемый дефект из v2-аудита, не закрыт; D29 (открыт) — traversal через `state_manager`, D1 закрыт лишь частично | память `audit-v2-task` (D3 OPEN, D29 🟠) | I6 |
| F16 | 🟠 | `threat_landscape.md` отсутствует на диске (заявлен в скиле security-reviewer как источник угроз) | `find threat_landscape*` → ∅ | I8, security |
| F17 | 🟠 | Секрет-гигиена: `tunnel.yaml` (D31) — уже в .gitignore, но проверить отсутствие ключей в трекнутых файлах; `.env`-стратегия | grep `token/api_key`, gitleaks | I3, I6 |
| F18 | 🟡 | Симуляционные тесты (virus/cache/bot-army) — есть в скиле test-master как замысел, но покрытие на диске не подтверждено | `ls tests/` (gitignored) | I7 |

## Открытые вопросы (решить в фазах)

- **OQ1:** README переписать под факт (быстро) или догнать код до README (долго)? → влияет на A2/A4 vs пересмотр дизайна.
- **OQ2:** Аудит D#/G# — реконструировать `docs/dev/audit/` или единый источник = этот реестр F#? → X1.
- **OQ3:** `docs/dev` разгитигнорить (версионировать доки) или оставить приватным? → I1/I8 + пожелание владельца «отозвать позже».
