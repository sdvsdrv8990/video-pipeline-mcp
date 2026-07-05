# 05 — Система данных / шаблонов / media: спеки ↔ код

> Занесено по указанию владельца (Сессия 4). Источник — спек-файлы в `/home/admin/projects/`
> + `docs/dev/media_tools_deployment.md`. **Принцип владельца:** не ломать работающие функции,
> а найти чего не хватает чтобы доделать, и **синхронизировать** спеку с кодом (код успел уйти
> вперёд спек в одних местах и отстать в других).

## 0. Три слоя дизайна (в разное время) — что канонично

| Слой | Где | Статус как истины |
|---|---|---|
| **README** (`config/ops/*.ops.yaml`, `model_routing.yaml`, `.xlsx`-конфиг) | репо | ❌ УСТАРЕЛ — самый старый, вводит в заблуждение |
| **Спеки дизайна** (`media_tools_deployment.md` + 7 `*.schema.md` + `ИНСТРУКЦИЯ_*`) | `docs/dev/` + `/home/admin/projects/` | ✅ КАНОН намерения — актуальная задумка |
| **Код** | `core/`, `config/`, `server.py` | ✅ КАНОН поведения — местами ушёл дальше спек |

**Следствие для F2/F7 (исправление):** «config/ops пуст = архитектура сломана» — **НЕВЕРНО.**
Декларативный `config/ops` и `model_routing.yaml` **намеренно упразднены**: реестр операций и
fallback-цепочки консолидированы в `config/channel_config.yaml → resource_limits`. Один конфиг =
один источник правды (`media_tools_deployment.md §0`). README просто не обновлён под это решение.
>
> **F23 РЕШЁН (S8, OQ6 = reconcile-by-purpose):** конфликт снят по назначению. `config/ops/{filesystem,tables,
> excel}.ops.yaml` = реестр операций tool-обёрток (закон §3) → **строим** (A3). **media** остаётся в
> `resource_limits` (media-план), своего `media.ops.yaml` нет. Оба документа владельца правы в своём скоупе.

## 1. Целевая архитектура (из спек)

**Иерархия сущностей:** `niche → networks → channel → video` (+ ветка `competitors/` под нашим каналом).

**Конфиг:** консолидированный `config/channel_config.yaml` — 7 секций: `workflow_sequences`,
`publishing_schedule`, `resource_limits` (провайдер+модель+голос+лимиты+fallback+retry+sync_mode),
`metadata_defaults`, `automation_rules`, `scene_profile`, `render_config`. **НЕТ** отдельных
ops/model_routing — это by design.

**Шаблоны:** `config/templates/workspace/*.tpl.yaml` (6: niche/network/channel/video/
competitor_channel/competitor_video) + `config/templates/tables/*.schema.yaml` (схемы книг).
Трёхфазное создание (папки→таблицы→файлы) с двойной проверкой (facts + чтение диска).

**Данные (Excel-книги, колонки заданы в `*.schema.md`):**
| Книга | Уровень | ~Листов | Схема-файл |
|---|---|---|---|
| `video_data.xlsx` | видео | ~22 | `video_data.schema.md` (META, SCENES🆕, RENDERS🆕, TRANSCRIPT, UNIQUENESS…) |
| `channel_data.xlsx` | канал | ~15 | `channel_data.schema.md` (VIDEOS_INDEX, статус-мастера…) |
| `network_config.xlsx` / `network_dashboard.xlsx` | сетка | — | `network_config.schema.md` (NETWORK_SCHEDULE_MASTER, SHARED_RESOURCES) |
| `competitor_channel_data.xlsx` | канал конкур. | ~10 | `competitor_channel_data.schema.md` (CHANNEL_META…) |
| `competitor_video_data.xlsx` | видео конкур. | ~19 | `competitor_video_data.schema.md` (META, PERFORMANCE — формулы) |
| `niche_network_data.xlsx` | ниша/конкур. | ~12 | `niche_network_data.schema.md` (COMPETITORS_INDEX…) |

Флаги колонок: `id` / `W` (writable) / `F` (formula, `computed:true writable:false`) / `fk` / `enum` (+`ui_colors`).

**Интроспектор:** `scripts/introspect_tables.py` — вытаскивает колонки из ~90 готовых Excel-книг →
генерит `schema.yaml` каждой (помечает `=`-столбцы `computed`). **Руками спекаем ТОЛЬКО** лист
`SCENES` + статус-столбцы (HP active/damaged/moved/deleted; производственный under_review/accepted/
rejected; статус видео draft…archived) — их в Excel ещё нет.

