# Q&A: channel_config.xlsx — конфиг канала

> **Роль:** конфиг-данные уровня канала. Сервер читает при валидации. 7 листов.
> **Сквозное:** [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные).
> **Навигация:** `workspace/.../channels/<channel>/channel_config.xlsx`.

## Листы (7)

### WORKFLOW_SEQUENCES — разрешённые переходы
`sequence_id` (id), `trigger_tool`, `allowed_next_tools` (allow-list), `requires_human_approval` (boolean), `approval_ui_hint`, `fallback_action`.

### PUBLISHING_SCHEDULE — расписание
`schedule_id` (id), `day_of_week`, `time` (HH:MM), `timezone`, `frequency` (enum: WEEKLY/BIWEEKLY), `status` (enum: ACTIVE/PAUSED), `min_uniqueness_required`.

### RESOURCE_LIMITS — лимиты и API
`resource_type` (id: tts_characters/image_generations/svg_tracing), `provider`, `daily_limit`, `current_usage`, `warning_threshold`, `fallback_provider` (стыковка с model_routing.yaml).

### METADATA_DEFAULTS — шаблоны метаданных
`metadata_type` (id), `template_string`, `variables_allowed`, `example_output`.

### AUTOMATION_RULES — когда ИИ сам, когда зовёт человека
`rule_id` (id), `condition`, `action`, `severity` (enum: CRITICAL/HIGH/MED).

### SCENE_PROFILE — профиль типов фрагментов
`fragment_type` (id: svg_bg/svg_character/svg_component/music/sound/filter/transition), `enabled` (boolean), `niche_weight`, `signal_on_reuse` (boolean), `reuse_threshold`.
**Принцип «тихий столбец»:** `enabled=false` гасит поведение, не структуру.

### RENDER_CONFIG — параметры рендера
`param` (id: codec/resolution/aspect_ratio/fps/crf/bitrate/container), `value`, `notes`.
