"""
core/contracts/task_status.py — TaskStatus

## Назначение
Статус async задачи для поллинга (Фаза 2).
Claude опрашивает через poll_* и видит: pending → processing → completed/failed.
"""

from pydantic import BaseModel, model_validator
from typing import Literal

from .error_detail import ErrorDetail


class TaskStatus(BaseModel):
    """Статус async задачи для поллинга."""

    task_id: str
    status: Literal["pending", "processing", "completed", "failed"]
    progress: dict | None = None
    result: dict | None = None
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def _check_invariant(self) -> "TaskStatus":
        """Инвариант status ↔ error/result."""
        if self.status == "failed" and self.error is None:
            raise ValueError("status='failed' requires error to be set")
        if self.status == "completed" and self.result is None:
            raise ValueError("status='completed' requires result to be set")
        if self.status in ("pending", "processing"):
            self.error = None
            self.result = None
        return self
