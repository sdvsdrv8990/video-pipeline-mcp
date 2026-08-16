"""
tools/tables — данные таблиц (Категория 3): снапшот, примитивы чтения/записи,
очередь write.json и анализ данных (уникальные/частоты/дубликаты/пустые).

Тонкие обёртки: вся работа с read.json/write.json — в core/tables/TableEngine.
Контракт зафиксирован эталоном tests/quick/tools_inventory.golden.json.
"""

from core.contracts import Fact, ToolResult
from core.engine import Engine
from tools._schemas import SHEET, TABLE
from tools._context import ANNOTATIONS_MODIFY, ANNOTATIONS_READONLY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы tables в движке."""

    async def json_read_snapshot(table: str) -> "ToolResult":
        """Чтение снапшота таблицы."""
        try:
            snapshot = ctx.state_manager.read_snapshot(table)
        except ValueError as _pe:
            return ctx.err_path(_pe, f"Path escapes workspace: {table}")
        if snapshot is None:
            return ctx.err("TABLE_NOT_FOUND", f"Table not found: {table}")
        return ToolResult(status="success", data=snapshot, facts=[Fact(type="SnapshotRead", data={"table": table})])

    # ─── Категория 3: чтения (проекции) ───

    async def table_get_column(table: str, sheet: str, column: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.get_column(table, sheet, column))
        if not ok:
            return res
        return ToolResult(status="success", data={"column": column, "values": res},
                          facts=[Fact(type="ColumnRead", data={"table": table, "sheet": sheet, "column": column, "n": len(res)})])

    async def table_get_row(table: str, sheet: str, row_id: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.get_row(table, sheet, row_id))
        if not ok:
            return res
        return ToolResult(status="success", data={"row_id": row_id, "row": res},
                          facts=[Fact(type="RowRead", data={"table": table, "sheet": sheet, "row_id": row_id})])

    # ─── Категория 3: записи (через очередь) ───

    async def table_set(table: str, sheet: str, row_id: str, column: str, value=None) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.set(table, sheet, row_id, column, value))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res},
                          facts=[Fact(type="RowSet", data={"table": table, "sheet": sheet, "row_id": row_id, "column": column})])

    async def table_update(table: str, sheet: str, row_id: str, data: dict | None = None) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.update(table, sheet, row_id, data or {}))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res, "fields": len(res["data"])},
                          facts=[Fact(type="RowUpdated", data={"table": table, "sheet": sheet, "row_id": row_id,
                                                               "columns": sorted(res["data"])})])

    async def table_find_row(table: str, sheet: str, where: dict | None = None, limit: int = 20) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.find_row(table, sheet, where or {}, limit))
        if not ok:
            return res
        return ToolResult(status="success", data=res,
                          facts=[Fact(type="RowsFound", data={"table": table, "sheet": sheet, "count": res["count"]})])

    async def table_append(table: str, sheet: str, data: dict | None = None, id_prefix: str = "ROW") -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.append(table, sheet, data or {}, id_prefix))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res, "row_id": res["row_id"]},
                          facts=[Fact(type="RowAppended", data={"table": table, "sheet": sheet, "row_id": res["row_id"]})])

    async def table_delete(table: str, sheet: str, row_id: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.delete(table, sheet, row_id))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res},
                          facts=[Fact(type="RowDeleted", data={"table": table, "sheet": sheet, "row_id": row_id})])

    # ─── Категория 3: очередь (json_*) ───

    async def json_push_to_queue(table: str, action: dict) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.push_to_queue(table, action))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res},
                          facts=[Fact(type="QueuePushed", data={"table": table, "action": res.get("action")})])

    async def json_execute_queue(table: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.execute_queue(table))
        if not ok:
            return res
        return ToolResult(status="success", data=res,
                          facts=[Fact(type="QueueExecuted", data={"table": table, "applied": res["applied"], "skipped": len(res["skipped"])})])

    async def json_clear_queue(table: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.clear_queue(table))
        if not ok:
            return res
        return ToolResult(status="success", data=res,
                          facts=[Fact(type="QueueCleared", data={"table": table, "cleared": res["cleared"]})])

    # ─── Таблицы: анализ данных ───

    async def get_unique_values(table: str, sheet: str, column: str, limit: int = 100) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.get_unique_values(table, sheet, column, limit))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="UniqueValuesRead", data={"table": table, "sheet": sheet, "column": column, "count": res["count"]})])

    async def get_value_counts(table: str, sheet: str, column: str, limit: int = 10) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.get_value_counts(table, sheet, column, limit))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ValueCountsRead", data={"table": table, "sheet": sheet, "column": column, "total": res["total"]})])

    async def find_duplicates(table: str, sheet: str, columns: list[str] | None = None) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.find_duplicates(table, sheet, columns))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="DuplicatesFound", data={"table": table, "sheet": sheet, "groups": res["duplicate_groups"], "rows": res["duplicate_rows"]})])

    async def find_nulls(table: str, sheet: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.table_engine.find_nulls(table, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="NullsFound", data={"table": table, "sheet": sheet, "columns_with_nulls": res["columns_with_nulls"]})])

    engine.register(
        name="json_read_snapshot", title="Таблицы: снапшот (read.json)", description="Чтение снапшота таблицы (read.json)",
        input_schema={"type": "object", "properties": {"table": {"type": "string", "description": "Путь к таблице (сущности) относительно workspace"}}, "required": ["table"]},
        handler=json_read_snapshot, group="tables", annotations=ANNOTATIONS_READONLY
    )

    # ═══ КАТЕГОРИЯ 3: данные таблиц (json_* очередь + 5 примитивов) ═══
    # Формат кортежа: (name, title, description, schema, handler, annotations).
    tables_tools = [
        ("table_get_column", "Таблицы: столбец", "Проекция одного столбца листа: {row_id: value}",
         {"type": "object", "properties": {"table": TABLE, "sheet": SHEET, "column": {"type": "string", "description": "Имя столбца"}}, "required": ["table", "sheet", "column"]},
         table_get_column, ANNOTATIONS_READONLY),
        ("table_get_row", "Таблицы: строка", "Одна строка целиком: {column: value}",
         {"type": "object", "properties": {"table": TABLE, "sheet": SHEET, "row_id": {"type": "string", "description": "ID строки"}}, "required": ["table", "sheet", "row_id"]},
         table_get_row, ANNOTATIONS_READONLY),
        ("table_set", "Таблицы: изменить поле", "Изменить поле строки (RMW через очередь). Защита формул + enum.",
         {"type": "object", "properties": {"table": TABLE, "sheet": SHEET, "row_id": {"type": "string", "description": "ID строки"}, "column": {"type": "string", "description": "Имя столбца"}, "value": {"type": ["string", "number", "boolean", "object", "array", "null"], "description": "Новое значение (любой JSON-тип)"}}, "required": ["table", "sheet", "row_id", "column", "value"]},
         table_set, ANNOTATIONS_MODIFY),
        ("table_update", "Таблицы: изменить строку", "Изменить НЕСКОЛЬКО полей строки ОДНОЙ операцией: применяется целиком или не применяется вовсе. Для одного поля дешевле table_set.",
         {"type": "object", "properties": {"table": TABLE, "sheet": SHEET, "row_id": {"type": "string", "description": "ID строки (найти по значению — table_find_row)"}, "data": {"type": "object", "description": "Изменяемые поля {column: value}"}}, "required": ["table", "sheet", "row_id", "data"]},
         table_update, ANNOTATIONS_MODIFY),
        ("table_find_row", "Таблицы: найти строку по значению", "ID строк по значениям столбцов: {column: value} → [row_id]. Берут, когда значение известно, а ID нет.",
         {"type": "object", "properties": {"table": TABLE, "sheet": SHEET, "where": {"type": "object", "description": "Условия {column: value}, соединяются И"}, "limit": {"type": "integer", "description": "Максимум ID в ответе", "default": 20}}, "required": ["table", "sheet", "where"]},
         table_find_row, ANNOTATIONS_READONLY),
        ("table_append", "Таблицы: новая строка", "Добавить строку. ID присваивает сервер (приходит в facts).",
         {"type": "object", "properties": {"table": TABLE, "sheet": SHEET, "data": {"type": "object", "description": "Поля новой строки {column: value}"}, "id_prefix": {"type": "string", "description": "Префикс ID строки", "default": "ROW"}}, "required": ["table", "sheet", "data"]},
         table_append, ANNOTATIONS_MODIFY),
        ("table_delete", "Таблицы: удалить строку", "Удалить строку по ID (через очередь).",
         {"type": "object", "properties": {"table": TABLE, "sheet": SHEET, "row_id": {"type": "string", "description": "ID строки"}}, "required": ["table", "sheet", "row_id"]},
         table_delete, ANNOTATIONS_MODIFY),
        ("json_push_to_queue", "Таблицы: очередь → добавить", "Положить пишущую операцию (set/update/append/delete) в write.json.",
         {"type": "object", "properties": {"table": TABLE, "action": {"type": "object", "description": "{action: set|update|append|delete, sheet, ...}"}}, "required": ["table", "action"]},
         json_push_to_queue, ANNOTATIONS_MODIFY),
        ("json_execute_queue", "Таблицы: очередь → применить", "Применить очередь к read.json (RMW). Синк в .xlsx отложен.",
         {"type": "object", "properties": {"table": TABLE}, "required": ["table"]},
         json_execute_queue, ANNOTATIONS_MODIFY),
        ("json_clear_queue", "Таблицы: очередь → очистить", "Очистить очередь без применения (отладка/сброс).",
         {"type": "object", "properties": {"table": TABLE}, "required": ["table"]},
         json_clear_queue, ANNOTATIONS_MODIFY),
        ("get_unique_values", "Таблицы: уникальные значения", "Уникальные значения столбца.",
         {"type": "object", "properties": {"table": TABLE, "sheet": SHEET, "column": {"type": "string", "description": "Имя столбца"}, "limit": {"type": "integer", "description": "Максимум значений", "default": 100}}, "required": ["table", "sheet", "column"]},
         get_unique_values, ANNOTATIONS_READONLY),
        ("get_value_counts", "Таблицы: частотный анализ", "Top-N наиболее частых значений столбца.",
         {"type": "object", "properties": {"table": TABLE, "sheet": SHEET, "column": {"type": "string", "description": "Имя столбца"}, "limit": {"type": "integer", "description": "Top-N", "default": 10}}, "required": ["table", "sheet", "column"]},
         get_value_counts, ANNOTATIONS_READONLY),
        ("find_duplicates", "Таблицы: дубликаты", "Поиск дубликатов по столбцам (или всем).",
         {"type": "object", "properties": {"table": TABLE, "sheet": SHEET, "columns": {"type": "array", "items": {"type": "string"}, "description": "Столбцы для проверки (все если пусто)"}}, "required": ["table", "sheet"]},
         find_duplicates, ANNOTATIONS_READONLY),
        ("find_nulls", "Таблицы: пустые значения", "Поиск пустых/пропущенных значений по всем столбцам.",
         {"type": "object", "properties": {"table": TABLE, "sheet": SHEET}, "required": ["table", "sheet"]},
         find_nulls, ANNOTATIONS_READONLY),
    ]
    for name, title, desc, schema, handler, annot in tables_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="tables", annotations=annot)  # type: ignore[arg-type]
