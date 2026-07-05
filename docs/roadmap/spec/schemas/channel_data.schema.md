# СХЕМА КНИГИ: `channel_data.xlsx` (наш канал)

> Полная переспека одной книги «с нуля» — источник для `schema.json` этой книги.
> Уровень: **канал**. Идём сверху вниз по иерархии (niche → network → **channel** → video).
>
> **Легенда флагов:** `W` = writable (ИИ пишет через очередь) · `F` = computed/formula
> (Read-Only, считает Excel/сервер) · `id` = первичный ключ строки · `fk` = ссылка на
> другую книгу/лист · `enum` = фиксированный набор значений (дропдаун + цвета).
> **Термины:** книга/лист/столбец/строка. Запись данных — только через очередь (`table_set`),
> структура — через `excel_*`. Статусы — `excel_set_validation` + `excel_apply_formatting` из схемы.
>
> **Что добавлено к оригиналу (помечено `🆕`):** HP мастеров (системный статус файла) на
> уровне канала; явный enum + палитра у статуса видео. HP вариаций и лист `SCENES` —
> НЕ здесь, они на уровне видео (см. схему `video_data`).
>
> **Принцип «нет данных = не ошибка».** Метрик, вариаций, ассетов, конкурентов может
> не быть — это норма, не сбой. Computed-столбцы **определены и на пустом входе**
> (0 вариаций → `effective_freshness` по `total_uses`, не падает). Граница двух
> рекомендаций: **«плохо посчиталось» → alert в таблицу** (`UNIQUENESS_ALERTS`);
> **«нечем считать» → реакция** (`MISSING_OPTIONAL_DATA`, warning). Развёрнутые решения
> и их последствия — в `project_memory.md` (не дублируют таблицы, ссылки по ID).

---

## Лист 1: `VIDEOS_INDEX` — сводка всех видео канала

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `video_id` | string | id | ключ |
| `title` | string | W | |
| `publish_date` | date | W | |
| `status` | enum | W 🆕 | статус видео (см. палитру ниже) |
| `duration_sec` | integer | W | |
| `views` | integer | W | импорт аналитики |
| `likes` | integer | W | импорт аналитики |
| `retention_pct` | float | W | импорт аналитики |
| `overall_uniqueness` | float | F | свод из `video_data` |
| `tier` | enum | F | HIGH/MED/LOW (из uniqueness) |
| `script_score` | float | F | свод из `video_data` |
| `svg_score` | float | F | свод из `video_data` |
| `music_score` | float | F | свод из `video_data` |
| `sound_score` | float | F | свод из `video_data` |
| `transition_score` | float | F | свод из `video_data` |
| `target_audience_category` | string | W | C1/C2/C3 |
| `performance_score` | float | F | свод/расчёт |

🆕 **enum `status`** (палитра): `draft`(серый) · `in_production`(оранжевый) ·
`rendering`(синий) · `ready`(жёлтый) · `published`(зелёный) · `archived`(серый тёмный).

---

## Лист 2: `CHANNEL_STATS` — агрегаты канала

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `total_videos` | integer | F | |
| `avg_retention` | float | F | |
| `avg_uniqueness` | float | F | |
| `avg_script_score` | float | F | |
| `avg_svg_score` | float | F | |
| `videos_high` | integer | F | счётчик по tier |
| `videos_med` | integer | F | |
| `videos_low` | integer | F | |
| `last_3_avg_uniqueness` | float | F | |
| `trend` | enum | F | UP/FLAT/DOWN |

Весь лист агрегатный → почти всё `F`. Записывать сюда руками нельзя.

---

## Лист 3: `ASSET_USAGE_HISTORY` — реестр МАСТЕР-ассетов канала (+ HP 🆕)

> Это реестр мастеров уровня канала, ключ — `global_asset_id`. Сюда добавляется системный
> статус мастера (HP), т.к. мастер физически принадлежит каналу. Производственного
> статуса здесь НЕТ (он на уровне видео, про использование). HP вариаций — на видео.

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `global_asset_id` | string | id | ключ мастера (канонический термин системы) |
| `asset_type` | string | W | svg/audio/transition/… |
| `description` | string | W 🆕 | краткое описание — Claude понимает ассет не открывая файл |
| `system_status` | enum | W 🆕 | HP мастера (см. палитру) |
| `used_in_videos` | string[] | W | список video_id |
| `total_uses` | integer | F | |
| `last_used_video` | string | W | |
| `videos_since_last_use` | integer | F | |
| `variation_count` | integer | F 🆕 | сколько вариаций мастера всего по каналу (агрегат снизу; 0 = валидно) |
| `effective_freshness` | float | F 🆕 | свежесть с поправкой на разнообразие: f(total_uses, variation_count), мягкий вес вариаций (коэф. в конфиге); определена при 0 вариаций |
| `rotation_status` | enum | F | `=IF(videos_since_last_use>=4,"OK","ROTATE")` |

