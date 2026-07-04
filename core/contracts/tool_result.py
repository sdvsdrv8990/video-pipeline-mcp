"""
core/contracts/tool_result.py — ToolResult

## Назначение
Единый формат ответа от ЛЮБОГО инструмента сервера.
Claude видит один формат — не гадает что вернёт каждый инструмент.

## 4 уровня анализа

### 1. Код
- Один класс ToolResult(BaseModel) с 4 полями
- Использует ErrorDetail и Fact (связи через импорт)

### 2. Поведение
- Claude получает ToolResult после КАЖДОГО вызова инструмента
- status="success" → data + facts
- status="error" → error (ErrorDetail с кодом + recovery)
- Facts → память о действиях

### 3. Поток данных
```
Инструмент → ToolResult → Claude
     │                      │
     ├── data: dict         ├── читает facts
     ├── error: ErrorDetail ├── анализирует ошибку
     └── facts: list[Fact]  └── решает следующий шаг
```

### 4. Долгосрочный (6 мес)
- ToolResult — контракт стабилен (редко меняется)
- Расширяется только data (под конкретные инструменты)
- Facts копятся в _SESSION_LOG
- Через 6 мес: Claude знает паттерны "инструмент X обычно возвращает Y"

## Порядок полей (причина → следствие)
1. status — success/error (определяет что дальше)
2. data — полезные данные (содержимое при успехе)
3. error — ошибка (содержимое при ошибке)
4. facts — что сделано (память)

## Как будет меняться
- data может расширяться под конкретные инструменты
- facts будет заполняться чаще (больше инструментов)
- status всегда строго "success"|"error" (не меняется)

## Какие регрессии возможны
- Добавление нового status → сломает парсинг у Claude
- Изменение формата data → сломает все инструменты которые используют ToolResult
- Удаление facts → Claude потеряет память о действиях
"""

from pydantic import BaseModel, model_validator
from typing import Literal

from .error_detail import ErrorDetail
from .fact import Fact


class ToolResult(BaseModel):
    """Единый ответ от любого инструмента сервера.

    Attributes:
        status: success (выполнено) или error (ошибка)
        data: Полезные данные (при успехе)
        error: Детали ошибки (при ошибке)
        facts: Факты о сделанном (для памяти Claude)
    """
    status: Literal["success", "error"]
    data: dict | None = None
    error: ErrorDetail | None = None
    facts: list[Fact] = []

    @model_validator(mode="after")
    def _check_invariant(self) -> "ToolResult":
        """D22: инвариант status ↔ error/data."""
        if self.status == "error" and self.error is None:
            raise ValueError("status='error' requires error to be set")
        if self.status == "success" and self.error is not None:
            self.error = None  # success + error = молча убираем error
        return self
