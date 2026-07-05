# СХЕМА КНИГИ: `video_data.xlsx` (наше видео)

> Уровень: **видео** (центральная книга, Source of Truth конкретного видео).
> Легенда: `W` writable (через очередь) · `F` computed/formula (Read-Only) ·
> `id` ключ · `fk` ссылка · `enum` набор+цвета. Запись — только через очередь;
> формулы (`=`) не перезаписывать. Термины: книга/лист/столбец/строка.
>
> **Добавления к оригиналу (`🆕`):** лист `SCENES` (бывш. `SCENE_BREAKDOWN`, достроен) ·
> лист `RENDERS` (смонтированные видео по стадиям draft/final) ·
> `global_asset_id`+`variation_id` + HP вариаций + производственный статус в `ASSETS_USED` ·
> сцена убрана из `UNIQUENESS`-секции-3 (оставлена ссылка).
> **Принцип «нет данных = не ошибка»:** нет ассетов/вариаций/метрик — норма; computed
> определены на пустом входе; «плохо посчиталось» → alert, «нечем считать» → реакция.

---

## Лист 1: `META` — метаданные видео

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `video_id` | string | id | генерится сервером |
| `title` | string | W | макс 100 |
| `url` | string | W | пусто до публикации |
| `channel_id` | string | fk | Read-Only |
| `publish_date` | date | W | |
| `duration_sec` | integer | W | |
| `niche` | string | fk | наследуется из channel_data, Read-Only |
| `type` | string | F | фикс. `"own"` |
| `status` | enum | W | статус видео (палитра ниже) |
| `target_audience_category` | enum | W | C1/C2/C3 |
| `prediction_id` | string | fk | → CONTENT_PREDICTION (нишевый уровень) |

**enum `status`:** `IDEA`(серый) · `DRAFT`(серый светл.) · `IN_PRODUCTION`(оранжевый) ·
`RENDERING`(синий) · `PUBLISHED`(зелёный) · `ARCHIVED`(серый тёмн.).

---

## Лист 2: `PERFORMANCE` — метрики (снимок + формулы)

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `views` `likes` `comments` `shares` | integer | W | импорт YouTube API |
| `ctr_percent` | float | W | |
| `avg_watch_sec` | float | W | |
| `retention_percent` | float | F | `=avg_watch_sec/duration_sec*100` |
| `like_rate` | float | F | `=likes/views*100` |
| `comment_rate` | float | F | `=comments/views*100` |
| `engagement_rate` | float | F | `=(likes+comments+shares)/views*100` — снимок вовлечённости |
| `performance_score` | float | F | `=like_rate*0.4+comment_rate*0.3+(retention/100)*0.3` |
| `prediction_accuracy` | enum | F | ACCURATE/CLOSE/MISS/PENDING (формула по status) |

> Динамика вовлечённости во времени (день 1/7/30) отложена — для MVP снимок+тренд.
> При нуле просмотров формулы определены (не `#DIV/0!`) — «нет данных не ошибка».

---

## Лист 3: `TRANSCRIPT` — пословная транскрипция по сценам

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `scene_id` | string | id | генерится сервером (S01…) |
| `timestamp_start` `timestamp_end` | string | F | MM:SS, из транскрибации после озвучки |
| `duration_sec` | integer | F | `=end-start` |
| `act_number` | integer | W | 1–7 |
| `text` | string | F | дословно |
| `word_count` | integer | F | `=COUNTWORDS(text)` |
| `pace_wpm` | float | F | `=word_count/(duration_sec/60)` |
| `energy_level` | integer | W | 1–5 |
| `notes` | string | W | |

> `scene_id` здесь — источник тайминга для листа `SCENES` (join-ключ).

---

## Листы 4–10: `ACT_1_HOOK` … `ACT_7_TRUTH` (7 идентичных)

> 7 листов одной структуры, отличается только `act_name` (HOOK/SETUP/NUMBERS/
> MECHANISM/PARALLEL/IMPACT/TRUTH). Спецификация одна на все семь.

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `act_name` | string | F | фикс. название акта |
| `timestamp_start` `timestamp_end` | string | F | MM:SS |
| `duration_sec` | integer | F | |
| `duration_pct` | float | F | `=duration_sec/META!duration_sec*100` |
| `recommended_duration_sec` | integer | fk | из channel_data → STYLE_DNA, Read-Only |
| `status` | enum | F | `=IF(>rec*1.3,"LONG",IF(<rec*0.7,"SHORT","OK"))` |
| `text` | string | W | резюме акта 1–2 предл. |
| `technique_1/2/3` | string | W | техники акта |
| `visual_technique` `audio_technique` | string | W | |
| `effectiveness_score` | integer | W | 1–5 |
| `retention_impact` | enum | W | HIGH/MED/LOW |
| `notes` | string | W | |

