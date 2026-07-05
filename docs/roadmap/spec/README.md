# Спеки проекта — канон намерения

> Рабочие спек-файлы и инструкции владельца, импортированы в репо (Сессия 5) и теперь **версионируются**.
> Раньше лежали россыпью в `/home/admin/projects/` вне git (F22). Это **канон намерения** —
> как система задумана. Код — канон поведения (местами ушёл дальше/иначе). Синхронизируем навстречу,
> не ломая работающее (`../05_data_template_media_system.md`).
>
> **Оригиналы** остались в `/home/admin/projects/`. Отныне канон — эта папка; правки вносим здесь.
> Предложения по улучшению функций — в [`IMPROVEMENTS.md`](IMPROVEMENTS.md).

## Инструкции (`instructions/`) — как устроено и как делать

| Файл | Что задаёт | Код-зона |
|---|---|---|
| `ИНСТРУКЦИЯ_структура_и_ядро.md` | «карта и закон»: структура проекта + ядро сервера | `core/*`, `server.py` |
| `ИНСТРУКЦИЯ_инструменты.md` | спека интерфейса инструментов (row-by-id, RMW, статусы) | `tools/*`, `core/tables`, `core/excel` |
| `ИНСТРУКЦИЯ_шаблоны.md` | шаблоны рабочего пространства + таблиц; трёхфазное создание; интроспектор | `config/templates/*`, `core/engine`, `structure_*` |
| `ИНСТРУКЦИЯ_media_инструменты.md` | категория `media`: TTS/STT/картинки | `core/providers/*`, `tools/media` (нет) |
| `ИНСТРУКЦИЯ_видеомонтаж.md` | сборка готовой сцены из исходников | `tools/video` (нет), ffmpeg |
| `media_tools_deployment.md` | план развёртывания media (10 инструментов, порядок, контракты) | `core/providers/*`, `tools/media` |

## Схемы данных (`schemas/`) — колонки Excel-книг (источник для `config/templates/tables/`)

| Файл | Книга | Уровень |
|---|---|---|
| `channel_config.schema.md` | `config/channel_config.yaml` (7 секций) | конфиг канала ✅ реализован |
| `channel_data.schema.md` | `channel_data.xlsx` (~15 листов) | наш канал |
| `video_data.schema.md` | `video_data.xlsx` (~22 листа, SCENES/RENDERS 🆕) | наше видео |
| `network_config.schema.md` | `network_config.xlsx` | сетка |
| `competitor_channel_data.schema.md` | `competitor_channel_data.xlsx` (~10) | канал конкурента |
| `competitor_video_data.schema.md` | `competitor_video_data.xlsx` (~19) | видео конкурента |
| `niche_network_data.schema.md` | `niche_network_data.xlsx` (~12) | ниша/конкуренты |

## Прочее

| Файл | Что |
|---|---|
| `Бриф_табличные_инструменты.md` | бриф на слой табличных инструментов (Table Data Layer) |
| `project_memory.spec.md` | формат `project_memory.md` (данные в `workspace/`, память проекта) |

## Как пользоваться

1. При работе над воркстримом — читать соответствующую спеку **до** кода (иерархия чтения из `../README.md`).
2. Расхождение спека↔код → находка `F#` в `../02_findings.md`.
3. Идея как улучшить существующую функцию → предложение `IMP#` в [`IMPROVEMENTS.md`](IMPROVEMENTS.md).
4. Спека устарела относительно кода (код сделал лучше) → обновить спеку здесь, отметить в истории.
