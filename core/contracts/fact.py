"""
core/contracts/fact.py — Fact (факт о сделанном)

## Назначение
Факт = зафиксированное действие сервера. Claude запоминает что было сделано.
Facts = память о действиях сервера для оркестратора.

## 4 уровня анализа

### 1. Код
- Один класс Fact(BaseModel) с 3 полями: type, data, id
- Pydantic модель, валидируется автоматически

### 2. Поведение
- Claude получает facts[] в ToolResult после каждого успешного действия
- Facts идут в _SESSION_LOG для истории
- Facts позволяют Claude понимать "что уже сделано" без повторного чтения

### 3. Поток данных
```
Сервер выполняет действие → создаёт Fact → кладёт в ToolResult.facts
→ Claude получает → запоминает → может ссылаться в следующих вызовах
```

### 4. Долгосрочный (6 мес)
- Facts копятся в _SESSION_LOG
- Claude может анализировать паттерны: "часто создаю fs_create_file перед json_push_to_queue"
- Это знание идёт в project_memory.md как "рабочий процесс"
- Через 6 мес: оптимизация порядка вызовов, меньше лишних операций

## Как будет меняться
- Добавятся новые type: RenderCreated, TranscriptionReady, ImageGenerated
- data может расширяться под конкретные типы
- id всегда опциональный (не все действия присваивают ID)

## Какие регрессии возможны
- Изменение формата data → сломает парсинг facts в _SESSION_LOG
- Удаление type → Claude не поймёт что сделано
"""

from pydantic import BaseModel
from typing import Literal


# D25: реестр типов фактов (единый источник).
KNOWN_FACT_TYPES = {
    "DirectoryTree", "Echo", "FileCreated", "FileRead",
    "FileWritten", "FileMoved", "FileRenamed", "FileDeleted",
    "FileSearch", "StructureCreated",
    "RenderCompleted", "SnapshotRead", "TableRead",
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
