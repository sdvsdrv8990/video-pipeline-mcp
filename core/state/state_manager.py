"""core/state/state_manager.py — снимок для чтения и очередь записи рабочей области.

Чтение и запись разведены намеренно: снимок отвечает сразу, очередь копится до осознанного
применения — поэтому «посмотреть очередь» и «забрать очередь» разные операции, и первой долго не
было вовсе. Всякая запись атомарна и под локом: читатель видит либо старый файл целиком, либо
новый, но не рваный. Журнал сервера — след событий, а не копия рабочей области: значения усекаются,
секретные ключи маскируются.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.paths import Files

if TYPE_CHECKING:
    from core.observability import Journal


def _atomic_write_json(path: Path, data: Any):
    """JSON снимка, очереди и реестра — заменой слоя (FS-06): читатель видит старый файл целиком или новый, обрыв
    оставляет остаток с владельцем. Путь уже прошёл дверь у вызывающего; корень здесь — каталог самого файла."""
    Files(path.parent).replace(path.name, json.dumps(data, indent=2, ensure_ascii=False))


_SNAPSHOT_LOCKS: dict[Path, threading.Lock] = {}    # замок снимка — по его пути на процесс, а не по экземпляру
_SNAPSHOT_LOCKS_GUARD = threading.Lock()


def _snapshot_lock(path: Path) -> threading.Lock:
    """Замок одного снимка: писатели одного файла идут по очереди при любом числе экземпляров, разные снимки — нет."""
    with _SNAPSHOT_LOCKS_GUARD:
        return _SNAPSHOT_LOCKS.setdefault(path.resolve(), threading.Lock())


# Журнал сервера — не место для содержимого рабочей области и секретов.
# Значения усекаются, секретные ключи маскируются: лог остаётся следом событий,
# а не копией пользовательских данных.
LOG_VALUE_MAX = 200
LOG_SENSITIVE = {"authorization", "api_key", "token", "secret", "password", "cookie"}


def _sanitize_for_log(data: dict) -> dict:
    """Подготовить Fact.data к записи в журнал: маскировка секретов + усечение значений."""
    out: dict = {}
    for key, value in (data or {}).items():
        if any(s in str(key).lower() for s in LOG_SENSITIVE):
            out[key] = "***REDACTED***"
            continue
        text = str(value).replace("\n", " ")
        out[key] = text if len(text) <= LOG_VALUE_MAX else text[:LOG_VALUE_MAX] + f"…(+{len(text)-LOG_VALUE_MAX})"
    return out


class StateManager:
    """Управление состоянием сервера.

    Attributes:
        workspace_path: Путь к рабочему пространству
    """

    def __init__(self, workspace_path: str | Path, journal: Journal | None = None):
        """Инициализация.

        Args:
            workspace_path: Путь к workspace/
            journal: журнал фактов из объявления (`Logs.from_declaration`); нет — факты не пишутся (наборы без сервера)
        """
        self.workspace_path = Path(workspace_path)
        self.journal = journal
        # Очередь и снимки пишет только процесс сервера (раннер их не трогает): потоков хватает потокового замка,
        # межпроцессный здесь не нужен — в отличие от реестра связей и ключей канала.
        self._lock = threading.Lock()

    def read_snapshot(self, entity_path: str) -> dict | None:
        """Чтение снапшота (read.json).

        Args:
            entity_path: Путь к сущности (относительно workspace)

        Returns:
            Словарь данных или None

        Raises:
            PathRefused: путь вне workspace или в закрытом каталоге
        """
        snapshot_file = Files(self.workspace_path).place(entity_path) / "read.json"

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
            PathRefused: путь вне workspace или в закрытом каталоге
        """
        snapshot_file = Files(self.workspace_path).place(entity_path) / "read.json"
        with _snapshot_lock(snapshot_file):         # целиком — не посреди чужой правки снимка
            _atomic_write_json(snapshot_file, data)

    @contextmanager
    def edit_snapshot(self, entity_path: str) -> Iterator[dict]:
        """Чтение—правка—запись снимка одной операцией, всё или ничего: отдаёт снимок (файла нет — пустой dict) для правки
        на месте; выход без исключения — запись целиком, исключение — снимок прежний. Замок — на путь снимка в процессе,
        поэтому писатели одного снимка (очередь таблицы, учёт расхода, книга сцен) не теряют записей друг друга. Вложенный
        edit_snapshot или write_snapshot того же снимка внутри ждёт сам себя.

        Raises:
            PathRefused: путь вне workspace или в закрытом каталоге
        """
        snapshot_file = Files(self.workspace_path).place(entity_path) / "read.json"
        with _snapshot_lock(snapshot_file):
            snapshot = self.read_snapshot(entity_path) or {}
            yield snapshot
            _atomic_write_json(snapshot_file, snapshot)

    def push_to_queue(self, entity_path: str, operation: dict) -> bool:
        """Добавление операции в очередь (write.json).

        Args:
            entity_path: Путь к сущности
            operation: Операция для выполнения

        Returns:
            True если добавлено успешно

        Raises:
            PathRefused: путь вне workspace или в закрытом каталоге
        """
        queue_file = Files(self.workspace_path).place(entity_path) / "write.json"

        # Read-modify-write под локом + атомарная запись (без гонок/порчи).
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

    def read_queue(self, entity_path: str) -> list[dict]:
        """Очередь БЕЗ изъятия: `execute_queue` забирает и очищает, а посмотреть было нечем.

        Пустой список и на отсутствующий файл — для читателя «пусто» и «файла нет» одно и то же;
        отсутствие самой таблицы отвечает уровнем выше своим кодом.
        """
        queue_file = Files(self.workspace_path).place(entity_path) / "write.json"
        with self._lock:
            if not queue_file.exists():
                return []
            with open(queue_file, "r", encoding="utf-8") as f:
                return json.load(f)

    def execute_queue(self, entity_path: str) -> list[dict]:
        """Выполнение очереди операций.

        Args:
            entity_path: Путь к сущности

        Returns:
            Список выполненных операций

        Raises:
            PathRefused: путь вне workspace или в закрытом каталоге
        """
        queue_file = Files(self.workspace_path).place(entity_path) / "write.json"

        # Атомарный «забрать и очистить» под локом.
        with self._lock:
            if not queue_file.exists():
                return []

            with open(queue_file, "r", encoding="utf-8") as f:
                queue = json.load(f)

            _atomic_write_json(queue_file, [])

        return queue

    def clear_queue(self, entity_path: str) -> int:
        """Очистка очереди без выполнения: в отличие от execute_queue операции НЕ применяются
        к read.json, а выбрасываются.

        Возвращает число отброшенных операций; PathRefused — путь вне workspace или в закрытом каталоге.
        """
        queue_file = Files(self.workspace_path).place(entity_path) / "write.json"

        # Read-modify-write под локом + атомарная запись.
        with self._lock:
            if not queue_file.exists():
                return 0
            with open(queue_file, "r", encoding="utf-8") as f:
                count = len(json.load(f))
            _atomic_write_json(queue_file, [])
        return count

    def log_event(self, event_type: str, data: dict):
        """Факт в журнал фактов — под объявленным корнем журналов, с пределом и ротацией (`observability.yaml → logs`).
        Журнала нет — ничего не пишется.

        Args:
            event_type: Тип события
            data: Данные события
        """
        if self.journal is None:
            return
        entry = f"\n## [{time.strftime('%Y-%m-%d %H:%M:%S')}] {event_type}\n"
        for key, value in _sanitize_for_log(data).items():
            entry += f"- **{key}:** {value}\n"
        self.journal.write(entry)
