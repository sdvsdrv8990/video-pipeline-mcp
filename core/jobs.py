"""core/jobs.py — узел заданий: долгая работа инструмента идёт заданием со своим id и статусом.

id задания выдаёт и судит одна точка по форме из декларации `jobs.yaml` (алфавит, длина): потребители — установщик
моделей и инструменты, переведённые на узел, — своей формы не держат. Запись задания — файл в хранилище узла
(`store`, от дома инстанса — `instance_home`). Исполнитель — поток, который держит межпроцессный замок записи:
свободный замок у незаконченного задания значит «исполнителя нет ни в одном процессе». Места — у счёта движка
(`Engine.slots`), перевод исключения — `Engine.failure`: своих мест и своего перевода у узла нет. Повтор того же
вызова в объявленном окне — то же задание (AD-21): вторая платная работа не заводится.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from filelock import Timeout
from pydantic import BaseModel, ValidationError

from core.contracts import ContractError, ErrorDetail, TaskStatus
from core.contracts.error_detail import redact
from core.contracts.task_status import TaskEntry
from core.declaration import Declaration, Declared, declarations_root, instance_home
from core.declaration_schemas import JOBS
from core.paths import Files

if TYPE_CHECKING:
    from core.engine.engine import Engine

_DECLARED = Declared(declarations_root() / JOBS.file, ContractError, "«jobs»",
                     f"Заведи config/{JOBS.file} — форма id и сроки заданий объявлены там.", schema=JOBS.schema)
_LIVE = ("pending", "processing")
_BEGIN = threading.Lock()                       # поиск двойника и запись нового задания — один шаг для узлов процесса
_DONE: dict[str, threading.Event] = {}          # конец работы исполнителя этого процесса — его ждёт `settle`
log = logging.getLogger(__name__)


def issue() -> str:
    """Новый id задания объявленной формы; знаки — из криптографического источника: чужой id перебором не угадать."""
    shape = _DECLARED.model.id
    return "".join(secrets.choice(shape.alphabet) for _ in range(shape.length))


def issued(task_id: object) -> bool:
    """Мог ли id выдать узел: объявленная длина и только знаки алфавита. Есть ли такое задание — дело хранилища."""
    shape = _DECLARED.model.id
    return isinstance(task_id, str) and len(task_id) == shape.length and set(task_id) <= set(shape.alphabet)


def stale(silent: float) -> bool:
    """Молчит ли работа дольше объявленного `stale_after_sec` — одно правило простоя для читателя записи, работы, которая
    ждёт чужое, и установки из CLI."""
    return silent > _DECLARED.model.stale_after_sec


def outlasts(quiet: float, other: Declaration, theirs: Sequence[tuple[str, ...]]) -> None:
    """Срок простоя длиннее законной тишины работы из другой декларации (`theirs` — пути её ключей), иначе живая работа
    станет простоем: нарушение — отказ двери на оба файла. Судит потребитель обоих, отказ собирает дверь."""
    if _DECLARED.model.stale_after_sec <= quiet:
        raise _DECLARED.link_refused("link_greater", "простой задания дольше законной тишины работы",
                                     [("stale_after_sec",)], other, theirs)


def _fingerprint(tool: str, key: dict) -> str:
    """Отпечаток вызова: инструмент и канонический JSON того, что определяет работу; секреты замаскированы — другой
    ключ доступа при тех же аргументах остаётся тем же вызовом."""
    canonical = json.dumps({"tool": tool, "key": redact(key)}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _Artifact(BaseModel):
    """Вход или выход подзадачи: id и отпечаток содержимого — по ним восстановление найдёт производителя входа."""

    id: str
    sha256: str


class _Record(BaseModel):
    """Запись задания на диске: статус для клиента и служебное узла; время — эпоха, она переживает процесс.
    `fingerprint` — отпечаток вызова для повтора, `fresh` — слово «заново» при запуске. Граф подзадач (`parent`,
    `children`) и их входы и выходы — место под восстановление по зависимостям (PLAN.md)."""

    tool: str
    klass: str
    created: float
    heartbeat: float
    finished: float | None = None
    task: TaskStatus
    fingerprint: str | None = None
    fresh: bool = False
    parent: str | None = None
    children: list[str] = []
    inputs: list[_Artifact] = []
    outputs: list[_Artifact] = []


class Pulse:
    """Что узел отдаёт работе задания: `pulse(progress, working=True)` — ход и признак работы в запись (`working=False`
    — ход без признака: простой не отодвигается); `quiet()` — молчит ли задание дольше объявленного; `stall()` — отказ
    простоя с теми же фактами, что видит читатель."""

    def __init__(self, node: Tasks, record: _Record):
        self._node, self.record = node, record

    def __call__(self, progress: dict | None = None, working: bool = True) -> None:
        self.record = self._node._save(self.record, alive=working, progress=progress)

    def quiet(self) -> bool:
        return stale(self._node.clock() - self.record.heartbeat)

    def stall(self) -> ContractError:
        return self._node._stall(self.record)


class Tasks:
    """Узел заданий над движком: задание — запись в хранилище и поток-исполнитель; статус — чтение записей."""

    def __init__(self, engine: Engine, root: Path | None = None, clock: Callable[[], float] = time.time):
        self.engine = engine
        self.files = Files(root or instance_home(declarations_root()))
        self.clock = clock

    def begin(self, tool: str, params: dict, klass: str, work: Callable[[Pulse], dict], *,
              key: dict | None = None, fresh: bool = False) -> dict:
        """Задание в фоне: статус сразу, работа — когда у класса есть место (до того — pending). `work(pulse)` отдаёт
        результат; `pulse` — ход и признак работы, по признаку судится простой (`Pulse`). Повтор вызова (`tool` и `key`,
        по умолчанию `params`) в окне `dedupe_window_sec` — элемент того же задания, если оно не кончилось отказом;
        `fresh` — новое задание."""
        mark = _fingerprint(tool, params if key is None else key)
        with _BEGIN:
            twin = None if fresh else self._twin(mark)
            if twin is not None:
                return twin
            task_id, written = issue(), threading.Event()
            _DONE[task_id] = threading.Event()
            threading.Thread(target=self._run, args=(task_id, tool, params, klass, work, mark, fresh, written),
                             daemon=True, name=f"task-{task_id}").start()
            written.wait()
        return self._view(task_id)

    def settle(self, task_id: str, within: float) -> dict:
        """Элемент задания, как только его исполнитель в этом процессе кончил работу, но не позже `within` секунд: ждёт
        событие конца, а не опрашивает запись. Задание другого процесса или уже кончившееся — элемент сразу."""
        done = _DONE.get(task_id)
        if done is not None:
            done.wait(within)
        return self._view(task_id)

    def status(self, task_ids: list[str] | None = None, tool: str | None = None) -> list[dict]:
        """Статусы названных заданий; без id — всех в хранилище (с `tool` — заданий этого инструмента и битых записей:
        их инструмент не прочесть, молча они не пропадают), конченые старше `retain_sec` убираются."""
        found = task_ids or [task_id for task_id, record in self._records()
                             if tool is None or record is None or record.tool == tool]
        return [self._view(task_id) for task_id in found]

    def record_file(self, task_id: str) -> Path:
        """Настоящий путь записи задания — для обвязки, которая готовит хранилище (засев сценария)."""
        return self.files.place(self._path(task_id))

    def _run(self, task_id: str, tool: str, params: dict, klass: str, work: Callable[[Pulse], dict], mark: str,
             fresh: bool, written: threading.Event) -> None:
        path = self._path(task_id)
        try:
            with self.files.lock(path):
                try:
                    now = self.clock()
                    record = self._write(_Record(
                        tool=tool, klass=klass, created=now, heartbeat=now, fingerprint=mark, fresh=fresh,
                        task=TaskStatus(task_id=task_id, status="pending", params=redact(params))))
                finally:
                    written.set()
                self.engine.slots.wait(klass)
                pulse = Pulse(self, record)
                try:
                    pulse.record = self._save(record, status="processing")
                    done: dict = {"status": "completed", "result": work(pulse)}
                except Exception as e:
                    done = {"status": "failed", "error": self.engine.failure(tool, e).error}
                finally:
                    self.engine.slots.give(klass)
                self._save(pulse.record, finished=True, **done)
        finally:
            _DONE.pop(task_id).set()

    def _view(self, task_id: str) -> dict:
        """Элемент ответа (`TaskEntry`): задание — `task` (незаконченное без исполнителя — прерванное, живое молчащее —
        простой); нет такого или запись не читается — `error` своего элемента, прочие отвечают как обычно."""
        if not issued(task_id):
            return self._refused(task_id, "TASK_NOT_FOUND", task_id=task_id)
        path = self._path(task_id)
        try:
            record = self._load(path)
        except FileNotFoundError:
            return self._refused(task_id, "TASK_NOT_FOUND", task_id=task_id)
        except ValueError:
            # Статус по битой записи неизвестен: отказ элемента, а не «failed»; запись остаётся уликой.
            log.warning("задание %s: TASK_RECORD_INVALID, запись %s", task_id, path)
            return self._refused(task_id, "TASK_RECORD_INVALID", path=path)
        task = (self._live(path, record) if record.task.status in _LIVE else record).task
        return TaskEntry(task_id=task_id, task=task).model_dump()

    def _refused(self, entry: str, code: str, /, **facts) -> dict:
        return TaskEntry(task_id=entry, error=self._error("task_status", code, **facts)).model_dump()

    def _twin(self, mark: str) -> dict | None:
        """Элемент задания того же вызова, запущенного в окне повтора и не кончившегося отказом (кончившееся отказом
        заводится заново); свежее — первым. Нет такого — None."""
        since = self.clock() - _DECLARED.model.dedupe_window_sec
        same = sorted(((record.created, task_id) for task_id, record in self._records()
                       if record is not None and record.fingerprint == mark and record.created >= since), reverse=True)
        for _, task_id in same:
            entry = self._view(task_id)
            if (entry.get("task") or {}).get("status") != "failed":
                return entry
        return None

    def _records(self) -> list[tuple[str, _Record | None]]:
        """Записи хранилища по порядку id: id и запись (битая — None); конченые старше `retain_sec` убираются с их
        замком. Битая запись остаётся: её статус называет порчу, а не молчит. id берутся и по замку записи: имя записи
        под `os.replace` обход каталога может не увидеть (btrfs), а замок создаётся до первой записи и не подменяется."""
        store, now, retain = self._store(), self.clock(), _DECLARED.model.retain_sec
        named = {Path(entry.path).name for entry in self.files.walk(store) if entry.kind == "file"}
        ids = sorted({name.removesuffix(".lock").removesuffix(".json") for name in named
                      if name.endswith((".json", ".json.lock"))})
        found: list[tuple[str, _Record | None]] = []
        for task_id in filter(issued, ids):
            path = self._path(task_id)
            try:
                record = self._load(path)
            except FileNotFoundError:           # замок есть, записи ещё нет или она уже убрана
                continue
            except ValueError:
                found.append((task_id, None))
                continue
            if record.finished is not None and now - record.finished > retain:
                self.files.remove(path)
                if self.files.place(f"{path}.lock").exists():
                    self.files.remove(f"{path}.lock")
                continue
            found.append((task_id, record))
        return found

    def _live(self, path: str, record: _Record) -> _Record:
        """Незаконченное задание: замок записи свободен — исполнителя нет ни в одном процессе, задание прервано (это
        пишется); держится, а признака работы нет дольше `stale_after_sec` — простой (только в ответе: исполнитель жив)."""
        lock = self.files.lock(path)
        try:
            lock.acquire(timeout=0)
        except Timeout:
            if record.task.status != "processing" or not stale(self.clock() - record.heartbeat):
                return record
            error = self._translated(record.tool, self._stall(record))
            return record.model_copy(update={"task": self._task(record.task, status="failed", error=error)})
        try:
            current = self._load(path)          # исполнитель мог дописать запись между чтением и замком
            if current.task.status not in _LIVE:
                return current
            error = self._error(current.tool, "TASK_INTERRUPTED", task_id=current.task.task_id, tool=current.tool)
            return self._save(current, status="failed", error=error, finished=True)
        finally:
            lock.release()

    def _save(self, record: _Record, *, finished: bool = False, alive: bool = True, **changes) -> _Record:
        """Запись с изменённым статусом: признак работы — сейчас, если работа шла (`alive`); конец — если задание
        кончилось."""
        now = self.clock()
        return self._write(record.model_copy(update={
            "task": self._task(record.task, **changes), "heartbeat": now if alive else record.heartbeat,
            "finished": now if finished else None}))

    @staticmethod
    def _task(task: TaskStatus, **changes) -> TaskStatus:
        """Новый статус через проверку контракта (инвариант status ↔ result/error), а не копией без суда."""
        return TaskStatus.model_validate({**task.model_dump(), **changes})

    def _stall(self, record: _Record) -> ContractError:
        """Простой живого исполнителя с фактами записи: его видит читатель, им же кончается работа, заметившая свою
        тишину."""
        return ContractError("TASK_STALLED", facts={"task_id": record.task.task_id, "tool": record.tool,
                                                    "seconds": int(self.clock() - record.heartbeat)})

    def _error(self, name: str, code: str, /, **facts) -> ErrorDetail:
        return self._translated(name, ContractError(code, facts=facts))

    def _translated(self, name: str, e: ContractError) -> ErrorDetail:
        """Отказ задания — тем же переводом сервера, что отказ вызова (`Engine.failure`)."""
        error = self.engine.failure(name, e).error
        assert error is not None
        return error

    def _write(self, record: _Record) -> _Record:
        self.files.replace(self._path(record.task.task_id or ""), record.model_dump_json())
        return record

    def _load(self, path: str) -> _Record:
        """Запись с диска через дверь корня; нет файла — FileNotFoundError, не JSON или не схема — ValueError."""
        try:
            return _Record.model_validate(json.loads(self.files.place(path).read_text(encoding="utf-8")))
        except ValidationError as e:
            raise ValueError(f"запись не по схеме: {e.error_count()} нарушений") from None

    def _store(self) -> str:
        store = _DECLARED.model.store
        self.files.place(store).mkdir(parents=True, exist_ok=True)
        return store

    def _path(self, task_id: str) -> str:
        return f"{self._store()}/{task_id}.json"
