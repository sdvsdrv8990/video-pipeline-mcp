# СХЕМА КНИГИ: `competitor_video_data.xlsx` (видео конкурента)

> Уровень: **видео конкурента** (19 листов — поправка: в аудите было 14).
> Книга анализа «что украсть». Структура зеркалит наше видео, но: НЕТ
> производственных листов (ASSETS_USED, SCENES, UNIQUENESS, SCRIPT_DNA, POST_MORTEM)
> и ЕСТЬ steal-столбцы + INSIGHTS. Наших HP/статусов нет.
> Легенда: `W` · `F` · `id` · `fk` · `enum`.

---

## Лист 1: `META`

`video_id (id), title, url, channel_id (fk), channel_name, publish_date, duration_sec,
niche, views, likes, comments, estimated_subs_at_publish` — `W`;
`type` = `F` (фикс. `"competitor"`).

---

## Лист 2: `PERFORMANCE`

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `views` `likes` `comments` | integer | W | |
| `like_rate` | float | F | `=likes/views*100` |
| `comment_rate` | float | F | `=comments/views*100` |
| `engagement_rate` | float | F | `=(likes+comments)/views*100` |
| `estimated_retention` | float | F | `=MIN(85,20+engagement_rate*8)` (оценка, нет реальных данных) |
| `retention_tier` | enum | F | HIGH/MED/LOW |
| `performance_score` | float | F | `=like_rate*0.4+comment_rate*0.3+(est_retention/100)*0.3` |

---

## Лист 3: `TRANSCRIPT`

`scene_id (id), timestamp_start, timestamp_end, duration_sec, act_number, text,
word_count, pace_wpm (F: word_count/(duration/60)), energy_level (1–5), notes` —
остальное `W`/`F` как у нашего видео.

---

## Листы 4–10: `ACT_1_HOOK … ACT_7_TRUTH` (7 идентичных)

Как у нашего видео + **steal-столбцы**: `act_name, timestamp_start/end, duration_sec,
duration_pct (F), recommended_duration_sec, status (F), text, technique_1/2/3,
visual_technique, audio_technique, effectiveness_score (1–5), `**`steal_this (YES/NO),
steal_priority (HIGH/MED/LOW/SKIP), steal_note`**`, notes`. Кроме формул — `W`.

---

## Лист 11: `TECHNIQUES`

`technique_id (id), timestamp, act_number, technique_type, description, effectiveness
(1–5), steal_priority (HIGH/MED/LOW/SKIP), already_in_our_dna (YES/NO),
psychological_pattern_id (fk P01–P12), audience_category (C1/C2/C3), notes` — `W`.

---

## Лист 12: `PATTERNS`

`pattern_id (id), pattern_type (hook_formula/pacing/visual_style/audio_style/
comment_bait/thumbnail_angle/title_formula/other), description, timestamp_example,
frequency_in_channel, avg_effectiveness, steal_priority, psychological_pattern_id (fk),
audience_category, notes` — `W`.

---

## Лист 13: `SCENE_SEMANTICS` (сенсорный слой конкурента)

Как у нашего видео + steal-столбцы. `scene_id (id), timestamp, script_phrase, character,
emotion, color_primary/secondary, lighting, contrast_level, camera_move, sound_cue,
music_mood, voice_tone, gesture, eye_direction, visual_metaphor, text_on_screen_style,
retention_impact, technique_linked (fk), psychological_pattern_id (fk), audience_category,
`**`estimated_effectiveness (HIGH/MED/LOW), steal_priority`**`, notes` — `W`.

---

## Листы 14–16: `VISUAL_/SCRIPT_/AUDIO_PATTERNS` (шаблон + steal)

> Три реестра паттернов конкурента. Общие + дельты + steal-блок.

**Общие:** `pattern_id (id), scene_id (fk), context_tag, estimated_effectiveness
(HIGH/MED/LOW), steal_priority (HIGH/MED/LOW/SKIP), adapted_by_us (YES/NO), our_pattern_id
(fk), psychological_pattern_id (fk), audience_category, notes` — `W`.

**Дельты:**
- `VISUAL_PATTERNS` (14): + `visual_solution, character, emotion, color_primary, action`.
- `SCRIPT_PATTERNS` (15): + `pattern_description, sentence_avg_length, short_sentence_pct,
  rhetorical_questions_count, transition_type`.
- `AUDIO_PATTERNS` (16): + `pattern_description, sound_cue_type, music_mood, voice_tone,
  pause_duration_sec, tempo_bpm`.

---

## Лист 17: `RETENTION_PATTERNS` (комбинации)

`pattern_id (id), context_tag, combination_description, visual_pattern_id (fk),
script_pattern_id (fk), audio_pattern_id (fk), estimated_retention_impact (HIGH/MED/LOW),
timestamp_example, scene_id (fk), audience_category, psychological_patterns_triggered
(список), steal_priority, adapted_by_us (YES/NO), notes` — `W`.

---

## Лист 18: `INSIGHTS`

`insight_id (id), category (hook/structure/pacing/visual/audio/engagement/other),
observation, action_for_us, priority (HIGH/MED/LOW), applied (YES/NO),
applied_in_video_id (fk)` — `W`.

---

## Лист 19: `ANALYTICS` — дашборд видео конкурента (Read-Only)

> Весь `F`. Секции: **Структура актов** · **Производительность vs бенчмарк ниши**
> (delta, assessment) · **Топ техники** (steal_priority) · **Приоритеты для нашего канала**.

---

## РЕШЕНИЯ
1. 19 листов (аудит говорил 14 — поправка).
2. Главное отличие от нашего видео: **steal-столбцы** (`steal_this`, `steal_priority`,
   `adapted_by_us`, `our_pattern_id`, `already_in_our_dna`) + `INSIGHTS`. Нет
   производственных листов — мы конкурента не производим, а разбираем.
3. `estimated_retention` — оценка (формула), не реальные данные YouTube (их у нас нет).
