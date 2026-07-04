# Q&A: channel_data.xlsx — книга канала

> **Роль:** книга уровня канала. Агрегация по всем видео. 15 листов.
> **Сквозное:** [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные).
> **Статус:** схема описана.
> **Навигация:** `workspace/.../channels/<channel>/channel_data.xlsx`.

## Листы (15)

### VIDEOS_INDEX — сводка видео
`video_id` (id), `title` (W), `publish_date`, `status` (enum), `duration_sec`, `views`/`likes`, `retention_pct`, `overall_uniqueness` (F), `tier` (enum F), scores (F), `target_audience_category`, `performance_score` (F).

### CHANNEL_STATS — агрегаты канала
`total_videos` (F), `avg_retention` (F), `avg_uniqueness` (F), scores (F), `videos_high/med/low` (F), `last_3_avg_uniqueness` (F), `trend` (enum F: UP/FLAT/DOWN).

### ASSET_USAGE_HISTORY — реестр мастер-ассетов (+HP)
`global_asset_id` (id), `asset_type`, `description`, `system_status` (enum: active/damaged/moved/deleted), `used_in_videos`, `total_uses` (F), `last_used_video`, `videos_since_last_use` (F), `variation_count` (F), `effective_freshness` (F), `rotation_status` (enum F: OK/ROTATE).

### UNIQUENESS_ALERTS — алерты (MED/LOW)
`video_id` (fk), `tier` (enum F), `weak_dimension` (F), `score` (F), `recommendation`, `signal_status` (enum: active/confirmed/actioned/dismissed_false_positive), `flag_source` (enum: human_decision/ai_check), `comment`, `outcome_ref`.

### STYLE_DNA — правила подачи
`rule_id` (id), `category` (enum), `rule_description`, `example_from_human`, `strictness` (1–5), `applied_in_videos_count` (F), `last_applied_video_id`.

### VISUAL_DNA — визуальные правила
Аналогична STYLE_DNA.

### GOLDEN_SCRIPTS — эталонные сценарии
`script_id` (id), `title`, `status` (enum: APPROVED_HUMAN), `key_style_markers`, `file_path`, `ai_instruction`, метрики стиля (W).

### KNOWLEDGE_BASE — источники
`source_id` (id), `title`, `type` (enum: book/article/video), `key_concepts`, `how_to_use_in_script`, `file_path`, `used_in_videos_count` (F).

### CRITIQUE_LOG — критика и обучение
`critique_id` (id), `video_id` (fk), `draft_version`, `category` (enum), `specific_feedback`, `ai_action_for_next_time`, `resolved` (enum), `resolved_in_video_id` (fk).

### PATTERN_LIBRARY (4 листа)
VISUAL/SCRIPT/AUDIO/RETENTION_PATTERN_LIBRARY — библиотеки паттернов с `freshness_score`, `status`, `avg_retention_impact`.

### PATTERN_EFFECTIVENESS_MATRIX — матрица по ЦА
`psychological_pattern_id` (id P01–P12), `pattern_name`, `C1/C2/C3_effectiveness` (F), `overall_effectiveness` (enum F).

### ANALYTICS — дашборд (Read-Only)
Весь F. Секции: тренды, средние по актам, топ техники, здоровье ротации/паттернов.
