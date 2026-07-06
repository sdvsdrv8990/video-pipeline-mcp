"""
core/excel/excel_core.py — Движок СТРУКТУРЫ таблиц (Категория 2, Excel Engine)

## Назначение
Полный CRUD над ФОРМОЙ книги (листы, столбцы, формулы, форматирование,
валидация). Данные в ячейки здесь НЕ пишутся — только структура (несущее
правило: `excel_*` меняет форму, очередь меняет значения; ИНСТРУКЦИЯ §6).

## Границы (ИНСТРУКЦИЯ инструменты §3)
- Меняет ТОЛЬКО структуру. Значения строк — через очередь (Категория 3).
- `read_range` — отладочный путь чтения (сырой 2D), НЕ рабочий (рабочее чтение —
  json_read_snapshot + проекции).
- Защита формул: insert_formula не перезаписывает существующую формулу молча —
  отклоняет (FORMULA_PROTECTED).

## Конвенция листа
Заголовки столбцов — строка 1. Данные — со строки 2. Столбец адресуется ПО ИМЕНИ
заголовка (не по букве), чтобы Claude не считал буквы колонок.

## Containment
Все пути — через core.paths.safe_resolve внутри workspace/ (G17/D29).
"""

from __future__ import annotations

from pathlib import Path

from core.paths import safe_resolve

# openpyxl импортируем лениво внутри методов — чтобы импорт модуля не падал,
# если библиотека не установлена (движок данных Категории 3 от неё не зависит).


