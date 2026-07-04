"""
core/contracts/task_status.py — TaskStatus

## Назначение
Статус async задачи для поллинга (Фаза 2).
Claude опрашивает через poll_* и видит: pending → processing → completed/failed.

## 4 уровня анализа

### 1. Код
- Один класс TaskStatus(BaseModel) с 5 полями
- status — Literal с 4 значениями (строгий набор)
- result и error — опциональные (заполняются при terminal)

### 2. Поведение
- Claude шлёт trigger_* → получает task_id (через ToolResult)
- Claude опрашивает poll_* → получает TaskStatus
- pending/processing → Claude ждёт (или сервер опрашивает сам)
- completed → Claude читает result
- failed → Claude читает error и решает

### 3. Поток данных
```
Claude → trigger_* → сервер создаёт задачу → возвращает task_id
Claude → poll_*{task_id} → сервер проверяет → возвращает TaskStatus
     │
     ├── pending: ждать
     ├── processing: ждать
     ├── completed: читать result
     └── failed: читать error → retry/смена/человек
```

### 4. Долгосрочный (6 мес)
- Все async инструменты будут использовать этот паттерн
- progress может расширяться (процент, этапы)
- Claude накопит знание "какие задачи как долго выполняются"
- Через 6 мес: оптимизация таймаутов, предсказание времени

## Порядок полей (причина → следствие)
1. task_id — идентификатор задачи (обязательно для поллинга)
2. status — текущее состояние (определяет что дальше)
3. progress — прогресс (опционально, для длинных задач)
4. result — результат (при completed)
5. error — ошибка (при failed)

## Как будет меняться
- progress может стать обязательным для длинных задач
- result может расширяться (файл, метаданные, статистика)
- error будет заполняться чаще (больше async инструментов)

## Какие регрессии возможны
- Добавление нового status → сломает поллинг логику
- Изменение формата result → сломает download_* инструменты
- Удаление task_id → невозможно опрашивать статус
"""

from pydantic import BaseModel, model_validator
from typing import Literal

from .error_detail import ErrorDetail


class TaskStatus(BaseModel):
    """Статус async задачи для поллинга.

    Attributes:
        task_id: Уникальный ID задачи
        status: pending → processing → completed/failed
        progress: Прогресс выполнения (опционально)
        result: Результат при completed (опционально)
        error: Ошибка при failed (опционально)
    """
    task_id: str
    status: Literal["pending", "processing", "completed", "failed"]
    progress: dict | None = None
    result: dict | None = None
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def _check_invariant(self) -> "TaskStatus":
        """D22: инвариант status ↔ error/result."""
        if self.status == "failed" and self.error is None:
            raise ValueError("status='failed' requires error to be set")
        if self.status == "completed" and self.result is None:
            raise ValueError("status='completed' requires result to be set")
        if self.status in ("pending", "processing"):
            self.error = None
            self.result = None
        return self
