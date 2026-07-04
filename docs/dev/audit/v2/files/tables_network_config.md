# Q&A: network_config.xlsx — конфиг сетки

> **Роль:** координация каналов: антиканнибализация, общие пули, кросс-канальные правила. 4 листа.
> **Сквозное:** [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные).
> **Навигация:** `workspace/.../networks/<network>/network_config.xlsx`.

## Листы (4)

### NETWORK_SCHEDULE_MASTER — сводное расписание
`date`, `channel_id` (fk), `planned_topic`, `status` (enum: PLANNED/IN_PRODUCTION/SCHEDULED), `conflict_check` (F, boolean: тема пересекается с другим каналом).

### SHARED_RESOURCES — общие пулы
`resource_name` (id: MJ_API_Quota / Master_SVG_Library), `total_pool_limit`, `allocated_channels`, `current_network_usage`, `reallocation_rule`.

### CROSS_CHANNEL_RULES — правила взаимодействия
`rule_id` (id), `rule_description`, `enforcement_level` (enum: HARD/SOFT).

### NETWORK_GATES — глобальные точки согласования
`gate_id` (id), `trigger_condition`, `approver_role`, `ui_message`.