🆕 **enum `system_status`** (HP, палитра): `active`(зелёный) · `damaged`(жёлтый) ·
`moved`(синий) · `deleted`(серый, soft-delete — обратимо). Смена — `table_set`;
необратимый `purge` — через `human_gate`.

---

## Лист 4: `UNIQUENESS_ALERTS` — алерты (только MED/LOW)

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `video_id` | string | fk | |
| `tier` | enum | F | MED/LOW (только проблемные) |
| `weak_dimension` | string | F | какое измерение просело |
| `score` | float | F | |
| `recommendation` | string | W | действие для исправления (в т.ч. «мало вариаций/ассетов — добери») |
| `signal_status` | enum | W 🆕 | жизненный цикл сигнала (см. палитру) |
| `flag_source` | enum | W 🆕 | `human_decision` / `ai_check` — чья воля за флагом (защита от тихого гашения ИИ) |
| `comment` | string | W 🆕 | почему помечен (вытекает из решения человека) |
| `outcome_ref` | string | W 🆕 | ссылка на результат решения (по ID; развёрнуто — в project_memory) |

🆕 **enum `signal_status`** (палитра): `active`(красный) · `confirmed`(оранжевый) ·
`actioned`(синий, сделана вариация/действие) · `dismissed_false_positive`(серый,
проверен и признан ложным). Гашение = смена статуса со следом, НЕ стирание.
`human_gate` при гашении сигнала высокой важности. `flag_source=ai_check` на важном
сигнале → требует подтверждения человека.

---

## Лист 5: `STYLE_DNA` — правила подачи (сценарий)

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `rule_id` | string | id | |
| `category` | enum | W | forbidden_words/pacing/rhetoric/tone |
| `rule_description` | string | W | |
| `example_from_human` | string | W | |
| `strictness` | integer | W | 1–5 |
| `applied_in_videos_count` | integer | F | |
| `last_applied_video_id` | string | W | |

---

## Лист 6: `VISUAL_DNA` — визуальные правила

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `rule_id` | string | id | |
| `category` | enum | W | color/emotion/lighting/camera/sound |
| `rule_description` | string | W | |
| `example_from_human` | string | W | |
| `strictness` | integer | W | 1–5 |
| `applied_in_videos_count` | integer | F | |
| `last_applied_video_id` | string | W | |

---

## Лист 7: `GOLDEN_SCRIPTS` — эталонные сценарии

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `script_id` | string | id | |
| `title` | string | W | |
| `status` | enum | W | APPROVED_HUMAN |
| `key_style_markers` | string | W | |
| `file_path` | string | W | путь с ID |
| `ai_instruction` | string | W | |
| `avg_sentence_length` | float | W | |
| `short_sentence_pct` | float | W | |
| `rhetorical_questions_freq` | float | W | |
| `transition_style` | string | W | |
| `tone_description` | string | W | |

---

## Лист 8: `KNOWLEDGE_BASE` — источники

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `source_id` | string | id | |
| `title` | string | W | |
| `type` | enum | W | book/article/video |
| `key_concepts` | string | W | |
| `how_to_use_in_script` | string | W | |
| `file_path` | string | W | |
| `used_in_videos_count` | integer | F | |

---

## Лист 9: `CRITIQUE_LOG` — критика и обучение

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `critique_id` | string | id | |
| `video_id` | string | fk | |
| `draft_version` | string | W | |
| `category` | enum | W | robotic_tone/factual_error/pacing_issue/visual_mismatch |
| `specific_feedback` | string | W | |
| `ai_action_for_next_time` | string | W | |
| `resolved` | enum | W | YES/NO |
| `resolved_in_video_id` | string | fk | |

---

## Лист 10: `VISUAL_PATTERN_LIBRARY`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `pattern_id` | string | id | |
| `context_tag` | string | W | loss/gain/shock/reveal/… |
| `visual_solution` | string | W | |
| `character` | string | W | |
| `emotion` | string | W | |
| `color_primary` | string | W | |
| `times_used` | integer | F | |
| `last_used_video_n` | integer | W | |
| `freshness_score` | enum | F | `=IF(times_used>=7,"RETIRE",IF(>=5,"ROTATE",IF(>=3,"HEALTHY","FRESH")))` |
| `best_competitor_example` | string | W | |
| `alternative_count` | integer | W | |
| `status` | enum | W | ACTIVE/RETIRED |
| `avg_retention_impact` | enum | F | HIGH/MED/LOW |
| `audience_category_best_fit` | enum | W | C1/C2/C3 |
| `psychological_pattern_id` | string | fk | P01–P12 |
| `notes` | string | W | |

---