---

## Лист 11: `TECHNIQUES` — реестр приёмов

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `technique_id` | string | id | генерится сервером |
| `timestamp` | string | W | MM:SS |
| `act_number` | integer | W | 1–7 |
| `technique_type` | string | W | stat_shock/storytelling/… |
| `description` | string | W | |
| `effectiveness` | integer | W | 1–5 (после публикации) |
| `psychological_pattern_id` | string | fk | P01–P12 |
| `audience_category` | string | W | C1/C2/C3 |

---

## Лист 12: `ASSETS_USED` — ассеты видео (🆕 ключи + статусы)

> 🆕 Ключ согласован с каноном: `global_asset_id` (какой мастер) + `variation_id`
> (какая вариация, пусто = сам мастер). HP вариаций живёт ЗДЕСЬ (вариации — сущности
> видео). Производственный статус (применение в этом видео) — тоже здесь.

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `global_asset_id` | string | id/fk 🆕 | мастер, от которого ассет (канонический ключ) |
| `variation_id` | string | id 🆕 | какая вариация; пусто = сам мастер |
| `asset_type` | enum | W | svg/music/sound/transition/effect |
| `is_master` | boolean | W | TRUE = мастер, не вариация |
| `description` | string | W 🆕 | краткое — Claude понимает ассет не открывая файл |
| `system_status` | enum | W 🆕 | HP вариации (см. палитру) |
| `production_status` | enum | W 🆕 | применение в видео (см. палитру) |
| `scene_id(s)` | string | W | список сцен (S01,S03…) |
| `usage_count_in_video` | integer | W | сколько раз в этом видео |
| `last_used_video_n` | integer | W | |
| `videos_since_last_use` | integer | F | `=current_video_n - last_used_video_n` |
| `uniqueness_score` | float | F | строгая для svg, мягкая для music/sound/transition; 0 ассетов = валидно |

🆕 **enum `system_status`** (HP): `active`(зелёный) · `damaged`(жёлтый) · `moved`(синий) ·
`deleted`(серый, soft-delete). 🆕 **enum `production_status`:** `under_review`(оранжевый) ·
`accepted`(зелёный) · `rejected`(красный). Разные оси: «отклонён» ≠ «удалён». Смена — `table_set`.

---

## Лист 13: `UNIQUENESS` — итог уникальности видео (🆕 секция сцен убрана)

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| **Секция 1 — по типам** | | | |
| `script_score` | float | F | `=1.0` (скрипт уникален) |
| `svg_scene_score` | float | F | `=AVERAGE(uniqueness всех svg)` |
| `music_score` `sound_score` `transition_score` | float | F | мягкие формулы (пул ниши) |
| **Секция 2 — итог** | | | |
| `overall_uniqueness` | float | F | `=script*0.35+svg*0.40+music*0.10+sound*0.08+transition*0.07` |
| `tier` | enum | F | `=IF(>=0.80,"HIGH",IF(>=0.60,"MED","LOW"))` |
| **Секция 3 — по сценам** | | | |
| `scenes_uniqueness_ref` | — | F 🆕 | ССЫЛКА на лист `SCENES.scene_uniqueness` (дубль убран) |

> 🆕 Было: `scene_id, svg_count, avg_svg_uniqueness, scene_tier` (только svg). Перенесено
> и расширено в лист `SCENES` (svg+аудио+переходы). Здесь оставлена только ссылка на итог.

---

## Лист 14: `SCRIPT_DNA_APPLIED` — применённые правила стиля

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `rule_id` | string | fk | из STYLE_DNA, Read-Only |
| `category` | enum | F | forbidden_words/pacing/rhetoric/tone |
| `applied_in_act` | integer | W | 1–7 |
| `how_it_was_applied` | string | W | пример из текста |
| `human_feedback` | string | W | комментарий человека |
| `resolved` | enum | W | YES/NO |

---

## Лист 15: `SCENES` 🆕 (бывш. `SCENE_BREAKDOWN`, достроен)

