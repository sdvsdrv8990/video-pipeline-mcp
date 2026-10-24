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
from core.declaration_schemas import BookColumn, BookDefaults, BookSchema, BookSheet, book_formula_slips, book_sheets
from core.engine.template_resolver import server_tables
from core.formulas import DATA_ROW, compiled, declared_language, degraded


class TableMaterializerError(ContractError):
    """Ошибка материализации в формате контракта (код из server_reactions.yaml)."""


class TableMaterializer:
    """Материализатор книг таблиц по `*.schema.yaml` поверх ExcelEngine."""

    def __init__(self, excel: ExcelEngine, schemas_dir: str | Path, tables=None):
        """`tables` — движок снимков (`TableEngine`): книга родилась — снимок той же сущности получает схемы её листов;
        без него — только книга (пробы и наборы книг)."""
        self.excel = excel
        self.schemas_dir = Path(schemas_dir)
        self.tables = tables
        self._defaults: Mapping | None = None

    # ═══ Деградация формул — правила ЧИТАЮТСЯ, не зашиты ═══

    def _degrade_rules(self, schema: BookSchema) -> dict:
        """Запасные значения по типу столбца: книга перекрывает общий `_defaults.yaml`."""
        if self._defaults is None:
            self._defaults = book_defaults(self.schemas_dir)[1].degrade
        return {**self._defaults, **schema.degrade}

    @staticmethod
    def fallback(col: BookColumn, rules: dict) -> object:
        """Запасное значение формулы столбца на неполных данных — решает декларация: `on_empty` столбца, иначе правило по
        типу (книга перекрывает `_defaults.yaml`); нет — None, формула без обёртки."""
        return col.on_empty if col.on_empty is not None else rules.get(col.type)

    def _guarded(self, formula: str, col: BookColumn, rules: dict) -> str:
        """Формула строки книги с обёрткой IFERROR запасным значением столбца — та же, что у снимка (`degraded`)."""
        return degraded(formula, self.fallback(col, rules))

    def load_schema(self, book: str) -> BookSchema:
        """Декларация книги из каталога схем этого материализатора — через общую дверь книг (`book_schema`)."""
        return book_schema(self.schemas_dir, book)

    def materialize(self, book: str, path: str, entity: str = "") -> dict:
        """Создать книгу по декларации: листы → столбцы → формулы → валидации.

        `entity` — сущность, чей снимок получает схемы листов; пусто — каталог книги.
        Идемпотентности НЕТ намеренно: существующая книга — это данные владельца,
        молча перезаписывать её нельзя (ExcelEngine отдаёт FILE_EXISTS).
        """
        entity = entity or path.rpartition("/")[0]
        if self.tables is not None:
            try:
                self.tables.state.snapshot_file(entity)     # снимку некуда лечь — отказ до книги, а не книга без схем
            except ContractError as e:
                raise TableMaterializerError(e.code, facts=e.facts) from e
        schema = self.load_schema(book)
        rules = self._degrade_rules(schema)
        _, language = declared_language(TableMaterializerError)
        sheets = book_sheets(schema)
        guarded = sum(1 for sheet in schema.sheets for col in sheet.columns if col.flag == "F" and col.formula)
        try:
            self.excel.create_workbook(path, sheet=schema.sheets[0].name)
            for sheet in schema.sheets[1:]:
                self.excel.add_sheet(path, sheet.name)

            created: list[dict] = []
            for sheet in schema.sheets:
                for col in sheet.columns:
                    self.excel.add_column(path, sheet.name, col.name)
                    if col.type == "enum" and col.enum:
                        self.excel.set_validation(path, sheet.name, col.name, allowed=list(col.enum))
                # Строки-дефолты: лист может нести не только форму, но и стартовые значения (конфиг канала,
                # переведённый в листы); список в ячейке — через запятую; вычисляемый столбец — формула своей строки.
                list_cols = {c.name for c in sheet.columns if c.type == "list"}
                for at, row in enumerate(sheet.rows, start=DATA_ROW):
                    values = {k: (",".join(str(x) for x in v) if k in list_cols and isinstance(v, tuple) else v)
                              for k, v in row.items()}
                    values |= {col.name: self._guarded(compiled(col.formula, sheet.name, sheets, at, language), col,
                                                       rules)
                               for col in sheet.columns if col.flag == "F" and col.formula}
                    self.excel.append_row(path, sheet.name, values)
                created.append({"sheet": sheet.name, "columns": [c.name for c in sheet.columns],
                                "rows": len(sheet.rows)})
        except ExcelError as e:
            # Ошибка формы книги — код движка Excel уже в реестре реакций, не переобёртываем.
            raise TableMaterializerError(e.code, e.message, e.reason, e.suggested_tool) from e
        if self.tables is not None:
            formats = book_defaults(self.schemas_dir)[1].formats
            self.tables.declare_sheets(entity, schema, {
                (sheet.name, col.name): self.fallback(col, rules)
                for sheet in schema.sheets for col in sheet.columns if col.flag == "F" and col.formula}, {
                (sheet.name, col.name): formats[col.format].pattern
                for sheet in schema.sheets for col in sheet.columns if col.format})

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
                result = self.materialize(book, path, item.get("entity", ""))
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
    файл целиком (имена формул — её же суд); функции и агрегаты формул — связь с языком `config/formulas.yaml`, отказ
    называет оба файла; нет файла — `TEMPLATE_NOT_FOUND` с подсказкой, битый — отказ двери с путём ключа."""
    door = Declared(Path(tables_dir) / f"{book}.schema.yaml", TableMaterializerError, f"книги {book}", schema=BookSchema)
    schema = door.model
    language_door, language = declared_language(TableMaterializerError)
    if gaps := book_formula_slips(schema, language):
        rule = gaps[0][2].rule
        same = [(i, j, slip) for i, j, slip in gaps if slip.rule == rule]
        raise door.link_refused(rule, "; ".join(sorted({slip.expected for _, _, slip in same})),
                                [("sheets", str(i), "columns", str(j), "formula") for i, j, _ in same],
                                language_door, [(_LANGUAGE_KEY.get(rule, "macros"),)])
    defaults_door, defaults = book_defaults(tables_dir)
    if unknown := [(i, j) for i, sheet in enumerate(schema.sheets) for j, col in enumerate(sheet.columns)
                   if col.format and col.format not in defaults.formats]:
        raise door.link_refused("column_format", "формат из formats: " + (", ".join(sorted(defaults.formats)) or "—"),
                                [("sheets", str(i), "columns", str(j), "format") for i, j in unknown],
                                defaults_door, [("formats",)])
    return schema


# Ключ языка формул, на который ссылается изъян формулы книги (прочие изъяны рождает развёрнутый макрос).
_LANGUAGE_KEY = {"formula_function": "functions", "formula_row": "aggregates"}


def book_defaults(tables_dir: str | Path) -> tuple[Declared, BookDefaults]:
    """Общие правила книг `<tables_dir>/_defaults.yaml` — дверь и модель: запасные значения по типу, что запасное
    покрывает, форматы строк. Нет файла — правил нет (решение читателя); битый — отказ двери, как у книги."""
    door = Declared(Path(tables_dir) / "_defaults.yaml", TableMaterializerError, "общих правил книг", schema=BookDefaults)
    return door, (door.model if door.config_file.exists() else BookDefaults.model_validate({"degrade": {}}))
