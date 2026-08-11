"""
server.py — Точка входа MCP-сервера видеопайплайна

## Назначение
Принимает JSON-RPC запросы от Claude через туннель.
Обрабатывает через Auth → Firewall → Engine → Tools.

## Запуск
    python server.py            # сервер (127.0.0.1:8080)
    python server.py --tunnel   # сервер + Cloudflare-туннель одной командой

## Порт
    8080 (по умолчанию), слушает 127.0.0.1 — наружу смотрит только туннель.

## Инструменты (4 production)
    fs_get_directory_tree, fs_read_file, fs_create_file, json_read_snapshot

## Изменения аудита
- D1: safe-join путей fs_* (containment внутри workspace/)
- D2: загрузка config/firewall.yaml в Firewall(cfg)
- D3: bearer-аутентификация (MCP_AUTH_TOKEN) ДО файрвола
- D4: реестр реакций (server_reactions.yaml) подключён в Engine
- D10: fail-closed при ошибке парсинга/сбое firewall
- D12: bind 127.0.0.1 + валидация Origin
- D11: запуск туннеля вместе с сервером (--tunnel)
"""

import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path

import yaml

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent))

from core.engine import Engine
from core.firewall import Firewall, FirewallRequest, FirewallDecision
from core.transport import Transport
from core.reactions import Reactions
from core.ids import IDGenerator
from core.state import StateManager
from core.paths import safe_resolve
from core.contracts import ToolResult, Fact
# A2: движки, маппинг исключений ядра и аннотации MCP — общие для групп, живут в контексте.
from tools._context import ANNOTATIONS_MODIFY, ANNOTATIONS_READONLY, build_context
from tools import filesystem, memory, structure


# ═══ КОНФИГУРАЦИЯ ═══

# D12: по умолчанию слушаем localhost — публичный доступ идёт только через туннель.
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "8080"))

BASE_PATH = Path(__file__).parent
WORKSPACE_PATH = BASE_PATH / "workspace"
CONFIG_PATH = BASE_PATH / "config"

# D12: если задан — валидируем заголовок Origin (анти-DNS-rebinding).
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]

# D3: bearer-токен для аутентификации. Если не задан — auth отключена (локальная разработка).
MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")


# ═══ ХЕЛПЕРЫ ═══

def _load_yaml(path: Path) -> dict:
    """Безопасное чтение YAML-конфига (пустой dict, если файла нет)."""
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _safe_resolve(path: str) -> Path:
    """D1+D29: разрешение пути с containment внутри workspace/.

    Делегирует в core/paths.safe_resolve (единая точка, G17).
    Сохранён как обёртка для обратной совместимости с fs_* хендлерами.

    Args:
        path: Путь относительно workspace/

    Returns:
        Абсолютный Path внутри workspace/

    Raises:
        ValueError: если путь выходит за пределы workspace/
    """
    return safe_resolve(path, WORKSPACE_PATH)


def create_server():
    """Создание и настройка сервера.

    Returns:
        Tuple[Engine, Transport, Firewall]
    """
    # D2: реально загружаем конфиг файрвола (раньше игнорировался).
    firewall_config = _load_yaml(CONFIG_PATH / "firewall.yaml")

    # D4: реестр реакций подключаем к движку (раньше висел мёртвым объектом).
    reactions = Reactions(CONFIG_PATH / "server_reactions.yaml")

    firewall = Firewall(firewall_config)
    id_generator = IDGenerator()
    state_manager = StateManager(WORKSPACE_PATH)

    # D24: state_manager передаётся в engine для логирования facts в _SESSION_LOG.
    engine = Engine(reactions=reactions, state_manager=state_manager)

    # Создаём workspace если нет
    WORKSPACE_PATH.mkdir(parents=True, exist_ok=True)

    # Регистрация базовых инструментов
    register_basic_tools(engine, id_generator, state_manager)

    # Транспорт
    transport = Transport(engine=engine, firewall=firewall)

    return engine, transport, firewall


