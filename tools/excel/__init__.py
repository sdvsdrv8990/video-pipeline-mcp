"""
tools/excel — структура книг .xlsx (Категория 2): листы, столбцы, формулы,
форматирование, валидация + инспекция структуры файла.

Тонкие обёртки: вся работа с openpyxl и пересчёт формул (LibreOffice) —
в core/excel/ExcelEngine. Контракт зафиксирован эталоном tools_inventory.golden.json.
"""

from core.contracts import Fact, ToolResult
from core.engine import Engine
from tools._context import ANNOTATIONS_MODIFY, ANNOTATIONS_READONLY, ToolContext
from tools._schemas import PATH, SHEET


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы excel в движке."""

    # ─── Категория 2: структура (excel_*) ───

    async def excel_create_workbook(path: str, sheet: str = "Sheet1") -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.create_workbook(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="WorkbookCreated", data=res)])

    async def excel_add_sheet(path: str, sheet: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.add_sheet(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetAdded", data={"path": path, "sheet": sheet})])

    async def excel_rename_sheet(path: str, sheet: str, new_name: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.rename_sheet(path, sheet, new_name))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetRenamed", data={"path": path, "from": sheet, "to": new_name})])

    async def excel_delete_sheet(path: str, sheet: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.delete_sheet(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetDeleted", data={"path": path, "sheet": sheet})])

    async def excel_reorder_sheets(path: str, order: list) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.reorder_sheets(path, order))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetsReordered", data={"path": path})])

    async def excel_add_column(path: str, sheet: str, column: str, formula: str = "") -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.add_column(path, sheet, column, formula or None))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnAdded", data={"path": path, "sheet": sheet, "column": column})])

    async def excel_delete_column(path: str, sheet: str, column: str, force: bool = False) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.delete_column(path, sheet, column, force))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnDeleted", data={"path": path, "sheet": sheet, "column": column, "broke_formulas": len(res["broken_formulas"])})])

    async def excel_move_column(path: str, sheet: str, column: str, to_index: int, force: bool = False) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.move_column(path, sheet, column, to_index, force))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnMoved", data={"path": path, "sheet": sheet, "column": column, "to": to_index, "broke_formulas": len(res["broken_formulas"])})])

    async def excel_find_dependents(path: str, sheet: str, column: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.find_dependents(path, sheet, column))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="DependentsFound", data={"path": path, "sheet": sheet, "column": column, "count": res["count"]})])

    async def excel_insert_formula(path: str, sheet: str, cell: str, formula: str, overwrite: bool = False) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.insert_formula(path, sheet, cell, formula, overwrite))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FormulaInserted", data={"path": path, "sheet": sheet, "cell": cell})])

    async def excel_apply_formatting(path: str, sheet: str, target: str, fill: str = "", bold: bool | None = None, font_color: str = "") -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.apply_formatting(path, sheet, target, fill or None, bold, font_color or None))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FormattingApplied", data={"path": path, "sheet": sheet, "target": target})])

    async def excel_set_validation(path: str, sheet: str, column: str, allowed: list) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.set_validation(path, sheet, column, allowed))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ValidationSet", data={"path": path, "sheet": sheet, "column": column})])

    async def excel_read_range(path: str, sheet: str, cell_range: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.read_range(path, sheet, cell_range))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="RangeRead", data={"path": path, "sheet": sheet, "range": cell_range})])

    async def excel_validate_formulas(path: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.validate_formulas(path))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FormulasValidated", data={"path": path, "ok": res["ok"], "errors": len(res["errors"])})])

    # ─── Excel: копирование листа ───

    async def excel_copy_sheet(path: str, sheet: str, new_name: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.copy_sheet(path, sheet, new_name))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetCopied", data={"path": path, "from": sheet, "to": new_name})])

    # ─── Excel: анализ структуры ───

    async def inspect_file(path: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.inspect_file(path))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FileInspected", data={"path": path, "sheets": res["sheet_count"]})])

    async def get_sheet_info(path: str, sheet: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.get_sheet_info(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetInfoRead", data={"path": path, "sheet": sheet, "columns": res["column_count"]})])

    async def get_column_names(path: str, sheet: str) -> "ToolResult":
        ok, res = ctx.safe(lambda: ctx.excel_engine.get_column_names(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnNamesRead", data={"path": path, "sheet": sheet, "count": res["count"]})])

    # ═══ КАТЕГОРИЯ 2: структура таблиц (excel_*) ═══
    excel_tools = [
        ("excel_create_workbook", "Excel: новая книга", "Создать новый .xlsx (не перезаписывает существующий).",
         {"type": "object", "properties": {"path": PATH, "sheet": {"type": "string", "description": "Имя первого листа", "default": "Sheet1"}}, "required": ["path"]},
         excel_create_workbook, ANNOTATIONS_MODIFY),
        ("excel_add_sheet", "Excel: добавить лист", "Добавить лист в книгу.",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET}, "required": ["path", "sheet"]},
         excel_add_sheet, ANNOTATIONS_MODIFY),
        ("excel_rename_sheet", "Excel: переименовать лист", "Переименовать лист.",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET, "new_name": {"type": "string", "description": "Новое имя листа"}}, "required": ["path", "sheet", "new_name"]},
         excel_rename_sheet, ANNOTATIONS_MODIFY),
        ("excel_delete_sheet", "Excel: удалить лист", "Удалить лист (нельзя последний).",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET}, "required": ["path", "sheet"]},
         excel_delete_sheet, ANNOTATIONS_MODIFY),
        ("excel_reorder_sheets", "Excel: порядок листов", "Переупорядочить листы (order = все листы книги).",
         {"type": "object", "properties": {"path": PATH, "order": {"type": "array", "items": {"type": "string"}, "description": "Полный список листов в новом порядке"}}, "required": ["path", "order"]},
         excel_reorder_sheets, ANNOTATIONS_MODIFY),
        ("excel_add_column", "Excel: добавить столбец", "Добавить столбец (заголовок в строку 1). formula — опционально.",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET, "column": {"type": "string", "description": "Имя столбца (заголовок)"}, "formula": {"type": "string", "description": "Формула-образец (опц.)"}}, "required": ["path", "sheet", "column"]},
         excel_add_column, ANNOTATIONS_MODIFY),
        ("excel_delete_column", "Excel: удалить столбец", "Удалить столбец по имени заголовка. Отказывает, если на столбец ссылаются формулы (force=true — осознанно сломать).",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET, "column": {"type": "string", "description": "Имя столбца"}, "force": {"type": "boolean", "description": "Удалить, даже если формулы ссылаются (сломает их)", "default": False}}, "required": ["path", "sheet", "column"]},
         excel_delete_column, ANNOTATIONS_MODIFY),
        ("excel_move_column", "Excel: переместить столбец", "Переместить столбец на позицию to_index (1-based). Отказывает, если на столбец ссылаются формулы (force=true).",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET, "column": {"type": "string", "description": "Имя столбца"}, "to_index": {"type": "integer", "description": "Новая позиция (1-based)"}, "force": {"type": "boolean", "description": "Перенести, даже если формулы ссылаются (сломает их)", "default": False}}, "required": ["path", "sheet", "column", "to_index"]},
         excel_move_column, ANNOTATIONS_MODIFY),
        ("excel_find_dependents", "Excel: кто ссылается на столбец", "Формулы, ссылающиеся на столбец (по всей книге, включая межлистовые диапазоны). Спрашивают ПЕРЕД удалением/переносом.",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET, "column": {"type": "string", "description": "Имя столбца"}}, "required": ["path", "sheet", "column"]},
         excel_find_dependents, ANNOTATIONS_READONLY),
        ("excel_insert_formula", "Excel: вставить формулу", "Формула в ячейку. Не перезаписывает существующую молча (overwrite).",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET, "cell": {"type": "string", "description": "Ячейка (напр. C2)"}, "formula": {"type": "string", "description": "Формула (с '=' или без)"}, "overwrite": {"type": "boolean", "description": "Перезаписать существующую формулу", "default": False}}, "required": ["path", "sheet", "cell", "formula"]},
         excel_insert_formula, ANNOTATIONS_MODIFY),
        ("excel_apply_formatting", "Excel: форматирование", "Стили на ячейку/диапазон (заливка/жирный/цвет шрифта).",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET, "target": {"type": "string", "description": "Ячейка или диапазон (A1 / A1:C3)"}, "fill": {"type": "string", "description": "HEX заливки RRGGBB"}, "bold": {"type": "boolean", "description": "Жирный"}, "font_color": {"type": "string", "description": "HEX цвета шрифта RRGGBB"}}, "required": ["path", "sheet", "target"]},
         excel_apply_formatting, ANNOTATIONS_MODIFY),
        ("excel_set_validation", "Excel: выпадающий список", "Data Validation (dropdown) на столбец — материализует enum из схемы.",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET, "column": {"type": "string", "description": "Имя столбца"}, "allowed": {"type": "array", "items": {"type": "string"}, "description": "Список допустимых значений"}}, "required": ["path", "sheet", "column", "allowed"]},
         excel_set_validation, ANNOTATIONS_MODIFY),
        ("excel_read_range", "Excel: сырой диапазон (отладка)", "ОТЛАДКА: сырой 2D-массив ячеек. Рабочее чтение — json_read_snapshot.",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET, "cell_range": {"type": "string", "description": "Диапазон (напр. A1:C10)"}}, "required": ["path", "sheet", "cell_range"]},
         excel_read_range, ANNOTATIONS_READONLY),
        ("excel_validate_formulas", "Excel: проверить формулы", "Поиск ошибок формул (#REF!/#VALUE!/…) по всем листам.",
         {"type": "object", "properties": {"path": PATH}, "required": ["path"]},
         excel_validate_formulas, ANNOTATIONS_READONLY),
        ("excel_copy_sheet", "Excel: копировать лист", "Копирование листа с данными и форматированием.",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET, "new_name": {"type": "string", "description": "Имя копии листа"}}, "required": ["path", "sheet", "new_name"]},
         excel_copy_sheet, ANNOTATIONS_MODIFY),
        ("inspect_file", "Excel: обзор книги", "Обзор структуры: листы, размеры, формат.",
         {"type": "object", "properties": {"path": PATH}, "required": ["path"]},
         inspect_file, ANNOTATIONS_READONLY),
        ("get_sheet_info", "Excel: анализ листа", "Детальный анализ: колонки, типы, превью данных.",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET}, "required": ["path", "sheet"]},
         get_sheet_info, ANNOTATIONS_READONLY),
        ("get_column_names", "Excel: имена колонок", "Быстрый список колонок листа.",
         {"type": "object", "properties": {"path": PATH, "sheet": SHEET}, "required": ["path", "sheet"]},
         get_column_names, ANNOTATIONS_READONLY),
    ]
    for name, title, desc, schema, handler, annot in excel_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="excel", annotations=annot)  # type: ignore[arg-type]
