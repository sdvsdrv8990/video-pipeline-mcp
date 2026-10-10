"""core/engine/table_materializer.py — материализация книг .xlsx по декларации.

`structure_create` для `kind: table` заводит только `file_id` и запись в `tables_pending`, саму
книгу строит этот модуль. Имён книг, листов и столбцов здесь нет: добавить книгу значит добавить
YAML, а не код.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from core.excel import ExcelEngine, ExcelError
from core.contracts import ContractError
from core.declaration import Declaration, Declared
from core.declaration_schemas import BookColumn, BookDefaults, BookSchema, BookSheet
from core.engine.template_resolver import server_tables


class TableMaterializerError(ContractError):
    """Ошибка материализации в формате контракта (код из server_reactions.yaml)."""


class TableMaterializer:
    """Материализатор книг таблиц по `*.schema.yaml` поверх ExcelEngine."""

    def __init__(self, excel: ExcelEngine, schemas_dir: str | Path):
        self.excel = excel
        self.schemas_dir = Path(schemas_dir)
        self._defaults: Mapping | None = None

    # ═══ Деградация формул — правила ЧИТАЮТСЯ, не зашиты ═══

    def _degrade_rules(self, schema: BookSchema) -> dict:
        """Запасные значения по типу столбца: книга перекрывает общий `_defaults.yaml`."""
        if self._defaults is None:
            door = Declared(self.schemas_dir / "_defaults.yaml", TableMaterializerError, "общих правил книг",
                            "Поправь _defaults.yaml в config/templates/tables/.", schema=BookDefaults)
            # Нет файла — правил нет (решение читателя); битый — отказ двери, как у книги.
            self._defaults = door.model.degrade if door.config_file.exists() else {}
        return {**self._defaults, **schema.degrade}

    @staticmethod
    def _excel_literal(value) -> str:
        """Запасное значение в синтаксис Excel: число — как есть, строка — в кавычках."""
        if isinstance(value, bool):
            return "TRUE()" if value else "FALSE()"
        if isinstance(value, (int, float)):
            return str(value)
        return '"' + str(value).replace('"', '""') + '"'

    def _guarded(self, formula: str, col: BookColumn, rules: dict) -> str:
        """Формула, которая не ломается на неполных данных.

        Оборачиваем в IFERROR: любая ошибка вычисления (#DIV/0!, #REF!, #VALUE!, #NAME?)
        превращается в объявленное запасное значение. Что подставить — решает декларация,
        а не код: `on_empty` столбца, иначе правило по типу.
        """
        fallback = col.on_empty if col.on_empty is not None else rules.get(col.type)
        if fallback is None:
            return formula
        body = formula[1:] if formula.startswith("=") else formula
        return f"=IFERROR({body},{self._excel_literal(fallback)})"

    def load_schema(self, book: str) -> BookSchema:
        """Декларация книги из каталога схем этого материализатора — через общую дверь книг (`book_schema`)."""
        return book_schema(self.schemas_dir, book)

    def materialize(self, book: str, path: str) -> dict:
        """Создать книгу по декларации: листы → столбцы → формулы → валидации.

        Идемпотентности НЕТ намеренно: существующая книга — это данные владельца,
        молча перезаписывать её нельзя (ExcelEngine отдаёт FILE_EXISTS).
        """
        schema = self.load_schema(book)
        rules = self._degrade_rules(schema)
        guarded = 0
        try:
            self.excel.create_workbook(path, sheet=schema.sheets[0].name)
            for sheet in schema.sheets[1:]:
                self.excel.add_sheet(path, sheet.name)

            created: list[dict] = []
            for sheet in schema.sheets:
                for col in sheet.columns:
                    # F = вычисляемая: формула из спеки, иначе только заголовок-плейсхолдер.
                    formula = col.formula if col.flag == "F" else ""
                    if formula:
                        formula = self._guarded(formula, col, rules)
                        guarded += 1
                    self.excel.add_column(path, sheet.name, col.name, formula=formula or None)
                    if col.type == "enum" and col.enum:
                        self.excel.set_validation(path, sheet.name, col.name, allowed=list(col.enum))
                # Строки-дефолты: лист может нести не только форму, но и стартовые значения
                # (конфиг канала, переведённый в листы). Список в ячейке — через запятую.
                list_cols = {c.name for c in sheet.columns if c.type == "list"}
                for row in sheet.rows:
                    self.excel.append_row(path, sheet.name, {
                        k: (",".join(str(x) for x in v) if k in list_cols and isinstance(v, tuple) else v)
                        for k, v in row.items()})
                created.append({"sheet": sheet.name, "columns": [c.name for c in sheet.columns],
                                "rows": len(sheet.rows)})
        except ExcelError as e:
            # Ошибка формы книги — код движка Excel уже в реестре реакций, не переобёртываем.
            raise TableMaterializerError(e.code, e.message, e.reason, e.suggested_tool) from e

        return {
            "book": book, "path": path, "level": schema.level,
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


def declared_rows(config_path: str | Path, referrer: Declaration, *path: str) -> list[dict]:
    """Стартовые строки листа, который декларация `referrer` называет по пути `path`, — копиями: читатель меняет свою
    копию, а не объявление."""
    return [dict(row) for row in named_sheet(config_path, referrer, *path).rows]


def named_sheet(config_path: str | Path, referrer: Declaration, *path: str, book: str = "fallback_book") -> BookSheet:
    """Лист книги сервера, который декларация `referrer` называет по пути `path` (ключи `<book>` и `sheet`). Имя листа
    судит дверь этой декларации по листам книги: лист, которого книга не объявляет, — отказ с путём ключа, а не «строк
    нет»; нет ключа — тоже отказ с путём."""
    schema = server_book(config_path, referrer.need(*path, book, kind=str))
    names: Any = Literal[tuple(sheet.name for sheet in schema.sheets)]   # значение ключа — одно из имён листов книги
    name = referrer.need(*path, "sheet", kind=names)
    return next(sheet for sheet in schema.sheets if sheet.name == name)


def server_book(config_path: str | Path, book: str) -> BookSchema:
    """Книга сервера по имени — для читателей вне материализатора: каталог книг решает `server_tables`."""
    return book_schema(server_tables(config_path), book)


def book_schema(tables_dir: str | Path, book: str) -> BookSchema:
    """Декларация книги `<tables_dir>/<книга>.schema.yaml` — одна дверь на всех её читателей: схема `BookSchema` судит
    файл целиком; нет файла — `TEMPLATE_NOT_FOUND` с подсказкой, битый — отказ двери с путём ключа."""
    path = Path(tables_dir) / f"{book}.schema.yaml"
    return Declared(path, TableMaterializerError, f"книги {book}",
                    f"Заведи {path.name} в config/templates/tables/ (формат — схема BookSchema, "
                    "core/declaration_schemas.py).", schema=BookSchema).model
