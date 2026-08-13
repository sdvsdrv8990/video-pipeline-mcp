# Формат `config/templates/tables/*.schema.yaml` (воркстрим A1′)

> Мост: `spec/schemas/*.schema.md` (колонки книг, задал владелец) → этот YAML → материализация через
> `core/excel`. Определён S7. Loader (фаза ТАБЛИЦЫ) — код следующей сессии.

## Зачем

`structure_create` для `kind: table` файла сейчас **откладывает** книгу: присваивает `file_id`, кладёт в
`tables_pending` с именем `table_template`, но `.xlsx` не создаёт (`template_engine.py:155-163`, честная
заглушка Ф3/G16). A1′ достраивает фазу: по `table_template` → грузим `config/templates/tables/{name}.schema.yaml`
→ материализуем книгу через `core/excel`.

## Формат

```yaml
book: network_config          # имя (= table_template в workspace-tpl)
level: network                # уровень сущности (инфо)
source: network_config.schema.md   # происхождение (spec/schemas/…)
sheets:
  - name: NETWORK_SCHEDULE_MASTER
    columns:
      - { name: date,           type: date,    flag: W }
      - { name: status,         type: enum,    flag: W, enum: [PLANNED, IN_PRODUCTION, SCHEDULED] }
      - { name: conflict_check, type: boolean, flag: F }        # F без формулы = computed-плейсхолдер
```

**Флаги** (колонка «Флаг» в `spec/schemas`): `id` (ключ) · `W` (writable) · `F` (formula/computed →
`writable:false`; формула в `formula:` если задана спекой, иначе плейсхолдер) · `fk` (внешний ключ, read-only).

**Тип `enum`** (колонка «Тип») — отдельно от флага: несёт `enum: [...]` со значениями → loader делает
`set_validation` (дропдаун). Обычно с флагом `W` (человек выбирает из списка).

## Как loader материализует (дизайн для след. сессии — код)

| Элемент schema.yaml | Вызов `core/excel` |
|---|---|
| первый лист | `create_workbook(path, sheet=sheets[0].name)` |
| остальные листы | `add_sheet(path, sheet)` |
| колонка | `add_column(path, sheet, column, formula=col.formula if flag==F else None)` |
| `type: enum` | `set_validation(path, sheet, column, allowed=col.enum)` |
| (опц.) статус-цвета | `apply_formatting(...)` из `ui_colors` |

Loader живёт рядом с `template_engine` (напр. `core/engine/table_materializer.py`), дёргается фазой ТАБЛИЦЫ
из `tables_pending`. Контракт — `ToolResult` с фактами `TableCreated`/`SheetCreated`; ошибки → коды
реакций (`SHEET_EXISTS`, `FORMULA_PROTECTED`, …). Не ломать `structure_*` (35/35): фаза ТАБЛИЦЫ — отдельный
вызов после `structure_create`, существующее поведение (отложить в `tables_pending`) остаётся.

## Как авторятся схемы (S20)

Интроспектор из готовых `.xlsx` **невозможен** — книг никогда не существовало (планировались, не делались).
Источник — только прозаические спеки. Мост: `scripts/spec_to_schema.py` (`--verify` = приёмка на собранной
руками `network_config`, `--all --write` = черновики). Собранное руками черновиком **не затирается**.

Разбираются (S21): **табличная** форма (`| \`столбец\` | тип | флаг | прим. |`), **прозовая**
(`\`a (id), b, c (fk)\` — \`W\`;` одной строкой, плюс одиночное `\`type\` = \`F\`` вне списка) и **группы
листов** — составное имя `VISUAL_/SCRIPT_/AUDIO_PATTERNS` разворачивается в отдельные листы, потому что
блок «**Дельты:**» называет их поимённо (`- \`VISUAL_PATTERNS\` (14): + \`…\``); каждый лист = общие
столбцы + своя дельта.

**Граница машины — там, где спека молчит.** Не разбирается и требует владельца (конвертер называет
поимённо, молча не теряет):
- **диапазон без «Дельт»** — `## Листы 4–10: \`ACT_1_HOOK … ACT_7_TRUTH\``: имена середины (ACT_2…ACT_6)
  не объявлены НИГДЕ в спеках, взять их неоткуда;
- **дашборды из секций** (`ANALYTICS`) — часть имён стоит вне кавычек; лист собирается, но помечается
  «проверить полноту», а где имён нет вовсе — не создаётся;
- **составной столбец** `color_primary/secondary` — это два столбца, разводит человек;
- имена длиннее 31 символа — Excel не примет.

**Прозовая форма не объявляет тип** — принят `string`, в схеме стоит строка-предупреждение над столбцами.
Материализация от типа зависит только через `enum` (дропдаун), поэтому черновик рабочий, но тип — под вычитку.

## Статус авторинга схем (6 книг, снимок S21)

Все шесть материализуются в `.xlsx` живым прогоном. «Ждёт владельца» — перечислено ниже таблицы.

| Книга | schema.yaml | Листов | Ждёт владельца |
|---|---|---|---|
| network_config | ✅ собрана РУКАМИ (эталон `--verify`) | 4 | — |
| channel_data | ✅ черновик | 15 | `ANALYTICS`: полнота набора |
| competitor_channel_data | ✅ черновик | 10 | `ANALYTICS`: полнота набора |
| competitor_video_data | ✅ черновик | 11 | имена `ACT_2…ACT_6` (7 листов); `ANALYTICS` не создан; `color_primary/secondary` |
| niche_network_data | ✅ черновик | 11 | `AUDIENCE_PROFILE`: полнота; `ANALYTICS` не создан |
| video_data | ✅ черновик | 16 | имена `ACT_2…ACT_6` (7 листов) |

**`network_dashboard` снят** (S20): книга объявлялась шаблоном, но схемы не существовало ни дня → вечный
`TEMPLATE_NOT_FOUND` при каждом создании сетки. Инвариант против рецидива — `test_structure §33`.

**Интроспектор** (`scripts/introspect_tables.py`) — для авто-генерации из ~90 готовых `.xlsx`, если книги
появятся. Пока владелец не указал путь к книгам → авторим руками из `spec/schemas/*.schema.md` (они и есть
ручная спека колонок). Введём интроспектор, когда будут реальные книги.
