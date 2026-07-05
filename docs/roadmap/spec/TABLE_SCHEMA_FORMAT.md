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

## Статус авторинга схем (7 книг)

| Книга | schema.yaml | Источник |
|---|---|---|
| network_config | ✅ **proof (S7)** | `spec/schemas/network_config.schema.md` |
| competitor_channel_data | 🔲 | `spec/schemas/competitor_channel_data.schema.md` |
| competitor_video_data | 🔲 | + формулы PERFORMANCE |
| channel_data | 🔲 | ~15 листов |
| video_data | 🔲 | ~22 листа + SCENES🆕/статусы (руками, §5.2/5.3) |
| niche_network_data | 🔲 | |
| network_dashboard | 🔲 | (аналитика) |

**Интроспектор** (`scripts/introspect_tables.py`) — для авто-генерации из ~90 готовых `.xlsx`, если книги
появятся. Пока владелец не указал путь к книгам → авторим руками из `spec/schemas/*.schema.md` (они и есть
ручная спека колонок). Введём интроспектор, когда будут реальные книги.
