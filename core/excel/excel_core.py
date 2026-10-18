"""core/excel/excel_core.py — слой ФОРМЫ книги: листы, столбцы, формулы, оформление, валидация.

Значения ячеек здесь не пишутся — их меняет очередь слоя данных. Столбец адресуется по ИМЕНИ
заголовка, а не по букве: буква переезжает при вставке соседа, имя нет. Существующая формула не
перезаписывается молча — из значения её потом не восстановить. Сырое чтение диапазона оставлено для
отладки, рабочий путь идёт снимком и проекциями.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.paths import Files
from core.contracts import ContractError
from core.declaration import Declaration, declarations_root
from core.declaration_schemas import RECALC_ENGINE
from core.engines import locate
from core.environment import ChildSaid, child_spawn

# Ссылка в формуле: необязательный лист (`META!` / `'Мой лист'!`), затем ячейка или
# диапазон (`B2`, `$B$2`, `A2:D9`). Границы обязательны: без них `LOG10(` в `=LOG10(x)`
# прочиталось бы как ячейка LOG10 и дало ложную зависимость.
CELL_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_$!])"
    r"(?:(?:'(?P<quoted>[^']+)'|(?P<plain>[A-Za-z_][A-Za-z0-9_.]*))!)?"
    r"\$?(?P<c1>[A-Z]{1,3})\$?(?P<r1>\d+)"
    r"(?::\$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>\d+))?"
    r"(?![A-Za-z0-9_(])"
)

# openpyxl импортируем лениво внутри методов — чтобы импорт модуля не падал,
# если библиотека не установлена (движок данных Категории 3 от неё не зависит).


class ExcelError(ContractError):
    """Ошибка движка структуры. Код из server_reactions.yaml + подсказка."""


class ExcelEngine:
    """Движок структуры Excel-книг. Один на категорию.

    Attributes:
        workspace: корень workspace/ для containment.
    """

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace)

    # ═══ Служебное ═══

    def _resolve(self, path: str) -> Path:
        """Дверь рабочей области: отказ уходит клиенту своим кодом и фактом `path`."""
        return Files(self.workspace).place(path)

    def _load(self, path: str, must_exist: bool = True):
        """Открыть книгу; нет её — WORKBOOK_NOT_FOUND (`_book`), а без `must_exist` — None."""
        import openpyxl
        if not must_exist and not self._resolve(path).exists():
            return None
        return openpyxl.load_workbook(self._book(path))

    def _book(self, path: str) -> Path:
        """Путь книги, которая есть; нет — WORKBOOK_NOT_FOUND с путём клиента."""
        p = self._resolve(path)
        if not p.exists():
            raise ExcelError("WORKBOOK_NOT_FOUND", facts={"path": path})
        return p

    def _sheet(self, wb, sheet: str):
        if sheet not in wb.sheetnames:
            raise ExcelError("SHEET_NOT_FOUND", facts={"sheet": sheet, "known": ", ".join(wb.sheetnames)})
        return wb[sheet]

    @staticmethod
    def _vacant(wb, name: str) -> None:
        """Имя листа свободно — иначе SHEET_EXISTS с именем."""
        if name in wb.sheetnames:
            raise ExcelError("SHEET_EXISTS", facts={"sheet": name})

    @staticmethod
    def _headers(ws) -> dict[str, int]:
        """{имя_заголовка: индекс_столбца(1-based)} из строки 1."""
        headers: dict[str, int] = {}
        for idx, cell in enumerate(ws[1], start=1):
            if cell.value is not None:
                headers[str(cell.value)] = idx
        return headers

    @staticmethod
    def _column(headers: dict[str, int], sheet: str, *columns: str) -> None:
        """Столбцы есть в заголовках листа — иначе COLUMN_NOT_FOUND с именами и листом."""
        unknown = [c for c in columns if c not in headers]
        if unknown:
            raise ExcelError("COLUMN_NOT_FOUND", facts={"column": ", ".join(unknown), "sheet": sheet})

    def _save(self, wb, path: str):
        wb.save(self._resolve(path))

    # ═══ КНИГА / ЛИСТЫ ═══

    def create_workbook(self, path: str, sheet: str = "Sheet1") -> dict:
        """Новый .xlsx. FILE_EXISTS если файл уже есть (не перезаписываем молча)."""
        import openpyxl
        p = self._resolve(path)
        if p.exists():
            raise ExcelError("FILE_EXISTS", facts={"path": path})
        wb = openpyxl.Workbook()
        self._title(wb.active, sheet)
        p.parent.mkdir(parents=True, exist_ok=True)
        wb.save(p)
        return {"path": path, "sheet": sheet}

    def add_sheet(self, path: str, sheet: str) -> dict:
        wb = self._load(path)
        self._vacant(wb, sheet)
        self._title(wb.create_sheet(), sheet)
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "sheets": wb.sheetnames}

    def rename_sheet(self, path: str, sheet: str, new_name: str) -> dict:
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        self._vacant(wb, new_name)
        self._title(ws, new_name)
        self._save(wb, path)
        return {"path": path, "renamed": {sheet: new_name}, "sheets": wb.sheetnames}

    def delete_sheet(self, path: str, sheet: str) -> dict:
        wb = self._load(path)
        self._sheet(wb, sheet)
        if len(wb.sheetnames) == 1:
            raise ExcelError("LAST_SHEET", facts={"sheet": sheet})
        del wb[sheet]
        self._save(wb, path)
        return {"path": path, "deleted": sheet, "sheets": wb.sheetnames}

    def reorder_sheets(self, path: str, order: list[str]) -> dict:
        wb = self._load(path)
        if set(order) != set(wb.sheetnames):
            raise ExcelError("VALIDATION_ERROR", errors=[{"field": "order", "rule": "all_sheets", "expected": wb.sheetnames}])
        wb._sheets.sort(key=lambda ws: order.index(ws.title))
        self._save(wb, path)
        return {"path": path, "sheets": wb.sheetnames}

    def copy_sheet(self, path: str, sheet: str, new_name: str) -> dict:
        """Копирование листа с данными и форматированием."""
        wb = self._load(path)
        self._sheet(wb, sheet)
        self._vacant(wb, new_name)
        self._title(wb.copy_worksheet(wb[sheet]), new_name)
        self._save(wb, path)
        return {"path": path, "copied": sheet, "to": new_name, "sheets": wb.sheetnames}

    @staticmethod
    def _title(ws, name: str):
        """Имя листа судит книга; её отказ — SHEET_NAME_INVALID без присланного имени (причина — в цепочке)."""
        try:
            ws.title = name
        except ValueError as e:
            raise ExcelError("SHEET_NAME_INVALID") from e
        return ws

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
            raise ExcelError("COLUMN_EXISTS", facts={"column": column, "sheet": sheet})
        new_idx = (max(headers.values()) + 1) if headers else 1
        ws.cell(row=1, column=new_idx, value=column)
        if formula:
            # формула-образец в строку 2 (шаблон вычисляемого столбца)
            ws.cell(row=2, column=new_idx, value=formula if formula.startswith("=") else f"={formula}")
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "column": column, "index": new_idx}

    def append_row(self, path: str, sheet: str, values: dict) -> dict:
        """Строка данных в конец листа: {заголовок: значение}.

        Заголовок обязан существовать — молча создавать столбец под неизвестный ключ нельзя,
        иначе опечатка в декларации тихо расширяет книгу.
        """
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        headers = self._headers(ws)
        self._column(headers, sheet, *values)
        # Пишем после последней непустой строки: у листа с формулой-образцом занята строка 2.
        last = max((c.row for row in ws.iter_rows() for c in row if c.value is not None), default=1)
        for name, value in values.items():
            ws.cell(row=last + 1, column=headers[name], value=value)
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "row": last + 1, "written": len(values)}

    def find_dependents(self, path: str, sheet: str, column: str) -> dict:
        """Формулы, ссылающиеся на столбец. Читающая операция — ничего не меняет.

        Ищем по ВСЕЙ книге: ссылка бывает межлистовой (`META!B2`). Диапазон `A2:D9` считается
        ссылкой на каждый столбец внутри него — удаление любого из них ломает диапазон.
        """
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        headers = self._headers(ws)
        self._column(headers, sheet, column)
        deps = self._dependents(wb, sheet, headers[column])
        return {"path": path, "sheet": sheet, "column": column,
                "dependents": deps, "count": len(deps)}

    @staticmethod
    def _dependents(wb, sheet: str, col_idx: int) -> list[dict]:
        """Ячейки-формулы, чьи ссылки накрывают столбец col_idx листа sheet."""
        from openpyxl.utils import column_index_from_string
        found = []
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    value = cell.value
                    if not isinstance(value, str) or not value.startswith("="):
                        continue
                    for m in CELL_REF_RE.finditer(value):
                        # Лист ссылки: явный префикс либо лист самой формулы.
                        ref_sheet = m.group("quoted") or m.group("plain") or ws.title
                        if ref_sheet != sheet:
                            continue
                        try:
                            first = column_index_from_string(m.group("c1"))
                            last = column_index_from_string(m.group("c2") or m.group("c1"))
                        except ValueError:
                            continue                       # не ссылка (напр. имя функции)
                        if min(first, last) <= col_idx <= max(first, last):
                            found.append({"sheet": ws.title, "cell": cell.coordinate,
                                          "formula": value, "ref": m.group(0)})
                            break                          # одна ячейка — одна запись
        return found

    def _guard_column(self, wb, sheet: str, column: str, col_idx: int, force: bool) -> list[dict]:
        """Пред-проверка деструктива над столбцом: молчаливый коррапт формул запрещён."""
        deps = self._dependents(wb, sheet, col_idx)
        if deps and not force:
            raise ExcelError("COLUMN_HAS_DEPENDENTS", facts={
                "column": column, "count": len(deps),
                "cells": ", ".join(f"{d['sheet']}!{d['cell']}" for d in deps[:5]) + (" …" if len(deps) > 5 else "")})
        return deps

    def delete_column(self, path: str, sheet: str, column: str, force: bool = False) -> dict:
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        headers = self._headers(ws)
        self._column(headers, sheet, column)
        broken = self._guard_column(wb, sheet, column, headers[column], force)
        ws.delete_cols(headers[column], 1)
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "deleted": column,
                "broken_formulas": broken}          # непусто только при force=true

    def move_column(self, path: str, sheet: str, column: str, to_index: int, force: bool = False) -> dict:
        """Переместить столбец на позицию to_index (1-based) сдвигом ячеек."""
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        headers = self._headers(ws)
        self._column(headers, sheet, column)
        from_index = headers[column]
        ncols = max(headers.values())
        if not (1 <= to_index <= ncols):
            raise ExcelError("VALIDATION_ERROR", errors=[{"field": "to_index", "rule": "range", "expected": f"1..{ncols}"}])
        if to_index == from_index:
            return {"path": path, "sheet": sheet, "column": column, "index": to_index,
                    "broken_formulas": []}
        # Перенос меняет БУКВУ столбца: формулы продолжат указывать на старую позицию.
        broken = self._guard_column(wb, sheet, column, from_index, force)
        # снять значения столбца, удалить, вставить на новое место
        col_values = [ws.cell(row=r, column=from_index).value for r in range(1, ws.max_row + 1)]
        ws.delete_cols(from_index, 1)
        ws.insert_cols(to_index, 1)
        for r, val in enumerate(col_values, start=1):
            ws.cell(row=r, column=to_index, value=val)
        self._save(wb, path)
        return {"path": path, "sheet": sheet, "column": column, "index": to_index,
                "broken_formulas": broken}

    # ═══ ФОРМУЛЫ / ФОРМАТ / ВАЛИДАЦИЯ ═══

    def insert_formula(self, path: str, sheet: str, cell: str, formula: str, overwrite: bool = False) -> dict:
        """Формула в ячейку. Защита: не перезаписывает существующую формулу молча."""
        wb = self._load(path)
        ws = self._sheet(wb, sheet)
        target = ws[cell]
        existing = target.value
        if isinstance(existing, str) and existing.startswith("=") and not overwrite:
            raise ExcelError("FORMULA_PROTECTED", facts={"cell": cell, "sheet": sheet})
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
        self._column(headers, sheet, column)
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

    _ERROR_TOKENS = ("#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#N/A", "#NULL!", "#NUM!")

    def _recalc_via_lo(self, src: Path) -> Path:
        """Пересчитать формулы реальным движком LibreOffice headless → путь к вычисленной копии.

        openpyxl формулы не считает; LO recalc'ает при загрузке и сохраняет значения.
        Профиль уникален на вызов (без конфликта параллельных soffice). Отказ — кодом причины: движка нет
        (RECALC_UNAVAILABLE), срок (RECALC_TIMEOUT), движок не вернул книгу (RECALC_FAILED); тексты — реестр.
        """
        import shutil
        import subprocess
        import tempfile
        engines = Declaration(declarations_root() / RECALC_ENGINE.file, ExcelError, "движков сервера")
        soffice = locate(engines.section(*RECALC_ENGINE.section, schema=RECALC_ENGINE.schema))
        if not soffice:
            raise ExcelError("RECALC_UNAVAILABLE")
        td = Path(tempfile.mkdtemp(prefix="xlsx_recalc_"))
        try:
            done = subprocess.run(
                # URL профиля — as_uri: на Windows `file://C:\…` LibreOffice не разбирает (нужно file:///C:/…).
                [soffice, "--headless", "--calc", f"-env:UserInstallation={(td / 'profile').as_uri()}",
                 "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(td), str(src)],
                check=True, capture_output=True, timeout=120, **child_spawn("replace"),
            )
        except subprocess.TimeoutExpired as e:
            shutil.rmtree(td, ignore_errors=True)
            raise ExcelError("RECALC_TIMEOUT", facts={"timeout_sec": e.timeout}) from e
        except subprocess.CalledProcessError as e:
            shutil.rmtree(td, ignore_errors=True)
            # Вывод — уже текст (child_spawn "replace"); слова LibreOffice несут пути хоста — причиной в журнал.
            raise ExcelError("RECALC_FAILED", facts={"exit_code": e.returncode}) from ChildSaid(e.stderr or "")
        out = td / src.name
        if not out.exists():
            shutil.rmtree(td, ignore_errors=True)
            raise ExcelError("RECALC_FAILED", facts={"exit_code": done.returncode}) from ChildSaid(done.stderr or "")
        return out

    def validate_formulas(self, path: str) -> dict:
        """Валидация формул РЕАЛЬНЫМ пересчётом (LibreOffice) — ловит #DIV/0!/#REF! и пр.

        Раньше был греп токенов по несчитанной книге (openpyxl) = театр: `=1/0` не ловился.
        """
        import shutil
        import openpyxl
        src = self._book(path)
        recalced = self._recalc_via_lo(src)
        try:
            wb = openpyxl.load_workbook(recalced, data_only=True)
            errors = []
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for c in row:
                        if isinstance(c.value, str) and any(t in c.value for t in self._ERROR_TOKENS):
                            errors.append({"sheet": ws.title, "cell": c.coordinate, "value": c.value})
        finally:
            shutil.rmtree(recalced.parent, ignore_errors=True)
        return {"path": path, "errors": errors, "ok": len(errors) == 0}