> Единый лист сцены: точка сборки тайминга + состава (фрагменты по ТИПАМ) + уникальности.
> Достроен из `SCENE_BREAKDOWN`. Привязка к фразе — через `SCENE_SEMANTICS.script_phrase`
> по `scene_id`. Сцена больше НЕ живёт в `UNIQUENESS`-секции-3.
> Фрагменты разведены по типам: SVG-роли (фон/персонажи/компоненты), музыка, звуки,
> фильтры, переходы. Какие типы активны — из `channel_config.SCENE_PROFILE`; видео
> собирает только из включённых типов (выключенный = «тихий столбец», не считается/не шумит).

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `scene_id` | string | id | join-ключ к TRANSCRIPT/SEMANTICS/ASSETS_USED |
| `act` | integer | fk | 1–7 |
| `timestamp_start` `timestamp_end` | string | F | из TRANSCRIPT (тайминг → ограничение сцены) |
| `text_snippet` | string | F | фрагмент текста сцены |
| `bg_assets` | string[] | W 🆕 | SVG-роль: фон |
| `character_assets` | string[] | W 🆕 | SVG-роль: персонажи |
| `component_assets` | string[] | W 🆕 | SVG-роль: прочие компоненты |
| `music_assets` | string[] | W | подложка |
| `sound_assets` | string[] | W 🆕 | звуковые эффекты |
| `filter_assets` | string[] | W 🆕 | фильтры/обработка |
| `transition_assets` | string[] | W | переходы |
| `scene_uniqueness` | float | F 🆕 | взвешенная композиция по типам; веса/активность из `channel_config.SCENE_PROFILE`; выключенный тип не входит; определена при пустом |
| `animation_order` | string | W | порядок появления элементов |
| `animation_speed` | enum | W | Fast/Medium/Slow |
| `retention_at_this_sec` | float | F | из YouTube Analytics |
| `retention_impact` | enum | F | HIGH/MED/LOW по дельте retention |

---

## Лист 16: `SCENE_SEMANTICS` — сенсорный слой сцены

> Несёт `script_phrase` — точную привязку сцены к фразе сценария (по `scene_id`).

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `scene_id` | string | id | |
| `timestamp` | string | F | MM:SS |
| `script_phrase` | string | F | ключевая фраза, которую визуализирует сцена |
| `character` | string | W | |
| `emotion` | enum | W | anger/joy/shock/fear/neutral |
| `color_primary` `color_secondary` | string | W | HEX |
| `lighting` | enum | W | high_key/low_key/neutral |
| `contrast_level` | enum | W | high/med/low |
| `camera_move` | enum | W | fast_zoom_in/slow_pan/static/zoom_out |
| `sound_cue` | string | W | |
| `music_mood` | string | W | |
| `voice_tone` | enum | W | whisper/shout/calm/urgent |
| `gesture` `eye_direction` | string | W | |
| `visual_metaphor` | string | W | |
| `text_on_screen_style` | string | W | |
| `retention_impact` | enum | F | HIGH/MED/LOW + % |
| `technique_linked` | string | fk | → TECHNIQUES |
| `psychological_pattern_id` | string | fk | P01–P12 |
| `audience_category` | string | W | C1/C2/C3 |

---

## Листы 17–19: `VISUAL_/SCRIPT_/AUDIO_PATTERNS_USED` (общий шаблон)

> Три листа близкой структуры — реестры применённых паттернов по типу. Общие столбцы
> ниже, дельты — в примечании.

**Общие столбцы:**

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `pattern_id` | string | fk | из соответствующей *_PATTERN_LIBRARY (канал) |
| `scene_id` | string | fk | |
| `context_tag` | string | W | |
| `uniqueness_score` | float | F | уникальность применения; пустой вход определён |
| `competitor_ref_id` | string | W | источник заимствования (опц.) |
| `retention_impact` | enum | F | HIGH/MED/LOW |
| `psychological_pattern_id` | string | fk | P01–P12 |
| `audience_category` | string | W | C1/C2/C3 |
| `notes` | string | W | |

**Дельты по листам:**
- `VISUAL_PATTERNS_USED` (17): + `visual_solution, character, emotion, color_primary, action`.
- `SCRIPT_PATTERNS_USED` (18): + `pattern_description, sentence_avg_length (F),
  short_sentence_pct (F), rhetorical_questions_count, transition_type`.
- `AUDIO_PATTERNS_USED` (19): + `pattern_description, sound_cue_type, music_mood,
  voice_tone, pause_duration_sec, tempo_bpm, volume_change`.

---

