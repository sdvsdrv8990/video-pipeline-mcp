# СХЕМА КНИГИ: `niche_network_data.xlsx` (сетка конкурентов в нише)

> Уровень: **ниша** (агрегат по всем конкурентам ниши). Аналитика для gap-анализа.
> Легенда: `W` writable · `F` computed · `id` ключ · `fk` ссылка · `enum` набор.
> Наших HP/сцен/статусов тут НЕТ — это книга анализа конкурентов. Принцип
> «нет данных = не ошибка»: нет конкурентов → не сбой, а `MISSING_OPTIONAL_DATA`.

---

## Лист 1: `COMPETITORS_INDEX`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `channel_id` | string | id | |
| `channel_name` | string | W | |
| `subs_approx` | integer | W | |
| `videos_analyzed` | integer | W | |
| `avg_performance_score` | float | F | |
| `primary_hook_type` `primary_visual_style` `primary_audio_style` | string | W | |
| `threat_level` | enum | W | HIGH/MED/LOW |
| `last_analyzed_date` | date | W | |
| `top_video_id` | fk | W | |
| `top_video_views` | integer | W | |
| `our_adoption_count` | integer | F | сколько их инсайтов мы применили |

---

## Лист 2: `NICHE_PATTERNS`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `pattern_id` | string | id | |
| `pattern_type` | enum | W | visual/script/audio/retention |
| `found_in_channels` | string[] | W | список channel_id |
| `channels_count` | integer | F | |
| `frequency_pct` | float | F | `=channels_count/total_channels*100` |
| `avg_effectiveness` | float | W | |
| `best_example_video_id` | fk | W | |
| `our_adoption_status` | enum | W | NOT_ADOPTED/IN_DNA/TESTING/TESTED/REJECTED |
| `psychological_pattern_id` | fk | W | P01–P12 |
| `audience_category` | enum | W | C1/C2/C3 |
| `notes` | string | W | |

---

## Лист 3: `TOP_TOPICS`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `rank` | integer | W | |
| `topic` | string | W | |
| `channel_id` `video_id` | fk | W | |
| `views` | integer | W | |
| `performance_score` | float | W | |
| `hook_angle_used` | string | W | |
| `approach_a_feasibility` `approach_b_signal` | integer | W | 1–5 |
| `our_coverage_status` | enum | W | NOT_COVERED/PLANNED/IN_PRODUCTION/DONE |
| `our_video_id` | fk | W | |
| `notes` | string | W | |

---

## Лист 4: `AUDIENCE_PROFILE` (2 секции)

**Секция A — 3 категории зрителей:** `category_id (id), category_name, description,
evidence_source (список video_id), size_estimate (LARGE/MED/SMALL), primary_pain_point,
primary_desire, content_format_preference` — все `W`.

**Секция B — 12 психопаттернов (P01–P12):** `pattern_id (id), pattern_name, pattern_type
(cognitive_bias/behavioral/psychographic), description, evidence_from_top_videos,
strength_in_niche (1–5), content_application, already_in_our_dna (YES/NO)` — все `W`.
Фиксированный справочник: P01 Loss Aversion … P12 Dunning-Kruger Signal.

---

## Лист 5: `CONTENT_PREDICTION` — предсказания (gap → план)

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `prediction_id` | string | id | |
| `approach` | enum | W | A/B |
| `prediction_origin` | enum | W | competitor_clone/audience_gap/trend_collision/cross_niche_transfer/emerging_pattern |
| `approach_name` `approach_goal` `why_this_is_white_spot` | string | W | |
| `ai_execution_steps` `topic` `hook_angle` | string | W | |
| `target_category_id` | fk | W | из AUDIENCE_PROFILE |
| `patterns_triggered` | string[] | W | P01–P12 |
| `source_channels` `source_video_ids` | string[] | W | |
| `evidence` | string | W | |
| `predicted_performance` | integer | W | 1–5 |
| `confidence` `risk_level` | enum | W | |
| `estimated_production_difficulty` `estimated_uniqueness` | integer | W | 1–5 |
| `our_advantage_angle` `recommended_format` | string | W | |
| `status` | enum | W | IDEA/PLANNED/IN_PRODUCTION/TESTING/DONE/REJECTED |
| `assigned_to_agent` `result_video_id` | fk | W | |
| `actual_performance_score` | float | W | после DONE |
| `prediction_accuracy` | enum | F | ACCURATE/CLOSE/MISS/PENDING (формула по status) |
| `postmortem_notes` `next_iteration_action` | string | W | |

---

## Листы 6: `TOP_VIDEOS_NICHE`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `rank` | integer | W | |
| `video_id` `channel_id` | fk | W | |
| `title` | string | W | |
| `views` `performance_score` | num | W | |
| `hook_type` `key_technique` `why_it_dominated` | string | W | |
| `steal_priority` | enum | W | HIGH/MED/LOW |
| `applied_by_us` | enum | W | YES/NO |
| `our_video_id` | fk | W | |

---

## Листы 7–10: `NICHE_VISUAL_/SCRIPT_/AUDIO_/RETENTION_PATTERNS` (шаблон)

> Четыре листа нишевых паттернов близкой структуры. Общие столбцы + дельты.

**Общие:** `pattern_id (id), context_tag, found_in_channels (список), channels_count (F),
frequency_pct (F), avg_effectiveness, best_example_video_id (fk), our_adoption_status
(enum), our_pattern_id (fk), psychological_pattern_id (fk P01–P12), audience_category
(enum), notes` — кроме помеченных `F`, всё `W`.

**Дельты:**
- `NICHE_VISUAL_PATTERNS` (7): + `visual_solution`.
- `NICHE_SCRIPT_PATTERNS` (8): + `pattern_description`.
- `NICHE_AUDIO_PATTERNS` (9): + `pattern_description`.
- `NICHE_RETENTION_PATTERNS` (10): + `combination_description, visual_pattern_id,
  script_pattern_id, audio_pattern_id, avg_retention_impact, psychological_patterns_triggered (список)`.

---

## Лист 11: `PATTERN_EFFECTIVENESS_MATRIX` — матрица по ЦА (ниша)

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `psychological_pattern_id` | string | id | P01–P12 |
| `pattern_name` | string | W | |
| `C1_effectiveness` `C2_effectiveness` `C3_effectiveness` | string | F | HIGH/MED/LOW + % |
| `overall_effectiveness` | enum | F | HIGH/MED/LOW |
| `best_context_tag` `dominant_pattern_type` | string | F | |
| `notes` | string | W | |

---

## Лист 12: `ANALYTICS` — дашборд ниши (Read-Only)

> Весь `F`. Секции: **Рейтинг конкурентов** · **Доминирующие паттерны** (`gap=IF(NOT_ADOPTED,"GAP","COVERED")`) ·
> **Наша позиция** (percentile по нише) · **Белые пятна** (opportunity_score, prediction_id) ·
> **Точность предсказаний** (`accuracy_rate=accurate/total*100`).

---

## РЕШЕНИЯ
1. Книга анализа — без наших HP/сцен/статусов. Наша связь — через `our_*` столбцы.
2. `CONTENT_PREDICTION` — мост gap-анализа к нашему производству (`prediction_id` →
   `video_data.META.prediction_id`). Замкнутая петля точности (predicted vs actual).
3. Нет конкурентов/паттернов → `MISSING_OPTIONAL_DATA` (warning), не блок.
