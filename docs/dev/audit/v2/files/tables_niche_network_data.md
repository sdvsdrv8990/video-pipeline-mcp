# Q&A: niche_network_data.xlsx — сетка конкурентов в нише

> **Роль:** агрегат по всем конкурентам ниши. Аналитика для gap-анализа. 12 листов.
> **Сквозное:** [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные).
> **Навигация:** `workspace/.../niches/<niche>/niche_network_data.xlsx`.

## Листы (12)

### COMPETITORS_INDEX
`channel_id` (id), `channel_name`, `subs_approx`, `videos_analyzed`, `avg_performance_score` (F), `primary_hook_type`/`primary_visual_style`/`primary_audio_style`, `threat_level` (enum), `last_analyzed_date`, `top_video_id` (fk), `top_video_views`, `our_adoption_count` (F).

### NICHE_PATTERNS
`pattern_id` (id), `pattern_type` (enum), `found_in_channels`, `channels_count` (F), `frequency_pct` (F), `avg_effectiveness`, `best_example_video_id` (fk), `our_adoption_status` (enum: NOT_ADOPTED/IN_DNA/TESTING/TESTED/REJECTED), `psychological_pattern_id` (fk), `audience_category` (enum), `notes`.

### TOP_TOPICS
`rank`, `topic`, `channel_id`/`video_id` (fk), `views`, `performance_score`, `hook_angle_used`, `approach_a_feasibility`/`approach_b_signal` (1–5), `our_coverage_status` (enum: NOT_COVERED/PLANNED/IN_PRODUCTION/DONE), `our_video_id` (fk), `notes`.

### AUDIENCE_PROFILE (2 секции)
Секция A — 3 категории зрителей. Секция B — 12 психопаттернов (P01–P12): `pattern_id`, `pattern_name`, `description`, `evidence_from_top_videos`, `strength_in_niche` (1–5), `content_application`, `already_in_our_dna`.

### CONTENT_PREDICTION — предсказания
`prediction_id` (id), `approach` (enum: A/B), `prediction_origin` (enum), `approach_name`/`approach_goal`/`why_this_is_white_spot`, `ai_execution_steps`/`topic`/`hook_angle`, `target_category_id` (fk), `patterns_triggered`, `predicted_performance` (1–5), `confidence`/`risk_level` (enum), `status` (enum), `actual_performance_score`, `prediction_accuracy` (enum F), `postmortem_notes`/`next_iteration_action`.

### TOP_VIDEOS_NICHE
`rank`, `video_id`/`channel_id` (fk), `title`, `views`/`performance_score`, `hook_type`/`key_technique`/`why_it_dominated`, `steal_priority` (enum), `applied_by_us` (enum), `our_video_id` (fk).

### NICHE_*_PATTERNS (4 листа)
NICHE_VISUAL/SCRIPT/AUDIO/RETENTION_PATTERNS. Общие: `pattern_id`, `context_tag`, `found_in_channels`, `channels_count` (F), `frequency_pct` (F), `avg_effectiveness`, `our_adoption_status`, `psychological_pattern_id` (fk), `audience_category` (enum).

### PATTERN_EFFECTIVENESS_MATRIX
`psychological_pattern_id` (id P01–P12), `pattern_name`, `C1/C2/C3_effectiveness` (F), `overall_effectiveness` (enum F), `best_context_tag`/`dominant_pattern_type` (F), `notes`.

### ANALYTICS — дашборд ниши (Read-Only)
Весь F. Секции: рейтинг конкурентов, доминирующие паттерны, наша позиция, белые пятна, точность предсказаний.
