# СХЕМА КНИГИ: `competitor_channel_data.xlsx` (канал конкурента)

> Уровень: **канал конкурента** (родитель — наш канал, см. вложенность шаблонов).
> Книга анализа: «что у них работает и что красть». Наших HP/сцен/статусов нет.
> Легенда: `W` · `F` · `id` · `fk` · `enum`.

---

## Лист 1: `CHANNEL_META`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `channel_id` | string | id | |
| `channel_name` `url` `niche` | string | W | |
| `subs_approx` `avg_views_per_video` | integer | W | |
| `upload_frequency_per_month` | float | W | |
| `content_style` `primary_hook_type` `primary_visual_style` `primary_audio_style` | string | W | |
| `videos_analyzed` | integer | W | |
| `overall_threat_level` | enum | W | HIGH/MED/LOW |
| `last_analyzed_date` | date | W | |
| `notes` | string | W | |

---

## Лист 2: `VIDEOS_INDEX`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `video_n` | integer | W | |
| `video_id` | fk | W | |
| `title` | string | W | |
| `publish_date` | date | W | |
| `duration_sec` `views` `likes` `comments` | integer | W | |
| `estimated_retention` `performance_score` | float | W | |
| `tier` | enum | W | HIGH/MED/LOW |
| `top_technique` `hook_type` | string | W | |
| `steal_count` | integer | F | кол-во YES в steal_this по видео |
| `notes` | string | W | |

---

## Лист 3: `CHANNEL_PATTERNS`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `pattern_type` | enum | W | visual/script/audio/retention |
| `pattern_id` | string | id | |
| `description` | string | W | |
| `frequency_count` | integer | W | |
| `frequency_pct` | float | F | `=frequency_count/videos_analyzed*100` |
| `best_example_video_id` | fk | W | |
| `avg_effectiveness` | float | W | |
| `our_adoption_status` | enum | W | NOT_ADOPTED/IN_DNA/TESTING/TESTED/REJECTED |
| `our_pattern_id` | fk | W | |
| `psychological_pattern_id` | fk | W | P01–P12 |
| `audience_category` | enum | W | C1/C2/C3 |
| `notes` | string | W | |

---

## Лист 4: `TOP_VIDEOS`

`rank, video_id (fk), title, views, performance_score, hook_type, why_it_worked,
key_technique, steal_priority (HIGH/MED/LOW), applied_in_our_video_id (fk), notes` — всё `W`.

---

## Листы 5–8: `VISUAL_/SCRIPT_/AUDIO_/RETENTION_SOLUTIONS_LIBRARY` (шаблон)

> Четыре библиотеки решений конкурента близкой структуры. Общие столбцы + дельты.

**Общие:** `solution_id (id), context_tag, estimated_effectiveness (HIGH/MED/LOW),
steal_priority (HIGH/MED/LOW/SKIP), adapted_by_us (YES/NO), our_pattern_id (fk),
psychological_pattern_id (fk P01–P12), audience_category (enum), notes` — всё `W`.

**Дельты:**
- `VISUAL_SOLUTIONS_LIBRARY` (5): + `visual_solution, description, character, emotion, color_primary`.
- `SCRIPT_SOLUTIONS_LIBRARY` (6): + `pattern_description, sentence_avg_length,
  short_sentence_pct, rhetorical_questions_freq, transition_type`.
- `AUDIO_SOLUTIONS_LIBRARY` (7): + `pattern_description, sound_cue_type, music_mood,
  voice_tone, pause_duration_sec, tempo_bpm`.
- `RETENTION_SOLUTIONS_LIBRARY` (8): + `combination_description, visual_solution_id,
  script_solution_id, audio_solution_id, estimated_retention_impact,
  psychological_patterns_triggered (список)`.

---

## Лист 9: `INSIGHTS`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `insight_id` | string | id | |
| `category` | enum | W | hook/structure/pacing/visual/audio/engagement/other |
| `observation` `action_for_us` | string | W | |
| `priority` | enum | W | HIGH/MED/LOW |
| `source_video_id` | fk | W | |
| `applied` | enum | W | YES/NO |
| `applied_in_our_video_id` | fk | W | |
| `result` | string | W | факт результата применения |

---

## Лист 10: `ANALYTICS` — дашборд канала конкурента (Read-Only)

> Весь `F`. Секции: **Тренды производительности** · **Частота техник**
> (`pct_of_videos=times_used/videos_analyzed*100`, trend, our_status) · **Средняя
> структура актов** (vs наш канал, delta) · **Сравнение с нашим каналом**
> (`metric, this_competitor, us, delta, who_wins`).

---

## РЕШЕНИЯ
1. Без наших надстроек — книга анализа. Связь с нами через `our_*` / `applied_in_our_*`.
2. `INSIGHTS.result` + `applied_in_our_video_id` — замкнутая петля «инсайт → применили →
   результат по фактам». Развёрнуто — в `project_memory.md` (по ID, не дублируя).
