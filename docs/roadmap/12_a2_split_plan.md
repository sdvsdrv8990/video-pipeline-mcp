# A2 — план распила монолита `server.py` (готов к исполнению)

> Статус: **В РАБОТЕ (S17)** — шаг 1 (скелет) ✅, шаги 2–8 впереди. Единственный незакрытый воркстрим батча фиксов.
>
> **Сеть безопасности готова (S17):** `tests/quick/test_tools_inventory.py` + эталон
> `tools_inventory.golden.json` — 52 инструмента с точными group/title/description/annotations/input_schema.
> Красный тест = контракт клиента поехал. Подключён в CI-гейт; обновление эталона только через `--bless`
> (diff обязан быть виден в ревью). Критерий «52 инструмента на месте» теперь механический, не «на глаз».
>
> **Отклонение от дизайна ниже (осознанное, S17):** `err`/`safe`/`resolve` — **методы** `ToolContext`, а не
> поля-`Callable`. Поля `Callable` стирают сигнатуру для mypy (гейт I4 их не проверяет); методы типизируются.
> Состав зависимостей — как в плане, плюс `workspace_path`/`config_path` (нужны группам вместо модульных
> констант `server.py`). `tools/` добавлен в `[tool.mypy] files`.
> Исполнять инкрементально: **одна группа = один зелёный коммит**; частичный распил = сломанный
> сервер, поэтому на каждом шаге baseline зелёный + 52 инструмента на месте. Свежая сессия/полный контекст.

## Что распиливаем
`server.py::register_basic_tools` (строки 128–1200, ~1072 стр) — одна функция с 52 хендлерами
7 групп + хелперы + спек-листы. По обмеру S15 риск НИЗКИЙ (хендлеры тонкие, логика в `core/*`
движках, движки НЕ трогаем), но объём большой.

## Карта (S16, проверено `grep`)
- **Хендлеры:** fs (12) · memory (2) · tables:data (5 table_ + 4 json_) · analysis (get_×4/find_×2/inspect_×1=7) · excel (14) · structure (5) · search (3).
- **Хелперы внутри функции:** `table_engine`/`excel_engine`/`template_engine`/`link_registry` (475–481), `_err` (483), `_safe` (495).
- **Модульного уровня (готовы к переиспользованию):** `_safe_resolve` (79), `ToolResult`/`ErrorDetail`/`Recovery`/`Fact` (module-top import, S16 I4), `ANNOTATIONS_READONLY/MODIFY/DESTRUCTIVE`.
- **Регистрация:** спек-листы `fs_tools`/`memory_tools`/`tables_tools`/`excel_tools`/`search_tools` + циклы; прямые `engine.register(...)` для `json_read_snapshot` (902) и structure/analysis (1012/1033/1047/1060/1068).

## Дизайн: `ToolContext` (общие зависимости)
Хендлеры — closures над общим состоянием. Ввести `core/tools_context.py` (или `tools/_context.py`):
```python
@dataclass
class ToolContext:
    engine: Engine
    id_generator: IDGenerator
    state_manager: StateManager
    safe_resolve: Callable          # = _safe_resolve (module)
    err: Callable                   # = _err (реестр реакций; нужен engine.reactions)
    safe: Callable                  # = _safe (маппит Table/Excel/Template/LinkError → err)
    table_engine: TableEngine
    excel_engine: ExcelEngine
    template_engine: TemplateEngine
    link_registry: LinkRegistry
```
`_err`/`_safe` вынести на module-level (или в context factory), т.к. они станут общими для групп.
`_err` уже зависит только от `engine.reactions` (S16 A6) — легко параметризуется.

## Целевая структура (закон размещения §5 — `tools/<group>/`)
```
tools/
  _context.py              # ToolContext + build_context(engine, id_gen, sm)
  filesystem/__init__.py   # register(engine, ctx): fs_* (12) + fs_tools spec
  memory/__init__.py       # memory_* (2)
  tables/__init__.py       # table_/json_ (9) + analysis get_/find_/inspect_ (7)
  excel/__init__.py        # excel_* (14) + copy_sheet/inspect_file/get_sheet_info/get_column_names
  structure/__init__.py    # structure_* (5)
  search/__init__.py       # search_* (3)
```
`register_basic_tools` становится тонким: `ctx = build_context(...)`; `filesystem.register(engine, ctx)`; … по группам.

## Порядок исполнения (каждый шаг = зелёный коммит)
1. ✅ **Скелет (S17):** `tools/_context.py` — `ToolContext` (движки + `resolve`/`err`/`safe`) + `build_context` + `ANNOTATIONS_*`. `register_basic_tools` строит `ctx`; локальные имена `table_engine`/`_err`/`_safe` = мост к нетронутым 52 хендлерам. Из `server.py` ушли 11 импортов (исключения ядра больше не нужны — маппинг в контексте). Инвентарь 59/59, audit 44/44, search 24/24, structure 35/35, tables 33/33, ruff/mypy PASS.
2. **filesystem** (эталон паттерна): перенести 12 fs-хендлеров + `fs_tools` в `tools/filesystem/`, вызвать из register_basic_tools. Проверить 52 инструмента + тесты. Коммит.
3. **memory** → 4. **structure** → 5. **search** → 6. **tables+analysis** → 7. **excel**. Каждая группа — тот же паттерн, отдельный зелёный коммит.
8. **Финал:** `register_basic_tools` = только `build_context` + 6 вызовов `register`. Удалить мёртвое. Коммит.

## Критерий приёмки (реал-тест — просьба владельца «инструменты на месте, функции целы»)
- `tools/list` = **52 инструмента** (до и после — идентичный набор имён + группы + annotations).
- **Все тест-наборы зелёные** после КАЖДОГО шага: C1 (audit 41/41·structure 35/35·tables 33/33·search 24/24), C2 (6 симуляций), интеграция на живом сервере (firewall 4/4·tunnel 20/20).
- `ruff`/`mypy` PASS (CI-гейт I3). Диффы движков `core/*` = 0 (не трогаем).
- Реал-пруф на живом сервере (`./run.sh`): `tools/list` через транспорт + смоук вызовов из каждой группы.

## Заметки/риски
- Closures резолвят имена в call-time — но после переезда в модуль замыкание рвётся; ВСЕ общие ссылки идут через `ctx`.
- `_safe` ловит `TableError/ExcelError/TemplateError/LinkError` — импорты этих исключений нужны в `tools/_context.py`.
- Аннотации (`ANNOTATIONS_*`) — вынести в `tools/_context.py` или оставить module-level в server.py и импортировать.
- Не менять имена/схемы/группы инструментов (контракт клиента) — только МЕСТО определения.
