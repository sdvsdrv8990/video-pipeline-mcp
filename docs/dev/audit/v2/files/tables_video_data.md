# Q&A: video_data.xlsx — книга видео

> **Роль:** центральная книга уровня видео. Source of Truth конкретного видео. 23 листа.
> **Сквозное:** [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные) (Excel = SOT).
> **Статус:** схема описана, интроспектор генерирует schema.yaml.
> **Навигация:** `workspace/.../videos/<video>/video_data.xlsx` → `json_read_snapshot` → Claude.

## Листы (23)

### META — метаданные видео
`video_id` (id), `title` (W), `url` (W), `channel_id` (fk, RO), `publish_date`, `duration_sec`, `niche` (fk, RO), `type` = F "own", `status` (enum), `target_audience_category` (enum), `prediction_id` (fk).
**enum status:** IDEA · DRAFT · IN_PRODUCTION · RENDERING · PUBLISHED · ARCHIVED

### PERFORMANCE — метрики
`views` `likes` `comments` `shares` (W), `ctr_percent`, `avg_watch_sec`. Формулы: `retention_percent`, `like_rate`, `comment_rate`, `engagement_rate`, `performance_score` (=like_rate×0.4+comment_rate×0.3+retention/100×0.3), `prediction_accuracy` (enum F: ACCURATE/CLOSE/MISS/PENDING).

### TRANSCRIPT — пословная транскрипция
`scene_id` (id), `timestamp_start/end` (F), `duration_sec` (F), `act_number` (W), `text` (F), `word_count` (F), `pace_wpm` (F), `energy_level` (W), `notes` (W).

### ACT_1_HOOK … ACT_7_TRUTH (7 листов)
Идентичны: `act_name` (F), `timestamp_start/end` (F), `duration_sec/pct` (F), `recommended_duration_sec` (fk), `status` (enum F: LONG/SHORT/OK), `text` (W), `technique_1/2/3` (W), `visual/audio_technique` (W), `effectiveness_score` (W), `retention_impact` (enum W), `notes` (W).

### TECHNIQUES — реестр приёмов
`technique_id` (id), `timestamp`, `act_number`, `technique_type`, `description`, `effectiveness` (1–5), `psychological_pattern_id` (fk), `audience_category`, `notes`.

### ASSETS_USED — ассеты видео
`global_asset_id` (id/fk), `variation_id` (id), `asset_type` (enum W), `is_master`, `description`, `system_status` (enum: active/damaged/moved/deleted), `production_status` (enum: under_review/accepted/rejected), `scene_id(s)`, `usage_count_in_video`, `last_used_video_n`, `videos_since_last_use` (F), `uniqueness_score` (F).

### UNIQUENESS — итог уникальности
Секции: script/svg/music/sound/transition scores (F), `overall_uniqueness` (F), `tier` (enum F: HIGH/MED/LOW), `scenes_uniqueness_ref` (F).

### SCRIPT_DNA_APPLIED — правила стиля
`rule_id` (fk), `category` (enum F), `applied_in_act` (W), `how_it_was_applied` (W), `human_feedback` (W), `resolved` (enum: YES/NO).

### SCENES — сцены (точка сборки)
`scene_id` (id), `act` (fk), `timestamp_start/end` (F), `text_snippet` (F), `bg_assets`/`character_assets`/`component_assets` (W), `music_assets`/`sound_assets`/`filter_assets`/`transition_assets` (W), `scene_uniqueness` (F), `animation_order` (W), `animation_speed` (enum), `retention_at_this_sec` (F), `retention_impact` (enum F).

### SCENE_SEMANTICS — сенсорный слой
`scene_id` (id), `timestamp` (F), `script_phrase` (F), `character`, `emotion` (enum), `color_primary/secondary`, `lighting` (enum), `contrast_level` (enum), `camera_move` (enum), `sound_cue`, `music_mood`, `voice_tone` (enum), `gesture`/`eye_direction`, `visual_metaphor`, `text_on_screen_style`, `retention_impact` (enum F), `technique_linked` (fk), `psychological_pattern_id` (fk), `audience_category`.

### VISUAL_/SCRIPT_/AUDIO_PATTERNS_USED (3 листа)
`pattern_id` (fk), `scene_id` (fk), `context_tag`, `uniqueness_score` (F), `retention_impact` (enum F), `notes`.

### RETENTION_PATTERNS — синергии
`pattern_id` (id), `combination_description`, `visual/script/audio_pattern_id` (fk), `retention_impact` (F), `confidence` (enum), `notes`.

### RENDERS — смонтированные видео
`render_id` (id), `render_stage` (enum: draft/final), `render_profile` (fk), `format_label`, `file_path`, `file_name` (F), `render_status` (enum), `derived_from_render_id` (fk), `duration_sec` (F), `file_verified` (F).

### POST_MORTEM — журнал ошибок
`issue_type` (enum), `timestamp`, `scene_id` (fk), `act` (fk), `observation`, `instruction_for_next_video`, `applied_in_future_video_id` (fk), `resolved` (enum: YES/NO).

### ANALYTICS — дашборд видео (Read-Only)
Весь F. Секции: производительность, структура актов, уникальность, сцены под угрозой.

## Связь
- `channel_data.VIDEOS_INDEX` ← `video_id` (fk)
- `video_data.ASSETS_USED.global_asset_id` → `channel_data.ASSET_USAGE_HISTORY`
- `video_data.SCENES` → `channel_data.ANALYTICS`
