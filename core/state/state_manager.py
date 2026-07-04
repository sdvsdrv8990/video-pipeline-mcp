"""
core/state/state_manager.py — Управление состоянием

## Назначение
Управление read.json, write.json, session log, project_memory.

## 4 уровня анализа

### 1. Код
- StateManager класс с методами read_snapshot, write_queue, log
- Управляет файлами состояния в workspace/

### 2. Поведение
- Claude читает данные через read.json
- Запись идёт через очередь в write.json
- Все действия логируются в _SESSION_LOG

### 3. Поток данных
```
Claude → read_snapshot() → read.json
Claude → write_queue() → write.json → execute_queue() → Excel
Действия → _SESSION_LOG
```

### 4. Долгосрочный (6 мес)
- Состояние растёт по мере работы
- Логи помогают отслеживать историю
- Кеш оптимизирует чтение

## Порядок полей
1. Пути (workspace_path)
2. Методы (read, write, log)
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from core.paths import safe_resolve


def _atomic_write_json(path: Path, data: Any):
    """D9: атомарная запись JSON — temp-файл в том же каталоге + os.replace.

    os.replace атомарен в пределах одной ФС: читатель видит либо старый,
    либо новый файл целиком, но никогда не «рваную» запись. Это устраняет
    порчу read.json/write.json при обрыве/конкурентной записи.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class StateManager:
    """Управление состоянием сервера.

    Attributes:
        workspace_path: Путь к рабочему пространству
    """

    def __init__(self, workspace_path: str | Path):
        """Инициализация.

        Args:
            workspace_path: Путь к workspace/
        """
        self.workspace_path = Path(workspace_path)
        # D9: сериализуем read-modify-write очереди в пределах процесса.
        # Кросс-процессную блокировку (несколько инстансов) добавит filelock.
        self._lock = threading.Lock()

    def read_snapshot(self, entity_path: str) -> dict | None:
        """Чтение снапшота (read.json).

        Args:
            entity_path: Путь к сущности (относительно workspace)

        Returns:
            Словарь данных или None

        Raises:
            ValueError: если путь выходит за пределы workspace (D29)
        """
        safe_resolve(entity_path, self.workspace_path)  # D29: containment
        snapshot_file = self.workspace_path / entity_path / "read.json"

        if not snapshot_file.exists():
            return None

        with open(snapshot_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def write_snapshot(self, entity_path: str, data: dict):
        """Запись снапшота (read.json).

        Args:
            entity_path: Путь к сущности
            data: Данные для записи

        Raises:
            ValueError: если путь выходит за пределы workspace (D29)
        """
        safe_resolve(entity_path, self.workspace_path)  # D29: containment
        snapshot_file = self.workspace_path / entity_path / "read.json"
        _atomic_write_json(snapshot_file, data)  # D9

    def push_to_queue(self, entity_path: str, operation: dict) -> bool:
        """Добавление операции в очередь (write.json).

        Args:
            entity_path: Путь к сущности
            operation: Операция для выполнения

        Returns:
            True если добавлено успешно

        Raises:
            ValueError: если путь выходит за пределы workspace (D29)
        """
        safe_resolve(entity_path, self.workspace_path)  # D29: containment
        queue_file = self.workspace_path / entity_path / "write.json"

        # D9: read-modify-write под локом + атомарная запись (без гонок/порчи).
        with self._lock:
            queue = []
            if queue_file.exists():
                with open(queue_file, "r", encoding="utf-8") as f:
                    queue = json.load(f)

            queue.append({
                "timestamp": time.time(),
                "operation": operation
            })

            _atomic_write_json(queue_file, queue)

        return True

    def execute_queue(self, entity_path: str) -> list[dict]:
        """Выполнение очереди операций.

        Args:
            entity_path: Путь к сущности

        Returns:
            Список выполненных операций

        Raises:
            ValueError: если путь выходит за пределы workspace (D29)
        """
        safe_resolve(entity_path, self.workspace_path)  # D29: containment
        queue_file = self.workspace_path / entity_path / "write.json"

        # D9: атомарный «забрать и очистить» под локом.
        with self._lock:
            if not queue_file.exists():
                return []

            with open(queue_file, "r", encoding="utf-8") as f:
                queue = json.load(f)

            _atomic_write_json(queue_file, [])

        return queue

    def log_event(self, event_type: str, data: dict):
        """Логирование события в _SESSION_LOG.

        Лог пишется в корень проекта (не в workspace/), т.к. это лог сервера,
        а не пользовательские данные.

        Args:
            event_type: Тип события
            data: Данные события
        """
        # Лог в корне проекта (не в workspace/)
        log_file = Path(__file__).resolve().parents[2] / "_SESSION_LOG.md"

        entry = f"\n## [{time.strftime('%Y-%m-%d %H:%M:%S')}] {event_type}\n"
        for key, value in data.items():
            entry += f"- **{key}:** {value}\n"

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)

    def entity_exists(self, entity_path: str) -> bool:
        """Проверка существования сущности.

        Args:
            entity_path: Путь к сущности

        Returns:
            True если существует

        Raises:
            ValueError: если путь выходит за пределы workspace (D29)
        """
        safe_resolve(entity_path, self.workspace_path)  # D29: containment
        return (self.workspace_path / entity_path).exists()
