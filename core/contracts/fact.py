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
    # Режимы создания (S20/F59): сервер объясняет выбор и отчитывается о нём
    "CreationSkipped", "TemplatesCustomized",
    # Уникальность (A7.2): число, неполнота входов и сигнал в петлю решений
    "UniquenessComputed", "UniquenessIncomplete", "UniquenessAlert",
    "UniquenessCompensated",
    # Медиа: какой провайдер и модель выбраны по данным канала и чем кончилось исполнение
    "ProviderResolved", "MediaGenerated",
    # Каталог моделей: что доступно и что поставлено на машину
    "ModelsListed", "ModelInstalled", "ModelInstallStarted", "ModelInstallStatus",
    "MemoryRead", "MemoryWritten",
    "SearchCompleted", "QuickSearch", "MultiSearch",
    "FsSearch", "FsSearchYaml", "FsSearchMulti",
    "RenderCompleted", "SnapshotRead", "TableRead",
    # Таблицы: данные (Категория 3)
    "ColumnRead", "RowRead", "RowSet", "RowUpdated", "RowsFound", "RowAppended", "RowDeleted",
    "DependentsFound",
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
    # Фаза ТАБЛИЦЫ (A1′): книга материализована по декларации *.schema.yaml
    "TableMaterialized",
    # Реестр связей / ORPHAN (Ф2), верификация и здоровье (Ф4)
    "EntityLinked", "EntityOrphaned", "StructureVerified", "HealthChecked",
    # Иерархия ID: цепочка по каталогу назначения (S18-g/S18-h)
    "EntityRegistered", "EntityAdopted", "ChainResolved",
    # Индекс реестра: как ИИ узнаёт, какой ID искать (S19)
    "EntitiesFound", "EntityAnnotated", "MemoryIndexed",
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
