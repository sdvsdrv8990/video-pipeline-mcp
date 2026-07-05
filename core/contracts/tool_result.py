"""
core/contracts/tool_result.py — ToolResult

## Назначение
Единый формат ответа от ЛЮБОГО инструмента сервера.
Claude видит один формат — не гадает что вернёт каждый инструмент.
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
