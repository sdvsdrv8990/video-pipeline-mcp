"""core/contracts/tool_result.py — единый формат ответа ЛЮБОГО инструмента.

Один формат на всех: иначе клиент гадает, что вернёт каждый инструмент, и разбирает ответы
по-разному.
"""

from pydantic import BaseModel, model_validator
from typing import Literal

from .error_detail import ErrorDetail
from .fact import Fact


class ToolResult(BaseModel):
    """Единый ответ от любого инструмента сервера."""

    status: Literal["success", "error"]
    data: dict | None = None
    error: ErrorDetail | None = None
    facts: list[Fact] = []

    @model_validator(mode="after")
    def _check_invariant(self) -> "ToolResult":
        """Инвариант status ↔ error/data."""
        if self.status == "error" and self.error is None:
            raise ValueError("status='error' requires error to be set")
        if self.status == "success" and self.error is not None:
            self.error = None  # success + error = молча убираем error
        return self
