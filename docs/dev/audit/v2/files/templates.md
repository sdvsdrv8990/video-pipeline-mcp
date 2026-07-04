# Q&A: шаблоны — рабочее пространство и таблицы

> **Роль:** формат шаблонов workspace, трёхфазное создание, шаблоны таблиц.
> **Сквозное:** [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные), [G16](../global.md#g16-незавершённость-должна-кричать-честная-заглушка-а-не-молчать).
> **Статус кода:** шаблоны описаны, движок шаблонов в core/engine/.
> **Навигация:** `config/templates/workspace/*.tpl.yaml` → `config/templates/tables/*.schema.yaml` → `core/engine/`.

## Решение 1: Формат шаблона — required внутри фрагмента
**Q:** как описать структуру workspace?
**A:** каждый фрагмент несёт свой `required` внутри себя:
```yaml
video:
  folders:
    - { name: "assets/svg", required: true }
  files:
    - { name: "video_data.xlsx", kind: table, table_template: video_data, required: true }
    - { name: "read.json", kind: file, required: true }
  children: []
```
**Правила:**
- `required` внутри фрагмента — источник истины об обязательности
- ID присваивает сервер каждому фрагменту при создании
- Композиция по ссылке: родитель знает ИМЯ типа ребёнка
- Пофрагментный скип: нет имени → фрагмент пропускается
**Связь:** SESSIONS.md §Приложение Ж, [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные).

## Решение 2: Трёхфазное создание
**Q:** в каком порядке создаётся структура?
**A:**
```
ФАЗА 1 — ПАПКИ: материализовать folder-фрагменты → каждая получает ID
    ↓ ПРОВЕРКА: facts + fs_get_directory_tree
ФАЗА 2 — ТАБЛИЦЫ: excel_create_workbook → add_sheet → set_validation
    ↓ ПРОВЕРКА: facts + excel_validate_formulas
ФАЗА 3 — ФАЙЛЫ: read.json, write.json, project_memory.md, _INDEX.md
    ↓ ПРОВЕРКА: facts + fs_get_directory_tree
```
**Почему 3 фазы:** таблицы зависят от папок, файлы — от таблиц. Порядок = граф зависимостей.
**Связь:** SESSIONS.md §Приложение Ж.

## Решение 3: Шаблоны рабочего пространства
**Q:** какие иерархии поддерживаются?
**A:**
| Шаблон | Уровень | Файлы | Дети |
|---|---|---|---|
| `niche.tpl` | ниша | _NICHE_INDEX.md, niche_read.json, niche_write.json | network |
| `network.tpl` | сетка | _NETWORK_INDEX.md, network_dashboard.xlsx, network_config.xlsx | channel, competitor_channel |
| `channel.tpl` | канал | channel_data.xlsx, channel_config.xlsx, project_memory.md, scene_layouts/ | video |
| `video.tpl` | видео | video_data.xlsx, read.json, write.json, project_memory.md, assets/, renders/ | — |
| `competitor_channel.tpl` | канал конкурента | channel_meta.json, competitor_channel_data.xlsx | competitor_video |
| `competitor_video.tpl` | видео конкурента | competitor_video_data.xlsx, read.json, write.json | — |
**Связь:** SESSIONS.md §Сессия 4, architecture.md §Две вселенные.

## Решение 4: Шаблоны таблиц через интроспектор
**Q:** как создать схемы таблиц?
**A:** существующие книги (~90 листов) — через `scripts/introspect_tables.py`. Руками спекаем только то, чего в Excel ещё нет: лист `SCENES`, статус-столбцы. Схемы в `config/templates/tables/*.schema.yaml`.
**Связь:** SESSIONS.md §Сессия 4.

## Открытые вопросы файла
- **⬜ F19:** Новые таблицы (SCENES лист, статус-столбцы) — руками.
- **🔶 D12:** introspect_tables.py частично реализован.

## Что улучшить
- Реализовать schemas для новых листов (SCENES, статусы).
- Добавить валидацию required-фрагментов при создании.