def register_basic_tools(engine: Engine, id_generator: IDGenerator, state_manager: StateManager):
    """Регистрация базовых инструментов.

    Args:
        engine: Движок инструментов
        id_generator: Генератор ID
        state_manager: Менеджер состояния
    """

    async def json_read_snapshot(table: str) -> "ToolResult":
        """Чтение снапшота таблицы."""
        try:
            snapshot = state_manager.read_snapshot(table)
        except ValueError:
            return _err("PATH_ESCAPE", f"Path escapes workspace: {table}")
        if snapshot is None:
            return _err("TABLE_NOT_FOUND", f"Table not found: {table}")
        return ToolResult(status="success", data=snapshot, facts=[Fact(type="SnapshotRead", data={"table": table})])

    # ═══ ДВИЖКИ + ХЕЛПЕРЫ: единая точка (A2 шаг 1 — tools/_context.py) ═══
    # Группы инструментов будут переезжать в tools/<group>/ по одной; там closures
    # недоступны, поэтому все общие ссылки идут через ctx. Локальные имена ниже —
    # мост, чтобы хендлеры не правились на шаге переноса движков.
    ctx = build_context(engine, id_generator, state_manager, CONFIG_PATH)
    table_engine = ctx.table_engine
    excel_engine = ctx.excel_engine
    _err = ctx.err
    _safe = ctx.safe

    # ─── Категория 3: чтения (проекции) ───

    async def table_get_column(table: str, sheet: str, column: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.get_column(table, sheet, column))
        if not ok:
            return res
        return ToolResult(status="success", data={"column": column, "values": res},
                          facts=[Fact(type="ColumnRead", data={"table": table, "sheet": sheet, "column": column, "n": len(res)})])

    async def table_get_row(table: str, sheet: str, row_id: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.get_row(table, sheet, row_id))
        if not ok:
            return res
        return ToolResult(status="success", data={"row_id": row_id, "row": res},
                          facts=[Fact(type="RowRead", data={"table": table, "sheet": sheet, "row_id": row_id})])

    # ─── Категория 3: записи (через очередь) ───

    async def table_set(table: str, sheet: str, row_id: str, column: str, value=None) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.set(table, sheet, row_id, column, value))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res},
                          facts=[Fact(type="RowSet", data={"table": table, "sheet": sheet, "row_id": row_id, "column": column})])

    async def table_append(table: str, sheet: str, data: dict | None = None, id_prefix: str = "ROW") -> "ToolResult":
        ok, res = _safe(lambda: table_engine.append(table, sheet, data or {}, id_prefix))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res, "row_id": res["row_id"]},
                          facts=[Fact(type="RowAppended", data={"table": table, "sheet": sheet, "row_id": res["row_id"]})])

    async def table_delete(table: str, sheet: str, row_id: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.delete(table, sheet, row_id))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res},
                          facts=[Fact(type="RowDeleted", data={"table": table, "sheet": sheet, "row_id": row_id})])

    # ─── Категория 3: очередь (json_*) ───

    async def json_push_to_queue(table: str, action: dict) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.push_to_queue(table, action))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res},
                          facts=[Fact(type="QueuePushed", data={"table": table, "action": res.get("action")})])

    async def json_execute_queue(table: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.execute_queue(table))
        if not ok:
            return res
        return ToolResult(status="success", data=res,
                          facts=[Fact(type="QueueExecuted", data={"table": table, "applied": res["applied"], "skipped": len(res["skipped"])})])

    async def json_clear_queue(table: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.clear_queue(table))
        if not ok:
            return res
        return ToolResult(status="success", data=res,
                          facts=[Fact(type="QueueCleared", data={"table": table, "cleared": res["cleared"]})])

    # ─── Категория 2: структура (excel_*) ───

    async def excel_create_workbook(path: str, sheet: str = "Sheet1") -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.create_workbook(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="WorkbookCreated", data=res)])

    async def excel_add_sheet(path: str, sheet: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.add_sheet(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetAdded", data={"path": path, "sheet": sheet})])

    async def excel_rename_sheet(path: str, sheet: str, new_name: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.rename_sheet(path, sheet, new_name))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetRenamed", data={"path": path, "from": sheet, "to": new_name})])

    async def excel_delete_sheet(path: str, sheet: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.delete_sheet(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetDeleted", data={"path": path, "sheet": sheet})])

    async def excel_reorder_sheets(path: str, order: list) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.reorder_sheets(path, order))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetsReordered", data={"path": path})])

    async def excel_add_column(path: str, sheet: str, column: str, formula: str = "") -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.add_column(path, sheet, column, formula or None))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnAdded", data={"path": path, "sheet": sheet, "column": column})])

    async def excel_delete_column(path: str, sheet: str, column: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.delete_column(path, sheet, column))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnDeleted", data={"path": path, "sheet": sheet, "column": column})])

    async def excel_move_column(path: str, sheet: str, column: str, to_index: int) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.move_column(path, sheet, column, to_index))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnMoved", data={"path": path, "sheet": sheet, "column": column, "to": to_index})])

    async def excel_insert_formula(path: str, sheet: str, cell: str, formula: str, overwrite: bool = False) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.insert_formula(path, sheet, cell, formula, overwrite))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FormulaInserted", data={"path": path, "sheet": sheet, "cell": cell})])

    async def excel_apply_formatting(path: str, sheet: str, target: str, fill: str = "", bold: bool | None = None, font_color: str = "") -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.apply_formatting(path, sheet, target, fill or None, bold, font_color or None))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FormattingApplied", data={"path": path, "sheet": sheet, "target": target})])

    async def excel_set_validation(path: str, sheet: str, column: str, allowed: list) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.set_validation(path, sheet, column, allowed))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ValidationSet", data={"path": path, "sheet": sheet, "column": column})])

    async def excel_read_range(path: str, sheet: str, cell_range: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.read_range(path, sheet, cell_range))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="RangeRead", data={"path": path, "sheet": sheet, "range": cell_range})])

    async def excel_validate_formulas(path: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.validate_formulas(path))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FormulasValidated", data={"path": path, "ok": res["ok"], "errors": len(res["errors"])})])

    # ─── Excel: копирование листа ───

    async def excel_copy_sheet(path: str, sheet: str, new_name: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.copy_sheet(path, sheet, new_name))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetCopied", data={"path": path, "from": sheet, "to": new_name})])

    # ─── Excel: анализ структуры ───

    async def inspect_file(path: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.inspect_file(path))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FileInspected", data={"path": path, "sheets": res["sheet_count"]})])

    async def get_sheet_info(path: str, sheet: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.get_sheet_info(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetInfoRead", data={"path": path, "sheet": sheet, "columns": res["column_count"]})])

    async def get_column_names(path: str, sheet: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.get_column_names(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnNamesRead", data={"path": path, "sheet": sheet, "count": res["count"]})])

    # ─── Таблицы: анализ данных ───

    async def get_unique_values(table: str, sheet: str, column: str, limit: int = 100) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.get_unique_values(table, sheet, column, limit))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="UniqueValuesRead", data={"table": table, "sheet": sheet, "column": column, "count": res["count"]})])

    async def get_value_counts(table: str, sheet: str, column: str, limit: int = 10) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.get_value_counts(table, sheet, column, limit))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ValueCountsRead", data={"table": table, "sheet": sheet, "column": column, "total": res["total"]})])

    async def find_duplicates(table: str, sheet: str, columns: list[str] | None = None) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.find_duplicates(table, sheet, columns))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="DuplicatesFound", data={"table": table, "sheet": sheet, "groups": res["duplicate_groups"], "rows": res["duplicate_rows"]})])

    async def find_nulls(table: str, sheet: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.find_nulls(table, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="NullsFound", data={"table": table, "sheet": sheet, "columns_with_nulls": res["columns_with_nulls"]})])

    # ═══ РЕГИСТРАЦИЯ (все хендлеры определены выше) ═══


    filesystem.register(engine, ctx)
    memory.register(engine, ctx)

    engine.register(
        name="json_read_snapshot", title="Таблицы: снапшот (read.json)", description="Чтение снапшота таблицы (read.json)",
        input_schema={"type": "object", "properties": {"table": {"type": "string", "description": "Путь к таблице (сущности) относительно workspace"}}, "required": ["table"]},
        handler=json_read_snapshot, group="tables", annotations=ANNOTATIONS_READONLY
    )

    # ═══ КАТЕГОРИЯ 3: данные таблиц (json_* очередь + 5 примитивов) ═══
    # Формат кортежа: (name, title, description, schema, handler, annotations).
    _TABLE = {"type": "string", "description": "Путь к таблице (сущности) относительно workspace"}
    _SHEET = {"type": "string", "description": "Имя листа (регистр важен)"}
    tables_tools = [
        ("table_get_column", "Таблицы: столбец {id:value}", "Проекция одного столбца листа: {row_id: value}",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}}, "required": ["table", "sheet", "column"]},
         table_get_column, ANNOTATIONS_READONLY),
        ("table_get_row", "Таблицы: строка {col:value}", "Одна строка целиком: {column: value}",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "row_id": {"type": "string", "description": "ID строки"}}, "required": ["table", "sheet", "row_id"]},
         table_get_row, ANNOTATIONS_READONLY),
        ("table_set", "Таблицы: изменить поле", "Изменить поле строки (RMW через очередь). Защита формул + enum.",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "row_id": {"type": "string", "description": "ID строки"}, "column": {"type": "string", "description": "Имя столбца"}, "value": {"description": "Новое значение (любой JSON-тип)"}}, "required": ["table", "sheet", "row_id", "column", "value"]},
         table_set, ANNOTATIONS_MODIFY),
        ("table_append", "Таблицы: новая строка", "Добавить строку. ID присваивает сервер (приходит в facts).",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "data": {"type": "object", "description": "Поля новой строки {column: value}"}, "id_prefix": {"type": "string", "description": "Префикс ID строки", "default": "ROW"}}, "required": ["table", "sheet", "data"]},
         table_append, ANNOTATIONS_MODIFY),
        ("table_delete", "Таблицы: удалить строку", "Удалить строку по ID (через очередь).",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "row_id": {"type": "string", "description": "ID строки"}}, "required": ["table", "sheet", "row_id"]},
         table_delete, ANNOTATIONS_MODIFY),
        ("json_push_to_queue", "Таблицы: очередь → добавить", "Положить пишущую операцию (set/append/delete) в write.json.",
         {"type": "object", "properties": {"table": _TABLE, "action": {"type": "object", "description": "{action: set|append|delete, sheet, ...}"}}, "required": ["table", "action"]},
         json_push_to_queue, ANNOTATIONS_MODIFY),
        ("json_execute_queue", "Таблицы: очередь → применить", "Применить очередь к read.json (RMW). Синк в .xlsx отложен.",
         {"type": "object", "properties": {"table": _TABLE}, "required": ["table"]},
         json_execute_queue, ANNOTATIONS_MODIFY),
        ("json_clear_queue", "Таблицы: очередь → очистить", "Очистить очередь без применения (отладка/сброс).",
         {"type": "object", "properties": {"table": _TABLE}, "required": ["table"]},
         json_clear_queue, ANNOTATIONS_MODIFY),
        ("get_unique_values", "Таблицы: уникальные значения", "Уникальные значения столбца.",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}, "limit": {"type": "integer", "description": "Максимум значений", "default": 100}}, "required": ["table", "sheet", "column"]},
         get_unique_values, ANNOTATIONS_READONLY),
        ("get_value_counts", "Таблицы: частотный анализ", "Top-N наиболее частых значений столбца.",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}, "limit": {"type": "integer", "description": "Top-N", "default": 10}}, "required": ["table", "sheet", "column"]},
         get_value_counts, ANNOTATIONS_READONLY),
        ("find_duplicates", "Таблицы: дубликаты", "Поиск дубликатов по столбцам (или всем).",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "columns": {"type": "array", "items": {"type": "string"}, "description": "Столбцы для проверки (все если пусто)"}}, "required": ["table", "sheet"]},
         find_duplicates, ANNOTATIONS_READONLY),
        ("find_nulls", "Таблицы: пустые значения", "Поиск пустых/пропущенных значений по всем столбцам.",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET}, "required": ["table", "sheet"]},
         find_nulls, ANNOTATIONS_READONLY),
    ]
    for name, title, desc, schema, handler, annot in tables_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="tables", annotations=annot)  # type: ignore[arg-type]

    # ═══ КАТЕГОРИЯ 2: структура таблиц (excel_*) ═══
    _PATH = {"type": "string", "description": "Путь к .xlsx относительно workspace"}
    excel_tools = [
        ("excel_create_workbook", "Excel: новая книга", "Создать новый .xlsx (не перезаписывает существующий).",
         {"type": "object", "properties": {"path": _PATH, "sheet": {"type": "string", "description": "Имя первого листа", "default": "Sheet1"}}, "required": ["path"]},
         excel_create_workbook, ANNOTATIONS_MODIFY),
        ("excel_add_sheet", "Excel: добавить лист", "Добавить лист в книгу.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET}, "required": ["path", "sheet"]},
         excel_add_sheet, ANNOTATIONS_MODIFY),
        ("excel_rename_sheet", "Excel: переименовать лист", "Переименовать лист.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "new_name": {"type": "string", "description": "Новое имя листа"}}, "required": ["path", "sheet", "new_name"]},
         excel_rename_sheet, ANNOTATIONS_MODIFY),
        ("excel_delete_sheet", "Excel: удалить лист", "Удалить лист (нельзя последний).",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET}, "required": ["path", "sheet"]},
         excel_delete_sheet, ANNOTATIONS_MODIFY),
        ("excel_reorder_sheets", "Excel: порядок листов", "Переупорядочить листы (order = все листы книги).",
         {"type": "object", "properties": {"path": _PATH, "order": {"type": "array", "items": {"type": "string"}, "description": "Полный список листов в новом порядке"}}, "required": ["path", "order"]},
         excel_reorder_sheets, ANNOTATIONS_MODIFY),
        ("excel_add_column", "Excel: добавить столбец", "Добавить столбец (заголовок в строку 1). formula — опционально.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца (заголовок)"}, "formula": {"type": "string", "description": "Формула-образец (опц.)"}}, "required": ["path", "sheet", "column"]},
         excel_add_column, ANNOTATIONS_MODIFY),
        ("excel_delete_column", "Excel: удалить столбец", "Удалить столбец по имени заголовка.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}}, "required": ["path", "sheet", "column"]},
         excel_delete_column, ANNOTATIONS_MODIFY),
        ("excel_move_column", "Excel: переместить столбец", "Переместить столбец на позицию to_index (1-based).",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}, "to_index": {"type": "integer", "description": "Новая позиция (1-based)"}}, "required": ["path", "sheet", "column", "to_index"]},
         excel_move_column, ANNOTATIONS_MODIFY),
        ("excel_insert_formula", "Excel: вставить формулу", "Формула в ячейку. Не перезаписывает существующую молча (overwrite).",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "cell": {"type": "string", "description": "Ячейка (напр. C2)"}, "formula": {"type": "string", "description": "Формула (с '=' или без)"}, "overwrite": {"type": "boolean", "description": "Перезаписать существующую формулу", "default": False}}, "required": ["path", "sheet", "cell", "formula"]},
         excel_insert_formula, ANNOTATIONS_MODIFY),
        ("excel_apply_formatting", "Excel: форматирование", "Стили на ячейку/диапазон (заливка/жирный/цвет шрифта).",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "target": {"type": "string", "description": "Ячейка или диапазон (A1 / A1:C3)"}, "fill": {"type": "string", "description": "HEX заливки RRGGBB"}, "bold": {"type": "boolean", "description": "Жирный"}, "font_color": {"type": "string", "description": "HEX цвета шрифта RRGGBB"}}, "required": ["path", "sheet", "target"]},
         excel_apply_formatting, ANNOTATIONS_MODIFY),
        ("excel_set_validation", "Excel: выпадающий список", "Data Validation (dropdown) на столбец — материализует enum из схемы.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}, "allowed": {"type": "array", "items": {"type": "string"}, "description": "Список допустимых значений"}}, "required": ["path", "sheet", "column", "allowed"]},
         excel_set_validation, ANNOTATIONS_MODIFY),
        ("excel_read_range", "Excel: сырой диапазон (отладка)", "ОТЛАДКА: сырой 2D-массив ячеек. Рабочее чтение — json_read_snapshot.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "cell_range": {"type": "string", "description": "Диапазон (напр. A1:C10)"}}, "required": ["path", "sheet", "cell_range"]},
         excel_read_range, ANNOTATIONS_READONLY),
        ("excel_validate_formulas", "Excel: проверить формулы", "Поиск ошибок формул (#REF!/#VALUE!/…) по всем листам.",
         {"type": "object", "properties": {"path": _PATH}, "required": ["path"]},
         excel_validate_formulas, ANNOTATIONS_READONLY),
        ("excel_copy_sheet", "Excel: копировать лист", "Копирование листа с данными и форматированием.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "new_name": {"type": "string", "description": "Имя копии листа"}}, "required": ["path", "sheet", "new_name"]},
         excel_copy_sheet, ANNOTATIONS_MODIFY),
        ("inspect_file", "Excel: обзор книги", "Обзор структуры: листы, размеры, формат.",
         {"type": "object", "properties": {"path": _PATH}, "required": ["path"]},
         inspect_file, ANNOTATIONS_READONLY),
        ("get_sheet_info", "Excel: анализ листа", "Детальный анализ: колонки, типы, превью данных.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET}, "required": ["path", "sheet"]},
         get_sheet_info, ANNOTATIONS_READONLY),
        ("get_column_names", "Excel: имена колонок", "Быстрый список колонок листа.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET}, "required": ["path", "sheet"]},
         get_column_names, ANNOTATIONS_READONLY),
    ]
    for name, title, desc, schema, handler, annot in excel_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="excel", annotations=annot)  # type: ignore[arg-type]

    structure.register(engine, ctx)

    # ═══ УМНЫЙ ПОИСК ПО ТАБЛИЦАМ ═══
    from core.search.query_planner import QueryPlanner, SearchError

    planner = QueryPlanner(table_engine, state_manager.workspace_path)

    async def search_tables(yaml_query: str | None = None, query_dict: dict | None = None) -> "ToolResult":
        """Умный поиск по таблицам через YAML-запрос.

        Принимает YAML-строку или dict с запросом. Возвращает объединённые
        результаты с прогрессом выполнения.
        """
        try:
            if yaml_query:
                import yaml
                data = yaml.safe_load(yaml_query)
            elif query_dict:
                data = query_dict
            else:
                return _err("VALIDATION_ERROR", "Укажи yaml_query или query_dict")

            plan = planner.load_query_from_dict(data)
            result = planner.execute_plan(plan)

            return ToolResult(status="success", data=result,
                              facts=[Fact(type="SearchCompleted", data={
                                  "query": plan.name,
                                  "reads": result["metadata"]["reads_executed"],
                                  "rows": result["metadata"]["total_rows"],
                                  "errors": result["metadata"]["reads_failed"],
                              })])
        except SearchError as e:
            return _err(e.code, e.message, e.reason)
        except Exception as e:
            return _err("INTERNAL_ERROR", f"Ошибка поиска: {e}")

    async def search_quick(table: str, sheet: str, column: str = "",
                           filter_col: str = "", filter_op: str = "eq",
                           filter_val: str = "", limit: int = 100) -> "ToolResult":
        """Быстрый поиск: одно чтение с фильтром (без YAML)."""
        query = {
            "name": "quick_search",
            "reads": [{
                "table": table,
                "sheet": sheet,
                "columns": [column] if column else [],
                "filter": {filter_col: {filter_op: filter_val}} if filter_col else {},
            }],
            "limit": limit,
        }
        plan = planner.load_query_from_dict(query)
        result = planner.execute_plan(plan)
        return ToolResult(status="success", data=result,
                          facts=[Fact(type="QuickSearch", data={
                              "table": table, "sheet": sheet,
                              "rows": result["metadata"]["total_rows"],
                          })])

    async def search_multi(tables: list[dict], join_key: str = "",
                           filter_after: dict | None = None,
                           sort_col: str = "", sort_order: str = "asc",
                           limit: int = 100) -> "ToolResult":
        """Многотабличный поиск с объединением."""
        reads = []
        for t in tables:
            reads.append({
                "table": t.get("table", ""),
                "sheet": t.get("sheet", ""),
                "columns": t.get("columns", []),
                "filter": t.get("filter", {}),
            })
        query = {
            "name": "multi_search",
            "reads": reads,
            "join": {"on": join_key, "strategy": "inner"} if join_key else None,
            "filter": filter_after or {},
            "sort": {"column": sort_col, "order": sort_order} if sort_col else None,
            "limit": limit,
        }
        plan = planner.load_query_from_dict(query)
        result = planner.execute_plan(plan)
        return ToolResult(status="success", data=result,
                          facts=[Fact(type="MultiSearch", data={
                              "tables": len(tables),
                              "rows": result["metadata"]["total_rows"],
                          })])

    # Регистрация
    search_tools = [
        ("search_tables", "Поиск: YAML-запрос", "Умный поиск по таблицам через YAML (очередь, многопоточность, объединение)",
         {"type": "object", "properties": {
             "yaml_query": {"type": "string", "description": "YAML-строка с запросом"},
             "query_dict": {"type": "object", "description": "Dict с запросом (альтернатива YAML)"},
         }},
         search_tables, ANNOTATIONS_READONLY),
        ("search_quick", "Поиск: быстрый", "Быстрый поиск в одной таблице с фильтром (без YAML)",
         {"type": "object", "properties": {
             "table": {"type": "string", "description": "Путь к таблице"},
             "sheet": {"type": "string", "description": "Имя листа"},
             "column": {"type": "string", "description": "Столбец для выборки (все если пусто)"},
             "filter_col": {"type": "string", "description": "Столбец фильтра"},
             "filter_op": {"type": "string", "enum": ["eq", "neq", "gt", "lt", "contains", "in"], "description": "Оператор"},
             "filter_val": {"type": "string", "description": "Значение фильтра"},
             "limit": {"type": "integer", "description": "Максимум строк", "default": 100},
         }, "required": ["table", "sheet"]},
         search_quick, ANNOTATIONS_READONLY),
        ("search_multi", "Поиск: многотабличный", "Поиск с объединением нескольких таблиц (JOIN по ключу)",
         {"type": "object", "properties": {
             "tables": {"type": "array", "items": {"type": "object", "properties": {
                 "table": {"type": "string"}, "sheet": {"type": "string"},
                 "columns": {"type": "array", "items": {"type": "string"}},
                 "filter": {"type": "object"},
             }}, "description": "Список таблиц для поиска"},
             "join_key": {"type": "string", "description": "Ключ для объединения (JOIN)"},
             "filter_after": {"type": "object", "description": "Фильтр после объединения"},
             "sort_col": {"type": "string", "description": "Столбец сортировки"},
             "sort_order": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
             "limit": {"type": "integer", "default": 100},
         }, "required": ["tables"]},
         search_multi, ANNOTATIONS_READONLY),
    ]
    for name, title, desc, schema, handler, annot in search_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="search", annotations=annot)  # type: ignore[arg-type]