## Лист 11: `SCRIPT_PATTERN_LIBRARY`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `pattern_id` | string | id | |
| `context_tag` | string | W | |
| `pattern_description` | string | W | |
| `sentence_avg_length` | float | W | |
| `short_sentence_pct` | float | W | |
| `rhetorical_questions_freq` | float | W | |
| `transition_type` | string | W | |
| `times_used` | integer | F | |
| `last_used_video_n` | integer | W | |
| `freshness_score` | enum | F | формула как в листе 10 |
| `best_competitor_example` | string | W | |
| `alternative_count` | integer | W | |
| `avg_retention_impact` | enum | F | HIGH/MED/LOW |
| `audience_category_best_fit` | enum | W | C1/C2/C3 |
| `psychological_pattern_id` | string | fk | P01–P12 |
| `notes` | string | W | |

---

## Лист 12: `AUDIO_PATTERN_LIBRARY`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `pattern_id` | string | id | |
| `context_tag` | string | W | |
| `pattern_description` | string | W | |
| `sound_cue_type` | string | W | |
| `music_mood` | string | W | |
| `voice_tone` | string | W | |
| `pause_duration_sec` | float | W | |
| `tempo_bpm` | integer | W | |
| `times_used` | integer | F | |
| `last_used_video_n` | integer | W | |
| `freshness_score` | enum | F | формула как в листе 10 |
| `best_competitor_example` | string | W | |
| `alternative_count` | integer | W | |
| `avg_retention_impact` | enum | F | HIGH/MED/LOW |
| `audience_category_best_fit` | enum | W | C1/C2/C3 |
| `psychological_pattern_id` | string | fk | P01–P12 |
| `notes` | string | W | |

---

## Лист 13: `RETENTION_PATTERN_LIBRARY` — комбинации

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `pattern_id` | string | id | |
| `context_tag` | string | W | |
| `combination_description` | string | W | |
| `visual_pattern_id` | string | fk | → лист 10 |
| `script_pattern_id` | string | fk | → лист 11 |
| `audio_pattern_id` | string | fk | → лист 12 |
| `times_used` | integer | F | |
| `last_used_video_n` | integer | W | |
| `freshness_score` | enum | F | формула как в листе 10 |
| `avg_retention_impact` | string | F | HIGH/MED/LOW + % |
| `audience_category_best_fit` | enum | W | C1/C2/C3 |
| `psychological_patterns_triggered` | string[] | W | список P01–P12 |
| `confidence` | enum | W | HIGH/MED/LOW |
| `notes` | string | W | |

---

## Лист 14: `PATTERN_EFFECTIVENESS_MATRIX` — по ЦА

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `psychological_pattern_id` | string | id | P01–P12 |
| `pattern_name` | string | W | |
| `C1_effectiveness` | string | F | HIGH/MED/LOW + % |
| `C2_effectiveness` | string | F | HIGH/MED/LOW + % |
| `C3_effectiveness` | string | F | HIGH/MED/LOW + % |
| `overall_effectiveness` | enum | F | HIGH/MED/LOW |
| `best_context_tag` | string | F | |
| `notes` | string | W | |

---

## Лист 15: `ANALYTICS` — дашборд (весь Read-Only)

> Дашборд из секций, всё `F` (формулы поверх остальных листов). Записи нет.

Секции: **Тренды по 10 видео** (`video_n, video_id, publish_date, retention_pct,
uniqueness, tier`) · **Средние по актам** (`act, avg_duration_pct, min, max,
recommended`) · **Топ техники** (`technique_type, times_used, avg_effectiveness,
top_video`) · **Здоровье ротации ассетов** (`asset_type, pool_size,
assets_needing_rotation, rotation_health`) · **Здоровье паттернов** (`pattern_type,
total_patterns, fresh_count, rotate_count, retire_count, rotation_health`) ·
**Флаги** (`last_3_uniqueness_avg, trend, alert`).

---

## РЕШЕНИЯ ПО ЭТОЙ КНИГЕ (что подтвердить)

1. **HP мастеров вписан столбцами в `ASSET_USAGE_HISTORY`** (а не отдельным листом),
   т.к. этот лист уже реестр мастеров с ключом `global_asset_id` — отдельный лист
   дублировал бы строки. Добавлены `description` + `system_status`. Костыльный
   `master_id` убран (у мастера его id и есть `global_asset_id`). Если хочешь
   отдельный лист `ASSET_HP` — скажи, вынесу.

   **Канонический термин (зафиксирован для всей системы):** ключ мастера —
   `global_asset_id`. На уровне видео ассет адресуется парой `global_asset_id` +
   `variation_id` (опц.). Связь уровней — по `global_asset_id` как join-ключу.
2. **Статус видео** в `VIDEOS_INDEX.status` стал enum с палитрой (был просто `status`).
3. **Производственного статуса и HP вариаций тут НЕТ** — они на уровне видео (верно по
   нашей модели: мастер→канал, вариация→видео, производственный статус→видео).
4. **`SCENES` тут НЕТ** — это лист уровня видео.
5. Флаги `W/F`: явные формулы (`rotation_status`, `freshness_score`) помечены `F` точно;
   агрегаты/своды помечены `F` по смыслу — **подтверди на интроспекции реального файла**,
   если какой-то столбец на деле пишется, а не считается.
