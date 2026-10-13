"""core/contracts/tool_result.py — единый формат ответа ЛЮБОГО инструмента.

Зовут: contracts.

Один формат на всех: иначе клиент гадает, что вернёт каждый инструмент, и разбирает ответы
по-разному.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

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


@dataclass(frozen=True)
class Later:
    """Ответ обработчика, который досказывает движок на своём цикле событий после конца вызова: место и поток вызова
    отданы до ожидания, `finish` ждёт событием (итог задания в пределах бюджета клиента) и отдаёт ToolResult."""

    finish: Callable[[], Awaitable[ToolResult]]