def _jsonrpc_error(request_id, code: int, message: str) -> dict:
    """Сборка JSON-RPC ошибки (для транспортного уровня)."""
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def run_server(host: str = HOST, port: int = PORT, use_tunnel: bool = False):
    """Запуск сервера.

    Args:
        host: Хост
        port: Порт
        use_tunnel: Поднять Cloudflare-туннель вместе с сервером (D11)
    """
    engine, transport, firewall = create_server()

    print("=== MCP-сервер видеопайплайна ===")
    print(f"Хост: {host}")
    print(f"Порт: {port}")
    print(f"Workspace: {WORKSPACE_PATH}")
    print(f"Инструментов: {len(engine.tools)}")
    print(f"Файрвол: активен (config: {'загружен' if (CONFIG_PATH / 'firewall.yaml').exists() else 'дефолт'})")
    print(f"Аутентификация: {'активна (bearer-токен)' if MCP_AUTH_TOKEN else 'отключена (MCP_AUTH_TOKEN не задан)'}")
    print()

    from aiohttp import web

    async def handle_jsonrpc(request: "web.Request") -> "web.Response":
        """Обработка JSON-RPC запросов: Origin → Auth → Firewall → Transport."""
        # D12: валидация Origin (если сконфигурирован allowlist).
        # D12: fail-closed — запрос БЕЗ Origin при заданном allowlist = блок.
        origin = request.headers.get("Origin")
        if ALLOWED_ORIGINS:
            if not origin or origin not in ALLOWED_ORIGINS:
                return web.json_response(_jsonrpc_error(None, -32002, "Forbidden origin"), status=403)

        try:
            raw_request = await request.text()
        except Exception:
            return web.json_response(_jsonrpc_error(None, -32700, "Cannot read body"), status=400)

        # D10: fail-closed — не можем распарсить/проверить → блокируем, а не пропускаем.
        try:
            req_data = json.loads(raw_request)
        except json.JSONDecodeError as e:
            return web.json_response(_jsonrpc_error(None, -32700, f"Parse error: {e}"), status=400)

        # D3: bearer-аутентификация ДО файрвола. Если MCP_AUTH_TOKEN не задан — пропускаем (локальная разработка).
        if MCP_AUTH_TOKEN:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32001, "AUTH_REQUIRED: Требуется заголовок Authorization: Bearer <token>"),
                    status=401
                )
            token = auth_header[7:]  # strip "Bearer "
            if not secrets.compare_digest(token, MCP_AUTH_TOKEN):
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32001, "AUTH_FAILED: Неверный токен аутентификации"),
                    status=401
                )

        if firewall:
            try:
                fw_request = FirewallRequest(
                    ip=request.remote or "127.0.0.1",
                    method=req_data.get("method", "") if isinstance(req_data, dict) else "",
                    params=req_data.get("params", {}) if isinstance(req_data, dict) else {},
                    timestamp=time.time()
                )
                fw_result = firewall.check(fw_request)
            except Exception as e:
                # D10: любой сбой firewall = блокировка (fail-closed), не пропуск.
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32000, f"Firewall error (blocked): {e}"),
                    status=403
                )

            # D21: RATE_LIMIT и BLOCK — разные HTTP-коды, чтобы Claude различал.
            if fw_result.decision == FirewallDecision.BLOCK:
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32000, f"Blocked: {fw_result.reason}"),
                    status=403
                )
            if fw_result.decision == FirewallDecision.RATE_LIMIT:
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32001, f"Rate limit exceeded: {fw_result.reason}"),
                    status=429,
                    headers={"Retry-After": "5"}
                )
            if fw_result.decision != FirewallDecision.ALLOW:
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32000, f"Blocked: {fw_result.reason}"),
                    status=403
                )

        # Лог факта подключения клиента: MCP-метод `initialize` = новый сеанс.
        # Это авторитетный сигнал, что Claude AI Web достучался до сервера через туннель.
        if isinstance(req_data, dict) and req_data.get("method") == "initialize":
            params = req_data.get("params") or {}
            client = params.get("clientInfo") or {}
            print(
                f"✅ Claude AI Web подключился: {client.get('name', 'unknown')} "
                f"{client.get('version', '?')} "
                f"(MCP protocol {params.get('protocolVersion', '?')}, ip={request.remote or '?'})"
            )

        # Обработка запроса. None → это была нотификация → HTTP 202 без тела (D13).
        response_text = await transport.handle_request(raw_request)
        if response_text is None:
            return web.Response(status=202)
        return web.Response(text=response_text, content_type="application/json")

    app = web.Application()
    app.router.add_post("/", handle_jsonrpc)
    app.router.add_post("/mcp", handle_jsonrpc)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    print(f"Сервер запущен на http://{host}:{port}")
    print(f"JSON-RPC endpoint: http://{host}:{port}/mcp")

    # D11: поднимаем туннель вместе с сервером (одной командой).
    tunnel = None
    tunnel_status_str = "нет"
    if use_tunnel:
        from core.transport.tunnel import CloudflaredTunnel
        tunnel = CloudflaredTunnel(port=port, config_path=CONFIG_PATH / "tunnel.yaml")
        try:
            public_url = tunnel.start()
            # Проверяем реальный статус соединения (не просто "процесс запущен").
            st = tunnel.status()
            if st["connected"]:
                tunnel_status_str = f"поднят → {public_url}/mcp"
                print()
                print(f"🌐 Публичный URL (вставь в коннектор Claude): {public_url}/mcp")
                # Рекомендация: quick → named для продакшена.
                if "trycloudflare.com" in (public_url or ""):
                    print()
                    print("💡 Рекомендация: quick-режим подходит для разработки.")
                    print("   Для продакшена используй named-режим:")
                    print("   • токен: экспорт MCP_TUNNEL_TOKEN из дашборда Cloudflare")
                    print("   • credentials: домен + файл credentials (см. tunnel.py)")
            else:
                tunnel_status_str = "процесс жив, но соединение НЕ установлено"
                print()
                print("⚠️  Туннель запущен, но соединение не установлено.")
                if st["last_error"]:
                    print(f"   Причина: {st['last_error']}")
                print(f"   Uptime: {st['uptime_sec']}s | Попыток перезапуска: {st['attempts']}")
                print()
                print("   Режимы работы Cloudflare Tunnel:")
                print("   • quick (без аккаунта): работает сразу, URL эфемерный (*.trycloudflare.com)")
                print("   • named + token: нужен токен из дашборда (env MCP_TUNNEL_TOKEN)")
                print("   • named + credentials: нужен домен + credentials файл")
        except Exception as e:
            tunnel_status_str = f"ошибка: {e}"
            print(f"⚠️  Туннель не поднят: {e}")
            print("   Сервер работает локально.")
            tunnel = None

    # Статус готовности (по спецификации MCP SDK).
    print()
    print(f"Статус: ГОТОВ | Туннель: {tunnel_status_str}")
    print("Для остановки: Ctrl+C")

    try:
        # Мониторинг туннеля: печатаем ТОЛЬКО изменения статуса, а не шум каждые N сек.
        # Восстановление соединения выполняет супервизор в CloudflaredTunnel сам —
        # здесь только наблюдаем его status() и сообщаем переходы в консоль.
        prev = tunnel.status() if tunnel else {}  # dict: блок туннеля ниже под `if not tunnel: continue`

        # Хот-релоад декларативного config без рестарта: следим за mtime файлов.
        # firewall.yaml → firewall.reload() (fail-closed), server_reactions.yaml →
        # reactions.load(). tunnel.yaml НЕ входит: смена режима/порта требует
        # рестарта cloudflared (честно). Код handlers/core тоже требует рестарта.
        reactions = getattr(engine, "reactions", None)
        watched = {
            CONFIG_PATH / "firewall.yaml": "firewall",
            CONFIG_PATH / "server_reactions.yaml": "reactions",
        }
        def _mtime(p: "Path") -> float:
            try:
                return p.stat().st_mtime if p.exists() else 0.0
            except OSError:
                return 0.0
        cfg_mtime = {p: _mtime(p) for p in watched}

        while True:
            await asyncio.sleep(10)

            # 0) Хот-релоад config по изменению mtime (работает и без туннеля).
            for cfg_path, kind in watched.items():
                m = _mtime(cfg_path)
                if m == cfg_mtime[cfg_path]:
                    continue
                cfg_mtime[cfg_path] = m  # фиксируем сразу → битый конфиг не ретрайдим каждые 10с
                try:
                    if kind == "firewall":
                        if firewall and firewall.reload(_load_yaml(cfg_path)):
                            print("♻️  [config] firewall.yaml перезагружен без рестарта")
                        else:
                            print("⚠️  [config] firewall.yaml НЕ применён (битый конфиг) — держим прежние правила (fail-closed)")
                    elif kind == "reactions" and reactions is not None:
                        reactions.load(cfg_path)
                        print("♻️  [config] server_reactions.yaml перезагружен без рестарта")
                except Exception as e:
                    print(f"⚠️  [config] {cfg_path.name} НЕ применён: {e} — держим прежнее")

            if not tunnel:
                continue
            st = tunnel.status()

            # 1) Публичный URL сменился (для quick-режима — норма при реконнекте процесса).
            #    Самое важное сообщение: старый адрес в коннекторе Claude уже мёртв.
            if st["public_url"] and st["public_url"] != prev["public_url"]:
                print()
                print(f"🌐 [tunnel] ПУБЛИЧНЫЙ URL ИЗМЕНИЛСЯ → {st['public_url']}/mcp")
                print("   ⚠️  Обнови адрес в коннекторе Claude AI Web — старый больше не отвечает.")
                print()
            # 2) Соединение потеряно.
            if prev["connected"] and not st["connected"]:
                reason = st["last_error"] or "нет соединения"
                print(f"🔴 [tunnel] соединение потеряно (uptime={st['uptime_sec']}s, попыток={st['attempts']}): {reason}")
            # 3) Соединение восстановлено.
            elif not prev["connected"] and st["connected"]:
                print(f"🟢 [tunnel] соединение восстановлено → {st['public_url']}/mcp")
            # 4) Новая ошибка без смены флага connected.
            elif st["last_error"] and st["last_error"] != prev["last_error"]:
                print(f"⚠️  [tunnel] {st['last_error']}")

            prev = st
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if tunnel:
            tunnel.stop()
        await runner.cleanup()


def main():
    """Главная функция."""
    import argparse

    # Построчная буферизация stdout/stderr: при выводе в файл/пайп (не tty) Python
    # по умолчанию БЛОЧНО буферизует stdout — статусные сообщения (URL туннеля,
    # подключение Claude) зависают в буфере и не видны. line_buffering=True флашит
    # на каждой строке: буфер сохраняется (быстрый вывод), но сообщения не теряются.
    # Не трогаем при отсутствии reconfigure (заглушки stdout в тестах/встраивании).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="MCP-сервер видеопайплайна")
    parser.add_argument("--host", default=HOST, help="Хост (по умолчанию: %(default)s)")
    parser.add_argument("--port", type=int, default=PORT, help="Порт (по умолчанию: %(default)s)")
    parser.add_argument("--tunnel", action="store_true", help="Поднять Cloudflare-туннель вместе с сервером (D11)")
    args = parser.parse_args()

    asyncio.run(run_server(args.host, args.port, use_tunnel=args.tunnel))


if __name__ == "__main__":
    main()
