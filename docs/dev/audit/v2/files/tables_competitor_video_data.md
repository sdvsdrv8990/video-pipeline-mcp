# Q&A: competitor_video_data.xlsx — видео конкурента

> **Роль:** книга анализа «что украсть». Зеркалит наше видео + steal-столбцы. 19 листов.
> **Сквозное:** [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные).
> **Навигация:** `workspace/.../competitors/<канал>/<конкурент>/videos/<видео>/competitor_video_data.xlsx`.

## Отличия от нашего video_data
- `type` = F "competitor" (вместо "own")
- **Нет** производственных листов (SCENES, RENDERS, POST_MORTEM)
- **Есть** steal-столбцы в ACT_*: `steal_this` (YES/NO), `steal_priority` (HIGH/MED/LOW/SKIP), `steal_note`
- **Есть** steal-столбцы в TECHNIQUES: `steal_priority`, `already_in_our_dna` (YES/NO)
- **Есть** steal-столбцы в PATTERNS: `estimated_effectiveness`, `steal_priority`
- **Есть** steal-столбцы в SCENE_SEMANTICS: `estimated_effectiveness`, `steal_priority`

## Листы (19)
META, PERFORMANCE, TRANSCRIPT, ACT_1..7, TECHNIQUES, PATTERNS, SCENE_SEMANTICS, VISUAL_/SCRIPT_/AUDIO_PATTERNS (3), RETENTION_PATTERNS, INSIGHTS, ANALYTICS (Read-Only).
