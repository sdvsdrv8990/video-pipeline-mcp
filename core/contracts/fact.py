"""
core/contracts/fact.py — Fact (факт о сделанном)

## Назначение
Факт = зафиксированное действие сервера. Claude запоминает что было сделано.
Facts = память о действиях сервера для оркестратора.
"""

from pydantic import BaseModel


# D25: реестр типов фактов (единый источник).
KNOWN_FACT_TYPES = {
    "DirectoryTree", "Echo", "FileCreated", "FileRead",
    "FileWritten", "FileMoved", "FileRenamed", "FileDeleted",
    "FileSearch", "StructureCreated", "FileAppended",
    "MemoryRead", "MemoryWritten",
    "SearchCompleted", "QuickSearch", "MultiSearch",
    "FsSearch", "FsSearchYaml", "FsSearchMulti",
    "RenderCompleted", "SnapshotRead", "TableRead",
    # Таблицы: данные (Категория 3)
    "ColumnRead", "RowRead", "RowSet", "RowAppended", "RowDeleted",
    "QueuePushed", "QueueExecuted", "QueueCleared",
    # Таблицы: структура (Категория 2, excel_*)
    "WorkbookCreated", "SheetAdded", "SheetRenamed", "SheetDeleted",
    "SheetsReordered", "ColumnAdded", "ColumnDeleted", "ColumnMoved",
    "FormulaInserted", "FormattingApplied", "ValidationSet",
    "RangeRead", "FormulasValidated", "SheetCopied",
    # Анализ данных
    "FileInspected", "SheetInfoRead", "ColumnNamesRead",
    "UniqueValuesRead", "ValueCountsRead", "DuplicatesFound", "NullsFound",
    # Шаблоны структуры (TemplateEngine): создание узлов с контролем глубины
    "NodeCreated", "FolderCreated", "ChildDeferred", "TableDeferred",
    # Реестр связей / ORPHAN (Ф2), верификация и здоровье (Ф4)
    "EntityLinked", "EntityOrphaned", "StructureVerified", "HealthChecked",
    # Проверка целостности реестра
    "IntegrityIssue",
    # Миграция структуры
    "EntityMigrated",
}


class Fact(BaseModel):
    """Факт о сделанном действии сервера.

    Attributes:
        type: Тип факта (D25: из реестра KNOWN_FACT_TYPES)
        data: Что именно сделано (произвольный dict)
    """
    type: str
    data: dict

    def model_post_init(self, __context) -> None:
        """D25: предупреждаем если тип не в реестре."""
        if self.type not in KNOWN_FACT_TYPES:
            import warnings
            warnings.warn(f"Fact.type='{self.type}' не в реестре KNOWN_FACT_TYPES", stacklevel=2)
