# СХЕМА КНИГИ: `network_config.xlsx` (конфиг сетки)

> Уровень: **сетка**. Координация каналов: антиканнибализация тем, общие пулы API,
> кросс-канальные правила, сетевые точки согласования. Конфиг-данные, сервер читает.
> Легенда: `W`·`F`·`enum`. Почти всё `W` (настройки человека/сети).

---

## Лист 1: `NETWORK_SCHEDULE_MASTER` — сводное расписание сети

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `date` | date | W | |
| `channel_id` | string | fk | |
| `planned_topic` | string | W | |
| `status` | enum | W | PLANNED/IN_PRODUCTION/SCHEDULED |
| `conflict_check` | boolean | F | TRUE = тема пересекается с другим каналом сети |

> `conflict_check` — служит антиканнибализации: сервер сверяет темы каналов на дату.

---

## Лист 2: `SHARED_RESOURCES` — общие пулы

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `resource_name` | string | id | MJ_API_Quota / Master_SVG_Library |
| `total_pool_limit` | integer/str | W | |
| `allocated_channels` | string[] | W | список или ALL |
| `current_network_usage` | integer | W | сервер обновляет |
| `reallocation_rule` | string | W | правило перераспределения |

> `Master_SVG_Library` здесь — общий пул мастеров на сеть. Стыкуется с `global_asset_id`:
> мастер может использоваться/вариироваться любым каналом сети.

---

## Лист 3: `CROSS_CHANNEL_RULES` — правила взаимодействия

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `rule_id` | string | id | |
| `rule_description` | string | W | |
| `enforcement_level` | enum | W | HARD (сервер блокирует) / SOFT (рекомендация ИИ) |

> `HARD` → сервер блокирует операцию (напр. одинаковый hook_angle на двух каналах за 7
> дней). `SOFT` → проходит как рекомендация. Согласуется с принципом «guard = допустимость».

---

## Лист 4: `NETWORK_GATES` — глобальные точки согласования

| Столбец | Тип | Флаг | Прим. |
|---|---|---|---|
| `gate_id` | string | id | |
| `trigger_condition` | string | W | напр. «запуск нового канала» |
| `approver_role` | string | W | Network_Admin |
| `ui_message` | string | W | что показать на согласовании |

> Это конфиг human_gate уровня сети. Сервер читает условие и при срабатывании требует
> подтверждения роли. Стыкуется с `server_reactions.yaml` (human_gate).

---

## РЕШЕНИЯ
1. `network_config` — координация сети как данные (правит человек/Network_Admin).
2. Стыковки: `Master_SVG_Library` ↔ `global_asset_id` (общий пул мастеров);
   `enforcement_level: HARD` ↔ guard-блок; `NETWORK_GATES` ↔ human_gate.
3. На сетке две книги: `network_dashboard` (аналитика, сделана) + `network_config` (правила, эта).
