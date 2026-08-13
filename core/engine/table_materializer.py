"""
core/engine/table_materializer.py — материализация книг .xlsx по декларации (A1′, фаза ТАБЛИЦЫ).

## Зачем
`structure_create` для файла с `kind: table` только ОТКЛАДЫВАЕТ книгу: присваивает `file_id`
и кладёт запись в `tables_pending` (`template_engine.py:155`), сам `.xlsx` не создаёт.
Этот модуль достраивает фазу: `table_template` → `config/templates/tables/<book>.schema.yaml`
→ форма книги через `core/excel` (листы, столбцы, формулы, дропдауны enum).

## Декларативность
Что материализуется — целиком в `.schema.yaml` (формат: `docs/roadmap/spec/TABLE_SCHEMA_FORMAT.md`).
Здесь нет ни имён книг, ни имён листов/столбцов: добавить книгу = добавить YAML, не код.

## Флаги колонок
`id` (ключ) · `W` (writable) · `F` (вычисляемая: `formula:` если спека её задала, иначе
плейсхолдер-заголовок) · `fk` (внешний ключ). Тип `enum` несёт `enum: [...]` → `set_validation`.
"""

from pathlib import Path

import yaml

from core.excel import ExcelEngine, ExcelError


class TableMaterializerError(Exception):
    """Ошибка материализации в формате контракта (код из server_reactions.yaml)."""

    def __init__(self, code: str, message: str, reason: str = "", suggested_tool: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool


class TableMaterializer:
    """Материализатор книг таблиц по `*.schema.yaml` поверх ExcelEngine."""

    def __init__(self, excel: ExcelEngine, schemas_dir: str | Path):
        self.excel = excel
        self.schemas_dir = Path(schemas_dir)
        self._defaults: dict | None = None

    # ═══ Деградация формул (F30) — правила ЧИТАЮТСЯ, не зашиты ═══

    def _degrade_rules(self, schema: dict) -> dict:
        """Запасные значения по типу столбца: книга перекрывает общий `_defaults.yaml`."""
        if self._defaults is None:
            path = self.schemas_dir / "_defaults.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
            self._defaults = (data or {}).get("degrade") or {}
        return {**self._defaults, **(schema.get("degrade") or {})}

    @staticmethod
    def _excel_literal(value) -> str:
        """Запасное значение в синтаксис Excel: число — как есть, строка — в кавычках."""
        if isinstance(value, bool):
            return "TRUE()" if value else "FALSE()"
        if isinstance(value, (int, float)):
            return str(value)
        return '"' + str(value).replace('"', '""') + '"'

    def _guarded(self, formula: str, col: dict, rules: dict) -> str:
        """Формула, которая не ломается на неполных данных (F30).

        Оборачиваем в IFERROR: любая ошибка вычисления (#DIV/0!, #REF!, #VALUE!, #NAME?)
        превращается в объявленное запасное значение. Что подставить — решает декларация,
        а не код: `on_empty` столбца, иначе правило по типу.
        """
        fallback = col["on_empty"] if "on_empty" in col else rules.get(col.get("type", "string"))
        if fallback is None:
            return formula
        body = formula[1:] if formula.startswith("=") else formula
        return f"=IFERROR({body},{self._excel_literal(fallback)})"

    def load_schema(self, book: str) -> dict:
        """Прочитать декларацию книги. Схема-источник истины, не код."""
        path = self.schemas_dir / f"{book}.schema.yaml"
        if not path.exists():
            raise TableMaterializerError(
                "TEMPLATE_NOT_FOUND", f"Схема книги не найдена: {book}",
                reason=f"Заведи {path.name} в config/templates/tables/ (формат — spec/TABLE_SCHEMA_FORMAT.md).")
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        sheets = schema.get("sheets") or []
        if not sheets:
            raise TableMaterializerError(
                "SCHEMA_INVALID", f"В схеме '{book}' нет ни одного листа (sheets).",
                reason="Опиши хотя бы один лист с колонками; книга без листов не материализуется.")
        for sheet in sheets:
            if not sheet.get("name"):
                raise TableMaterializerError(
                    "SCHEMA_INVALID", f"В схеме '{book}' есть лист без имени.",
                    reason="У каждого листа обязательно поле name.")
        return schema

    def materialize(self, book: str, path: str) -> dict:
        """Создать книгу по декларации: листы → столбцы → формулы → валидации.

        Идемпотентности НЕТ намеренно: существующая книга — это данные владельца,
        молча перезаписывать её нельзя (ExcelEngine отдаёт FILE_EXISTS).
        """
        schema = self.load_schema(book)
        sheets = schema["sheets"]
        rules = self._degrade_rules(schema)
        guarded = 0
        try:
            self.excel.create_workbook(path, sheet=sheets[0]["name"])
            for sheet in sheets[1:]:
                self.excel.add_sheet(path, sheet["name"])

            created = []
            for sheet in sheets:
                name = sheet["name"]
                columns = sheet.get("columns") or []
                for col in columns:
                    col_name = col.get("name")
                    if not col_name:
                        raise TableMaterializerError(
                            "SCHEMA_INVALID", f"Лист '{name}' книги '{book}': колонка без имени.",
                            reason="У каждой колонки обязательно поле name.")
                    # F = вычисляемая: формула из спеки, иначе только заголовок-плейсхолдер.
                    formula = col.get("formula") if col.get("flag") == "F" else None
                    if formula:
                        formula = self._guarded(formula, col, rules)
                        guarded += 1
                    self.excel.add_column(path, name, col_name, formula=formula)
                    if col.get("type") == "enum" and col.get("enum"):
                        self.excel.set_validation(path, name, col_name, allowed=col["enum"])
                # Строки-дефолты: лист может нести не только форму, но и стартовые значения
                # (конфиг канала, переведённый в листы). Список в ячейке — через запятую.
                rows = sheet.get("rows") or []
                list_cols = {c["name"] for c in columns if c.get("type") == "list"}
                for row in rows:
                    self.excel.append_row(path, name, {
                        k: (",".join(str(x) for x in v) if k in list_cols and isinstance(v, list) else v)
                        for k, v in row.items()})
                created.append({"sheet": name, "columns": [c["name"] for c in columns],
                                "rows": len(rows)})
        except ExcelError as e:
            # Ошибка формы книги — код движка Excel уже в реестре реакций, не переобёртываем.
            raise TableMaterializerError(e.code, e.message, e.reason, e.suggested_tool) from e

        return {
            "book": book, "path": path, "level": schema.get("level", ""),
            "sheets": created,
            "columns_total": sum(len(s["columns"]) for s in created),
            "rows_total": sum(s["rows"] for s in created),
            "formulas_guarded": guarded,
        }

    def materialize_pending(self, pending: list[dict]) -> dict:
        """Фаза ТАБЛИЦЫ: пройти `tables_pending` от structure_create.

        Одна книга не роняет остальные: отказы собираются в `failed` с кодом реакции —
        неполные входные данные не должны останавливать материализацию соседних книг.
        """
        materialized, failed = [], []
        for item in pending:
            book = item.get("table_template")
            path = item.get("path")
            if not book or not path:
                failed.append({"path": path, "book": book, "code": "SCHEMA_INVALID",
                               "message": "В записи tables_pending нет table_template или path."})
                continue
            try:
                result = self.materialize(book, path)
                result["file_id"] = item.get("file_id", "")
                materialized.append(result)
            except TableMaterializerError as e:
                failed.append({"path": path, "book": book, "code": e.code, "message": e.message})
        return {"materialized": materialized, "failed": failed,
                "total": len(pending), "created": len(materialized)}
