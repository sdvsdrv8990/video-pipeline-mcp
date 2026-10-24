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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

from core.contracts import ContractError
from core.paths import Files

if TYPE_CHECKING:
    from core.observability import Journal


class StateError(ContractError):
    """Отказ слоя снимков (код из server_reactions.yaml)."""


def _atomic_write_json(path: Path, data: Any):
    """JSON снимка, очереди и реестра — заменой слоя (FS-06): читатель видит старый файл целиком или новый, обрыв
    оставляет остаток с владельцем. Путь уже прошёл дверь у вызывающего; корень здесь — каталог самого файла."""
    Files(path.parent).replace(path.name, json.dumps(data, indent=2, ensure_ascii=False))


def _read_json(path: Path) -> Any:
    """JSON, который пишет `_atomic_write_json`, — чтением двери: открытый читателем файл писатель заменить может."""
    return json.loads(Files(path.parent).read_text(path.name))


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


# Путь сущности → (имена `{snapshot, queue}` из её шаблона, тип сущности); не сущность — ({}, "").
PairOf = Callable[[str], tuple[dict[str, str], str]]


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
        self._pairs: PairOf | None = None

    def bind_pairs(self, pairs: PairOf) -> None:
        """Откуда имена снимка и очереди сущности; подключает сборка контекста — реестр и шаблоны поднимаются позже."""
        self._pairs = pairs

    @overload
    def _file(self, entity_path: str, role: str, *, write: Literal[True]) -> Path: ...
    @overload
    def _file(self, entity_path: str, role: str, *, write: Literal[False]) -> Path | None: ...
    def _file(self, entity_path: str, role: str, *, write: bool) -> Path | None:
        """Файл роли `snapshot`/`queue` сущности по её шаблону. Пары нет: чтение — None, запись — отказ до записи.

        Raises:
            PathRefused: путь вне workspace или в закрытом каталоге
            StateError: запись в путь без пары — TABLE_NOT_FOUND (не сущность) или TABLE_PAIR_UNDECLARED
        """
        place = Files(self.workspace_path).place(entity_path)
        if self._pairs is None:
            raise RuntimeError("StateManager: имена снимка и очереди не подключены (bind_pairs)")
        pair, entity_type = self._pairs(entity_path)
        if pair:
            return place / pair[role]
        if not write:
            return None
        if not entity_type:
            raise StateError("TABLE_NOT_FOUND", facts={"table": entity_path})
        raise StateError("TABLE_PAIR_UNDECLARED", facts={"table": entity_path, "entity_type": entity_type})

    def snapshot_file(self, entity_path: str) -> Path:
        """Файл снимка сущности, куда пойдёт запись; писать некуда — отказ раньше записи."""
        return self._file(entity_path, "snapshot", write=True)

    def read_snapshot(self, entity_path: str) -> dict | None:
        """Снимок сущности; нет пары или файла — None.

        Raises:
            PathRefused: путь вне workspace или в закрытом каталоге
        """
        snapshot_file = self._file(entity_path, "snapshot", write=False)

        if snapshot_file is None or not snapshot_file.exists():
            return None

        return _read_json(snapshot_file)

    def write_snapshot(self, entity_path: str, data: dict):
        """Запись снимка целиком.

        Raises:
            PathRefused: путь вне workspace или в закрытом каталоге
            StateError: у пути нет пары снимок/очередь
        """
        snapshot_file = self.snapshot_file(entity_path)
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
            StateError: у пути нет пары снимок/очередь
        """
        snapshot_file = self.snapshot_file(entity_path)
        with _snapshot_lock(snapshot_file):
            snapshot = self.read_snapshot(entity_path) or {}
            yield snapshot
            _atomic_write_json(snapshot_file, snapshot)

    def push_to_queue(self, entity_path: str, operation: dict) -> bool:
        """Добавление операции в очередь сущности.

        Returns:
            True если добавлено успешно

        Raises:
            PathRefused: путь вне workspace или в закрытом каталоге
            StateError: у пути нет пары снимок/очередь
        """
        queue_file = self._file(entity_path, "queue", write=True)

        # Read-modify-write под локом + атомарная запись (без гонок/порчи).
        with self._lock:
            queue = []
            if queue_file.exists():
                queue = _read_json(queue_file)

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
        queue_file = self._file(entity_path, "queue", write=False)
        with self._lock:
            if queue_file is None or not queue_file.exists():
                return []
            return _read_json(queue_file)

    def execute_queue(self, entity_path: str) -> list[dict]:
        """Забрать очередь и очистить её; применяет операции вызывающий.

        Raises:
            PathRefused: путь вне workspace или в закрытом каталоге
        """
        queue_file = self._file(entity_path, "queue", write=False)

        # Атомарный «забрать и очистить» под локом.
        with self._lock:
            if queue_file is None or not queue_file.exists():
                return []

            queue = _read_json(queue_file)

            _atomic_write_json(queue_file, [])

        return queue

    def clear_queue(self, entity_path: str) -> int:
        """Очистка очереди без выполнения: в отличие от execute_queue операции НЕ применяются
        к снимку, а выбрасываются.

        Возвращает число отброшенных операций; PathRefused — путь вне workspace или в закрытом каталоге.
        """
        queue_file = self._file(entity_path, "queue", write=False)

        # Read-modify-write под локом + атомарная запись.
        with self._lock:
            if queue_file is None or not queue_file.exists():
                return 0
            count = len(_read_json(queue_file))
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
