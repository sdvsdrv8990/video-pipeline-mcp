"""
core/contracts/__init__ — точка входа для импорта контрактов

## Назначение
Из любого места сервера:
    from core.contracts import ToolResult, ErrorDetail, Fact, TaskStatus
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
