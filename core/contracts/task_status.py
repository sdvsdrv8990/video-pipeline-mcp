"""core/contracts/task_status.py — статус асинхронной задачи для опроса.

Зовут: contracts.

Клиент опрашивает задачу сам или ждёт смены статуса: состояние обязано читаться в любой момент отдельным вызовом, а
о ходе живой задачи — её факты (`last_work_at`, `idle_sec`), без вердикта сервера по часам.
"""

from pydantic import BaseModel, model_validator
from typing import Literal

from .error_detail import ErrorDetail


class TaskStatus(BaseModel):
    """Статус долгой задачи для опроса — одна форма на все долгие операции (установка модели, задача провайдера).
    `params` — с чем задачу запустили: живёт во всех статусах, в отличие от `result`. `task_id=None` — задача не
    заводилась: исход известен сразу (например, «уже стоит»), прозванивать нечего."""

    task_id: str | None
    status: Literal["pending", "processing", "completed", "failed", "cancelled"]
    params: dict | None = None
    progress: dict | None = None
    result: dict | None = None
    error: ErrorDetail | None = None
    last_work_at: str | None = None     # живая задача: последний признак работы, ISO 8601 UTC
    idle_sec: int | None = None         # живая задача: сколько секунд признака работы нет на момент ответа

    @model_validator(mode="after")
    def _check_invariant(self) -> "TaskStatus":
        """Инвариант status ↔ error/result: отказ и снятие несут причину в `error`, готово — `result`."""
        if self.status in ("failed", "cancelled") and self.error is None:
            raise ValueError(f"status='{self.status}' requires error to be set")
        if self.status == "completed" and self.result is None:
            raise ValueError("status='completed' requires result to be set")
        if self.status == "cancelled":
            self.result = None
        if self.status in ("pending", "processing"):
            self.error = None
            self.result = None
        else:
            self.last_work_at = None
            self.idle_sec = None
        return self


class TaskEntry(BaseModel):
    """Элемент ответа о наборе заданий: ровно одно из `task` (статус задания; кончилось отказом — он в `task.error`) и
    `error` (о самом задании сказать нечего: нет такого, запись не читается). Один плохой id не прячет статусы
    остальных: отказ — в своём элементе, а не всего ответа."""

    task_id: str
    task: TaskStatus | None = None
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def _one(self) -> "TaskEntry":
        if (self.task is None) == (self.error is None):
            raise ValueError("ровно одно из task и error")
        return self
