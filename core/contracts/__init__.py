"""
core/contracts/__init__ — точка входа для импорта контрактов

## Назначение
Из любого места сервера:
    from core.contracts import ToolResult, ErrorDetail, Fact, TaskStatus
"""

from .fact import Fact
from .errors import ContractError
from .error_detail import ErrorDetail, Recovery
from .tool_result import ToolResult
from .task_status import TaskStatus
from .untrusted import UntrustedText, as_untrusted

__all__ = [
    # Базовые (используются другими)
    "Fact",
    # Отказ подсистемы в форме контракта — база для исключений зон
    "ContractError",
    # Связанные (ErrorDetail использует Recovery)
    "Recovery",
    "ErrorDetail",
    # Основные (используют базовые)
    "ToolResult",
    "TaskStatus",
    # Провенанс чужого текста в выводе
    "UntrustedText",
    "as_untrusted",
]