class ExcelError(Exception):
    """Ошибка движка структуры. Код из server_reactions.yaml + подсказка."""

    def __init__(self, code: str, message: str, reason: str = "", suggested_tool: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool


class ExcelEngine:
    """Движок структуры Excel-книг. Один на категорию.

    Attributes:
        workspace: корень workspace/ для containment.
    """

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace)

    # ═══ Служебное ═══

    def _resolve(self, path: str) -> Path:
        """safe-join внутри workspace. ValueError → обёртка маппит в PATH_ESCAPE."""
        return safe_resolve(path, self.workspace)

    def _load(self, path: str, must_exist: bool = True):
        """Открыть книгу. WORKBOOK_NOT_FOUND если нет (и must_exist)."""
        import openpyxl
        p = self._resolve(path)
        if not p.exists():
            if must_exist:
                raise ExcelError(
                    "WORKBOOK_NOT_FOUND", f"Книга не найдена: {path}",
                    reason="Создай книгу через excel_create_workbook или проверь путь.",
                    suggested_tool="excel_create_workbook",
                )
            return None
        return openpyxl.load_workbook(p)

    def _sheet(self, wb, sheet: str):
        if sheet not in wb.sheetnames:
            raise ExcelError(
                "SHEET_NOT_FOUND", f"Лист '{sheet}' не найден. Есть: {', '.join(wb.sheetnames)}",
                reason="Проверь имя листа или создай через excel_add_sheet.",
            )
        return wb[sheet]

    @staticmethod
    def _headers(ws) -> dict[str, int]:
        """{имя_заголовка: индекс_столбца(1-based)} из строки 1."""
        headers: dict[str, int] = {}
        for idx, cell in enumerate(ws[1], start=1):
            if cell.value is not None:
                headers[str(cell.value)] = idx
        return headers

    def _save(self, wb, path: str):
        wb.save(self._resolve(path))

    # ═══ КНИГА / ЛИСТЫ ═══

    def create_workbook(self, path: str, sheet: str = "Sheet1") -> dict:
        """Новый .xlsx. FILE_EXISTS если файл уже есть (не перезаписываем молча)."""
        import openpyxl
        p = self._resolve(path)
        if p.exists():
            raise ExcelError("FILE_EXISTS", f"Файл или каталог уже существует: {path}",
                             reason="Удали существующий файл или используй другое имя.")
        p.parent.mkdir(parents=True, exist_ok=True)
        wb = openpyxl.Workbook()
        wb.active.title = sheet
        wb.save(p)
        return {"path": path, "sheet": sheet}

    def add_sheet(self, path: str, sheet: str) -> dict:
        wb = self._load(path)
        if sheet in wb.sheetnames:
            raise ExcelError("SHEET_EXISTS", f"Лист уже существует: {sheet}",
                             reason="Используй другое имя или excel_rename_sheet.")
        wb.create_sheet(title=sheet)
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "sheets": wb.sheetnames}

    def rename_sheet(self, path: str, sheet: str, new_name: str) -> dict:
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        if new_name in wb.sheetnames:
            raise ExcelError("SHEET_EXISTS", f"Лист уже существует: {new_name}",
                             reason="Выбери свободное имя.")
        ws.title = new_name
        self._save(wb, path)
        return {"path": path, "renamed": {sheet: new_name}, "sheets": wb.sheetnames}

    def delete_sheet(self, path: str, sheet: str) -> dict:
        wb = self._load(path)
        self._sheet(wb, sheet)
        if len(wb.sheetnames) == 1:
            raise ExcelError("LAST_SHEET", "Нельзя удалить последний лист книги.",
                             reason="В книге всегда должен быть хотя бы один лист.")
        del wb[sheet]
        self._save(wb, path)
        return {"path": path, "deleted": sheet, "sheets": wb.sheetnames}

    def reorder_sheets(self, path: str, order: list[str]) -> dict:
        wb = self._load(path)
        if set(order) != set(wb.sheetnames):
            raise ExcelError("VALIDATION_ERROR", "order должен содержать РОВНО все листы книги.",
                             reason=f"Ожидались листы: {wb.sheetnames}.")
        wb._sheets.sort(key=lambda ws: order.index(ws.title))
        self._save(wb, path)
        return {"path": path, "sheets": wb.sheetnames}

    def copy_sheet(self, path: str, sheet: str, new_name: str) -> dict:
        """Копирование листа с данными и форматированием."""
        wb = self._load(path)
        self._sheet(wb, sheet)
        if new_name in wb.sheetnames:
            raise ExcelError("SHEET_EXISTS", f"Лист уже существует: {new_name}",
                             reason="Выбери свободное имя.")
        source = wb[sheet]
        copy = wb.copy_worksheet(source)
        copy.title = new_name
        self._save(wb, path)
        return {"path": path, "copied": sheet, "to": new_name, "sheets": wb.sheetnames}

    # ═══ АНАЛИЗ СТРУКТУРЫ ═══

    def inspect_file(self, path: str) -> dict:
        """Обзор структуры книги: листы, размеры, формат."""
        wb = self._load(path)
        sheets = []
        for name in wb.sheetnames:
            ws = wb[name]
            sheets.append({
                "name": name,
                "rows": ws.max_row,
                "columns": ws.max_column,
            })
        return {
            "path": path,
            "format": self._resolve(path).suffix,
            "sheet_count": len(wb.sheetnames),
            "sheets": sheets,
        }

    def get_sheet_info(self, path: str, sheet: str) -> dict:
        """Детальный анализ листа: колонки, типы, превью."""
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        headers = self._headers(ws)
        columns = []
        for name, idx in headers.items():
            col_type = "string"
            sample_values = []
            for row in range(2, min(ws.max_row + 1, 7)):
                val = ws.cell(row=row, column=idx).value
                if val is not None:
                    sample_values.append(val)
                    if isinstance(val, (int, float)):
                        col_type = "number"
                    elif isinstance(val, bool):
                        col_type = "bool"
            columns.append({
                "name": name,
                "index": idx,
                "type": col_type,
                "sample": sample_values[:3],
            })
        return {
            "path": path,
            "sheet": sheet,
            "row_count": ws.max_row - 1,
            "column_count": len(headers),
            "columns": columns,
        }

    def get_column_names(self, path: str, sheet: str) -> dict:
        """Быстрый список колонок листа."""
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        headers = self._headers(ws)
        return {
            "path": path,
            "sheet": sheet,
            "columns": list(headers.keys()),
            "count": len(headers),
        }

    # ═══ СТОЛБЦЫ ═══

    def add_column(self, path: str, sheet: str, column: str, formula: str | None = None) -> dict:
        """Новый столбец = заголовок в строку 1 (следующий свободный)."""
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        headers = self._headers(ws)
        if column in headers:
            raise ExcelError("COLUMN_EXISTS", f"Столбец уже существует: {column}",
                             reason="Используй другое имя или excel_delete_column.")
        new_idx = (max(headers.values()) + 1) if headers else 1
        ws.cell(row=1, column=new_idx, value=column)
        if formula:
            # формула-образец в строку 2 (шаблон вычисляемого столбца)
            ws.cell(row=2, column=new_idx, value=formula if formula.startswith("=") else f"={formula}")
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "column": column, "index": new_idx}

    def delete_column(self, path: str, sheet: str, column: str) -> dict:
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        headers = self._headers(ws)
        if column not in headers:
            raise ExcelError("COLUMN_NOT_FOUND", f"Столбец '{column}' не найден в листе '{sheet}'.",
                             reason="Сверь имя заголовка (excel_read_range) или уже удалён.")
        ws.delete_cols(headers[column], 1)
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "deleted": column}

    def move_column(self, path: str, sheet: str, column: str, to_index: int) -> dict:
        """Переместить столбец на позицию to_index (1-based) сдвигом ячеек."""
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        headers = self._headers(ws)
        if column not in headers:
            raise ExcelError("COLUMN_NOT_FOUND", f"Столбец '{column}' не найден в листе '{sheet}'.",
                             reason="Сверь имя заголовка.")
        from_index = headers[column]
        ncols = max(headers.values())
        if not (1 <= to_index <= ncols):
            raise ExcelError("VALIDATION_ERROR", f"to_index вне диапазона 1..{ncols}.",
                             reason="Укажи позицию внутри существующих столбцов.")
        if to_index == from_index:
            return {"path": path, "sheet": sheet, "column": column, "index": to_index}
        # снять значения столбца, удалить, вставить на новое место
        col_values = [ws.cell(row=r, column=from_index).value for r in range(1, ws.max_row + 1)]
        ws.delete_cols(from_index, 1)
        ws.insert_cols(to_index, 1)
        for r, val in enumerate(col_values, start=1):
            ws.cell(row=r, column=to_index, value=val)
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "column": column, "index": to_index}

    # ═══ ФОРМУЛЫ / ФОРМАТ / ВАЛИДАЦИЯ ═══

    def insert_formula(self, path: str, sheet: str, cell: str, formula: str, overwrite: bool = False) -> dict:
        """Формула в ячейку. Защита: не перезаписывает существующую формулу молча."""
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        target = ws[cell]
        existing = target.value
        if isinstance(existing, str) and existing.startswith("=") and not overwrite:
            raise ExcelError(
                "FORMULA_PROTECTED", f"В ячейке {cell} уже есть формула: {existing}",
                reason="Перезапись критической формулы запрещена молча. Передай overwrite=true осознанно.",
            )
        target.value = formula if formula.startswith("=") else f"={formula}"
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "cell": cell, "formula": target.value}

    def apply_formatting(self, path: str, sheet: str, target: str,
                         fill: str | None = None, bold: bool | None = None,
                         font_color: str | None = None) -> dict:
        """Стили на ячейку/диапазон (A1 или A1:C3). fill/font_color — HEX 'RRGGBB'."""
        from openpyxl.styles import PatternFill, Font
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        cells = ws[target]
        # нормализуем в плоский список ячеек (одна ячейка → кортеж кортежей у диапазона)
        flat: list = []
        if hasattr(cells, "__iter__"):
            for row in cells:
                flat.extend(row if hasattr(row, "__iter__") else [row])
        else:
            flat = [cells]
        for c in flat:
            if fill:
                c.fill = PatternFill(start_color=fill, end_color=fill, fill_type="solid")
            if bold is not None or font_color:
                c.font = Font(bold=bool(bold), color=font_color or None)
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "target": target, "cells": len(flat)}

    def set_validation(self, path: str, sheet: str, column: str, allowed: list[str]) -> dict:
        """Выпадающий список (Data Validation) на весь столбец — материализует enum."""
        from openpyxl.worksheet.datavalidation import DataValidation
        from openpyxl.utils import get_column_letter
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        headers = self._headers(ws)
        if column not in headers:
            raise ExcelError("COLUMN_NOT_FOUND", f"Столбец '{column}' не найден в листе '{sheet}'.",
                             reason="Сначала добавь столбец через excel_add_column.")
        letter = get_column_letter(headers[column])
        formula = '"' + ",".join(allowed) + '"'
        dv = DataValidation(type="list", formula1=formula, allow_blank=True, showDropDown=False)
        dv.add(f"{letter}2:{letter}1048576")  # со строки 2 (без заголовка)
        ws.add_data_validation(dv)
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "column": column, "allowed": allowed}

    def read_range(self, path: str, sheet: str, cell_range: str) -> dict:
        """ОТЛАДКА: сырой 2D-массив. НЕ рабочий путь чтения (см. json_read_snapshot)."""
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        matrix = [[c.value for c in row] for row in ws[cell_range]] if cell_range else []
        return {"path": path, "sheet": sheet, "range": cell_range, "values": matrix,
                "note": "Отладочное чтение сырых ячеек. Рабочее чтение данных — json_read_snapshot."}

    def validate_formulas(self, path: str) -> dict:
        """Поиск ошибок формул (#REF!/#VALUE!/#DIV/0! и пр.) по всем листам."""
        wb = self._load(path)
        errors = []
        tokens = ("#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#N/A", "#NULL!", "#NUM!")
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and any(t in c.value for t in tokens):
                        errors.append({"sheet": ws.title, "cell": c.coordinate, "value": c.value})
        return {"path": path, "errors": errors, "ok": len(errors) == 0}