**Media-пайплайн** (`media_tools_deployment.md`): 10 инструментов STT(3)/TTS(4)/IMG(3) в `tools/media/`
→ адаптеры `core/providers/{stt,tts,img}` → всё из `resource_limits`. Именование ассетов
`{video_slug}_{tts|img}_{scene_id}.{wav|png}`. Сбои: LOCAL_INFERENCE_FAILED / PROVIDER_FAILED / CONTENT_REJECTED.

## 2. Статус спека ↔ код (проверено на диске, S4)

| Подсистема | Статус | Пруф |
|---|---|---|
| Workspace-шаблоны (6 tpl) | ✅ **DONE** | `config/templates/workspace/*.tpl.yaml` × 6 |
| `structure_create/link/migrate/status` | ✅ **DONE** | server.py + `tests/quick/test_structure.py` 35/35 |
| `channel_config.yaml` (7 секций) | ✅ **DONE** | grep секций — все на месте |
| Таблично-схемный слой `config/templates/tables/*.schema.yaml` | ❌ **MISSING** | `config/templates/tables/` пуст |
| `scripts/introspect_tables.py` | ❌ **MISSING** | не существует |
| Лист `SCENES` + статус-столбцы в книгах | ❌ **MISSING** | по спеке §5.2/5.3 — руками |
| Media-инструменты `tools/media/` + адаптеры | 🟠 **STUB** | провайдеры = NotImplementedError; план готов |
| Table-примитивы `core/tables`/`core/excel` + `table_*`/`excel_*` | ✅ **DONE** | tables 33/33 |

## 3. Спек-файлы — инвентарь и размещение

Сейчас лежат **вне репо** (`/home/admin/projects/*.md`) — не версионируются, риск потери (F22):
`ИНСТРУКЦИЯ_{шаблоны,инструменты,media_инструменты,видеомонтаж,структура_и_ядро}.md`,
`Бриф_табличные_инструменты.md`, `project_memory.spec.md`, 7× `*.schema.md`.
**Рекомендация:** импортировать в репо (`docs/dev/spec/` — приватно, или `docs/roadmap/spec/` —
трекается) как канон намерения. Ждёт решения владельца (см. §5).

## 4. Воркстримы (интеграция в master_roadmap)

**Исправление A-оси** (была основана на устаревшем README):
- ~~A1 «populate config/ops»~~ → **A1′ Таблично-схемный слой**: (a) `scripts/introspect_tables.py`
  на ~90 реальных книгах → `config/templates/tables/*.schema.yaml`; (b) руками лист `SCENES` +
  статус-столбцы (enum+ui_colors); (c) подключить схемы к `structure_create` фазе ТАБЛИЦЫ.
  Вход — 7 `*.schema.md`. **2–3 сессии.**
- ~~A3 «engine потребляет ops»~~ → **пересмотрен**: движок уже потребляет `channel_config` +
  workspace-tpl; остаётся подключить table-схемы (часть A1′). Отдельный ops-слой НЕ строим.
- **A2** (распил монолита server.py) и **A4** (README truth-up) — в силе; A4 теперь = «README под
  фактическую консолидированную архитектуру + пометить устаревшее».

**P-ось (media) уточнена** — `media_tools_deployment.md §4` даёт готовый порядок:
- P2 TTS: `prepare_tts_input`→`trigger`→`download+verify` (Фаза 1 плана).
- P3 STT: `trigger_transcription`→`parse_timestamps_and_silence` (Фаза 1).
- P4 IMG: `trigger`→`download+verify` (Фаза 2).
- **P4′ `tools/media/` обёртки** (stt_tools/tts_tools/img_tools) + регистрация в server.py.
- P-media Фаза 3: error/fallback/retry через `resource_limits`.

## 5. Открытые вопросы владельцу

- **OQ4:** импортировать спек-файлы из `/home/admin/projects/` в репо? Куда — `docs/dev/spec/`
  (приватно) или `docs/roadmap/spec/` (трекается)? Сейчас они вне контроля версий (F22).
- **OQ1 (обновлён):** README truth-up (A4) — теперь ясно, что переписывать под **консолидированную**
  архитектуру (channel_config-центричную), а не «догонять» несуществующий ops-слой. Рекомендация
  прежняя: сначала правда.
- **OQ5:** порядок — сначала таблично-схемный слой A1′ (закрывает «yaml-генерация таблиц с листами»)
  или media P2–P4 (продукт)? Оба разблокированы. A1′ логически ниже (media пишет в листы SCENES/…).

## Принцип синхронизации (директива владельца)
Не ломать работающие функции. На каждом воркстриме: (1) прочитать соответствующую спеку +
реальный код, (2) зафиксировать дельту спек↔код в `02_findings.md`, (3) достроить недостающее,
(4) обновить спеку под то, что код уже делает иначе/лучше. Спека и код сходятся навстречу.
