# Q&A: competitor_channel_data.xlsx — канал конкурента

> **Роль:** книга анализа «что у них работает и что красть». Нет HP/сцен/статусов. 10 листов.
> **Сквозное:** [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные).
> **Навигация:** `workspace/.../competitors/<наш_канал>/<конкурент>/competitor_channel_data.xlsx`.

## Листы (10)

### CHANNEL_META
`channel_id` (id), `channel_name`/`url`/`niche`, `subs_approx`/`avg_views_per_video`/`upload_frequency_per_month`, `content_style`/`primary_hook_type`/`primary_visual_style`/`primary_audio_style`, `videos_analyzed`, `overall_threat_level` (enum: HIGH/MED/LOW), `last_analyzed_date`, `notes`.

### VIDEOS_INDEX
`video_n`, `video_id` (fk), `title`, `publish_date`, `duration_sec`/`views`/`likes`/`comments`, `estimated_retention`/`performance_score`, `tier` (enum), `top_technique`/`hook_type`, `steal_count` (F), `notes`.

### CHANNEL_PATTERNS
`pattern_type` (enum: visual/script/audio/retention), `pattern_id` (id), `description`, `frequency_count`, `frequency_pct` (F), `best_example_video_id` (fk), `avg_effectiveness`, `our_adoption_status` (enum), `our_pattern_id` (fk), `psychological_pattern_id` (fk), `audience_category` (enum), `notes`.

### TOP_VIDEOS
`rank`, `video_id` (fk), `title`, `views`, `performance_score`, `hook_type`, `why_it_worked`, `key_technique`, `steal_priority` (enum), `applied_in_our_video_id` (fk), `notes` — всё W.

### SOLUTIONS_LIBRARY (4 листа)
VISUAL/SCRIPT/AUDIO/RETENTION_SOLUTIONS_LIBRARY — библиотеки решений конкурента. Общие: `solution_id` (id), `context_tag`, `estimated_effectiveness`, `steal_priority`, `adapted_by_us`, `our_pattern_id` (fk), `psychological_pattern_id` (fk), `audience_category` (enum), `notes`.

### INSIGHTS
`insight_id` (id), `category` (enum: hook/structure/pacing/visual/audio/engagement/other), `observation`/`action_for_us`, `priority` (enum), `source_video_id` (fk), `applied` (enum: YES/NO), `applied_in_our_video_id` (fk), `result`.

### ANALYTICS — дашборд (Read-Only)
Весь F. Секции: тренды производительности, частота техник, средняя структура актов, сравнение с нашим каналом.