## Лист 20: `RETENTION_PATTERNS` — синергии (комбинации)

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `pattern_id` | string | id | RP_COMB_… |
| `context_tag` | string | W | |
| `combination_description` | string | W | |
| `visual_pattern_id` | string | fk | → лист 17 |
| `script_pattern_id` | string | fk | → лист 18 |
| `audio_pattern_id` | string | fk | → лист 19 |
| `retention_impact` | string | F | HIGH +15% |
| `timestamp_example` | string | W | |
| `scene_id` | string | fk | |
| `audience_category` | string | W | |
| `psychological_patterns_triggered` | string[] | W | список P01–P12 |
| `confidence` | enum | W | HIGH/MED/LOW |
| `notes` | string | W | |

---

## Лист 21: `RENDERS` 🆕 — смонтированные видео по стадиям

> Рендер — не один файл: сначала черновой, потом финальный (плюс, возможно, форматы).
> Лист связывает стадии, хранит путь готового файла и его статус. Файл физически лежит
> в `videos/<video>/renders/` (отдельно от `assets/`-сырья), имя выводится из `video_id`.

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `render_id` | string | id | ключ рендера |
| `render_stage` | enum | W | `draft` / `final` |
| `render_profile` | string | fk | профиль из `channel_config.RENDER_CONFIG` (codec/res/aspect) |
| `format_label` | string | W | напр. `long_16x9` / `short_9x16` (если форматов несколько) |
| `file_path` | string | W | путь в `videos/<video>/renders/` (с `video_id` в имени) |
| `file_name` | string | F | `{video_slug}_{render_stage}[_{format_label}].mp4` |
| `render_status` | enum | W | `queued` / `rendering` / `ready` / `failed` (см. палитру) |
| `derived_from_render_id` | fk | W | привязка финала к черновику (история стадий) |
| `duration_sec` | integer | F | из готового файла (verify) |
| `file_verified` | boolean | F | файл ≠ 0 байт И играбелен (реальность-чек) |
| `notes` | string | W | |

🆕 **enum `render_status`** (палитра): `queued`(серый) · `rendering`(синий) ·
`ready`(зелёный) · `failed`(красный). Сбой рендера — локальный (FFmpeg), см. ИНСТРУКЦИЯ
видеомонтажа. Путь пишется через очередь; готовый файл не анонимен (связан с `video_id`).

---

## Лист 22: `POST_MORTEM` — журнал ошибок и уроков

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `issue_type` | enum | W | retention_drop/pacing_issue/style_violation/visual_mismatch/factual_error |
| `timestamp` | string | W | |
| `scene_id` | string | fk | |
| `act` | integer | fk | 1–7 |
| `observation` | string | W | что пошло не так |
| `instruction_for_next_video` | string | W | инструкция на будущее |
| `applied_in_future_video_id` | string | fk | где применено |
| `resolved` | enum | W | YES/NO |

> Связь с `project_memory.md`: развёрнутые уроки и их результаты — в памяти видео/канала
> (по ID), `POST_MORTEM` хранит факт проблемы, память — решение и его эффект.

---

## Лист 23: `ANALYTICS` — дашборд видео (Read-Only)

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `metric_group` | string | F | Performance/Act Structure/Uniqueness/… |
| `metric_name` | string | F | |
| `value_this_video` | float/str | F | |
| `value_channel_avg` | float/str | F | подтягивается из channel_data |
| `delta_or_status` | string | F | `=this-avg` или OK/SHORT/LONG/HIGH/MED/LOW |
| `ai_action_flag` | string | F | ⚠️ Rotation needed / Review ACT_3 |

> Весь лист `F`. Видео считает СВОЁ; сравнение с каналом — подтяжка из channel_data,
> не пересчёт. Блоки: производительность vs бенчмарк, структура актов, уникальность,
> сцены под угрозой, тренды, здоровье ротации.

---

## РЕШЕНИЯ ПО ЭТОЙ КНИГЕ

1. **`SCENE_BREAKDOWN` → `SCENES`** (достроен): + аудио/переходы в состав, +
   `scene_uniqueness` (svg+аудио+переходы). Сцена убрана из `UNIQUENESS`§3 (ссылка).
   Аудит был неточен («листа сцены нет») — лист был, просто данные сцены разбросаны.
2. **`ASSETS_USED`:** ключ → `global_asset_id`+`variation_id` (канон); +`system_status`
   (HP вариаций), +`production_status`, +`description`. Старый `master_id`/суффикс `-V2`
   заменён явной парой ключей.
3. **Вовлечённость:** снимок (`engagement_rate`) есть; динамика во времени отложена.
4. **Привязка сцена→фраза** уже была — `SCENE_SEMANTICS.script_phrase` по `scene_id`.
   Новый дублирующий `phrase_ref` НЕ заводим.
5. Флаги `W/F` взяты из оригинала (там размечены Read/Write/Formula) — точны.
