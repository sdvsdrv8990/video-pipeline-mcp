"""
core/contracts/__init__ — точка входа для импорта контрактов

## Назначение
Из любого места сервера:
    from core.contracts import ToolResult, ErrorDetail, Fact, TaskStatus

## Порядок импортов (зависимости снизу вверх)
1. Fact — базовый, используется другими
2. Recovery + ErrorDetail — ErrorDetail использует Recovery
3. ToolResult — использует ErrorDetail и Fact
4. TaskStatus — использует ErrorDetail

## Регрессии
- Удаление импорта → ломает все кто использует эту модель
- Переименование → ломает все импорты по всему проекту
"""

from .fact import Fact
from .error_detail import ErrorDetail, Recovery
from .tool_result import ToolResult
from .task_status import TaskStatus

__all__ = [
    # Базовые (используются другими)
    "Fact",
    # Связанные (ErrorDetail использует Recovery)
    "Recovery",
    "ErrorDetail",
    # Основные (используют базовые)
    "ToolResult",
    "TaskStatus",
]
