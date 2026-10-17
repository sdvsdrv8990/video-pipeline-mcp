"""core/jobs.py — узел заданий: долгая работа инструмента идёт заданием со своим id и статусом.

id задания выдаёт и судит одна точка по форме из декларации `jobs.yaml` (алфавит, длина): потребители — установщик
моделей и инструменты, переведённые на узел, — своей формы не держат. Запись задания — файл в хранилище узла
(`store`, от дома инстанса — `instance_home`). Исполнитель — поток, который держит межпроцессный замок записи:
свободный замок у незаконченного задания значит «исполнителя нет ни в одном процессе». Места — у счёта движка
(`Engine.slots`), перевод исключения — `Engine.failure`: своих мест и своего перевода у узла нет. Повтор того же
вызова в объявленном окне — то же задание (AD-21): вторая платная работа не заводится. Остановить работу извне может
только снятие (`cancel`) задания, чей исполнитель в этом процессе; конечный статус необратим.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from filelock import Timeout
from pydantic import BaseModel, ValidationError

from core.contracts import ContractError, ErrorDetail, TaskStatus
from core.contracts.error_detail import redact
from core.contracts.task_status import TaskEntry
from core.declaration import Declared, declarations_root, instance_home
from core.declaration_schemas import JOBS
from core.paths import Files

if TYPE_CHECKING:
    from core.engine.engine import Engine

_DECLARED = Declared(declarations_root() / JOBS.file, ContractError, "«jobs»",
                     f"Заведи config/{JOBS.file} — форма id и сроки заданий объявлены там.", schema=JOBS.schema)
_LIVE = ("pending", "processing")
_BEGIN = threading.Lock()                       # поиск двойника и запись нового задания — один шаг для узлов процесса
_PULSES: dict[str, Pulse] = {}                  # исполнители этого процесса: их конец ждёт `settle`, их снимает `cancel`
_RUNNER = secrets.token_hex(8)                  # метка этого процесса в записи: смену чужого исполнителя здесь не услышать
_HEARD: set[Callable[[], None]] = set()         # слушатели смены статуса в этом процессе: их будит `_changed`
_HEARD_LOCK = threading.Lock()
log = logging.getLogger(__name__)
T = TypeVar("T")


def _changed() -> None:
    """Статус записи сменился или исполнитель этого процесса кончился — будит слушателей ожидания (`Tasks.waiting`);
    тик хода их не будит."""
    with _HEARD_LOCK:
        heard = list(_HEARD)
    for wake in heard:
        wake()


@contextmanager
def _hearing(wake: Callable[[], None]) -> Iterator[None]:
    """Слушатель смены статуса на время ожидания; снимается и при отмене ожидающего (обрыв клиента)."""
    with _HEARD_LOCK:
        _HEARD.add(wake)
    try:
        yield
    finally:
        with _HEARD_LOCK:
            _HEARD.discard(wake)


def _soon(loop: asyncio.AbstractEventLoop, wake: Callable[[], None]) -> Callable[[], None]:
    """Будильник цикла ожидающего из любого потока; цикл уже закрыт — будить некого."""
    def ring() -> None:
        try:
            loop.call_soon_threadsafe(wake)
        except RuntimeError:            # цикл закрыт: ожидающего нет, слушатель снимется его же выходом
            pass
    return ring


def issue() -> str:
    """Новый id задания объявленной формы; знаки — из криптографического источника: чужой id перебором не угадать."""
    shape = _DECLARED.model.id
    return "".join(secrets.choice(shape.alphabet) for _ in range(shape.length))


def issued(task_id: object) -> bool:
    """Мог ли id выдать узел: объявленная длина и только знаки алфавита. Есть ли такое задание — дело хранилища."""
    shape = _DECLARED.model.id
    return isinstance(task_id, str) and len(task_id) == shape.length and set(task_id) <= set(shape.alphabet)


def _fingerprint(tool: str, key: dict) -> str:
    """Отпечаток вызова: инструмент и канонический JSON того, что определяет работу; секреты замаскированы — другой
    ключ доступа при тех же аргументах остаётся тем же вызовом."""
    canonical = json.dumps({"tool": tool, "key": redact(key)}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _said(entries: list[dict]) -> list[str]:
    """Что ответ говорит о каждом задании: статус или код отказа элемента — смену этого ждёт `Tasks.waiting`."""
    return [(entry["task"] or {}).get("status") or entry["error"]["code"] for entry in entries]


class _Artifact(BaseModel):
    """Вход или выход подзадачи: id и отпечаток содержимого — по ним восстановление найдёт производителя входа."""

    id: str
    sha256: str


class _Record(BaseModel):
    """Запись задания на диске: статус для клиента и служебное узла; время — эпоха, она переживает процесс.
    `fingerprint` — отпечаток вызова для повтора, `fresh` — слово «заново» при запуске, `runner` — метка процесса
    исполнителя. Граф подзадач (`parent`, `children`) и их входы и выходы — место под восстановление по зависимостям
    (PLAN.md)."""

    tool: str
    klass: str
    created: float
    heartbeat: float
    finished: float | None = None
    task: TaskStatus
    fingerprint: str | None = None
    fresh: bool = False
    runner: str | None = None
    parent: str | None = None
    children: list[str] = []
    inputs: list[_Artifact] = []
    outputs: list[_Artifact] = []


class Pulse:
    """Связь узла с работой одного задания этого процесса. `pulse(progress, working=True)` — ход и признак работы в
    запись (`working=False` — ход без признака: `last_work_at` не сдвигается). `cancelled` — задание снято
    (`Tasks.cancel`): работа судит его между шагами и кончается отказом `refusal()`, а крючки `on_cancel` будят то, что
    ждёт само (процесс, очередь мест). После конечного статуса запись не меняется: поздняя запись — отказ с фактом в
    журнале."""

    def __init__(self, node: Tasks, record: _Record):
        self._node, self.record = node, record
        self.cancelled, self.done = threading.Event(), threading.Event()
        self._hooks: list[Callable[[], None]] = []
        self._turn = threading.RLock()          # запись исполнителя и снятие — по очереди: снятое не оживает

    def __call__(self, progress: dict | None = None, working: bool = True) -> None:
        self.put("ход", alive=working, progress=progress)

    def refusal(self) -> ContractError:
        """Отказ снятия с фактами задания: им кончается работа, заметившая `cancelled`."""
        return ContractError("TASK_CANCELLED", facts={"task_id": self.record.task.task_id, "tool": self.record.tool})

    def on_cancel(self, hook: Callable[[], None]) -> None:
        """Крючок снятия; задание уже снято — крючок зовётся сразу."""
        with self._turn:
            if not self.cancelled.is_set():
                self._hooks.append(hook)
                return
        hook()

    def put(self, what: str, *, finished: bool = False, alive: bool = True, **changes) -> bool:
        """Запись задания со сменой, пока статус живой; после конечного — отказ с фактом в журнале и False."""
        with self._turn:
            if self.record.task.status not in _LIVE:
                log.info("задание %s: %s после %s отказано", self.record.task.task_id, what, self.record.task.status)
                return False
            self.record = self._node._save(self.record, finished=finished, alive=alive, **changes)
            return True

    def cancel(self, error: ErrorDetail) -> bool:
        """Снять живое задание: конечный `cancelled` с причиной `error` (перевод — у того, кто снимает) в запись, сигнал
        работе, крючки. Уже кончилось — False."""
        with self._turn:
            if self.record.task.status not in _LIVE:
                return False
            self.put("снятие", finished=True, alive=False, status="cancelled", error=error)
            self.cancelled.set()
            hooks, self._hooks = self._hooks, []
        for hook in hooks:
            try:
                hook()
            except Exception:  # статус уже `cancelled`; несработавший крючок — с трассой в журнал сервера
                log.exception("задание %s: крючок снятия упал", self.record.task.task_id)
        return True


def paced(probe: Callable[[], T | None], pulse: Pulse, pauses: Iterator[float]) -> T:
    """Ожидание внешнего состояния в работе задания: проба — исход (значение), «ещё нет» (None) или отказ (исключение);
    между пробами — пауза темпом `pauses`, снятие задания её прерывает отказом снятия. Срока по часам нет: ход — число
    проб в `pulse`, предел — у другой стороны (её отказ, конец процесса) и у того, кто снимает."""
    tries = 0
    while (outcome := probe()) is None:
        tries += 1
        pulse({"проб": tries})
        if pulse.cancelled.wait(next(pauses)):
            raise pulse.refusal()
    return outcome


class Tasks:
    """Узел заданий над движком: задание — запись в хранилище и поток-исполнитель; статус — чтение записей."""

    def __init__(self, engine: Engine, root: Path | None = None, clock: Callable[[], float] = time.time):
        self.engine = engine
        self.files = Files(root or instance_home(declarations_root()))
        self.clock = clock

    def begin(self, tool: str, params: dict, klass: str, work: Callable[[Pulse], dict], *,
              key: dict | None = None, fresh: bool = False) -> dict:
        """Задание в фоне: статус сразу, работа — когда у класса есть место (до того — pending). `work(pulse)` отдаёт
        результат; `pulse` — ход, признак работы и снятие (`Pulse`). Повтор вызова (`tool` и `key`, по умолчанию
        `params`) в окне `dedupe_window_sec` — элемент того же задания, если оно не кончилось отказом или снятием;
        `fresh` — новое задание."""
        mark = _fingerprint(tool, params if key is None else key)
        with _BEGIN:
            twin = None if fresh else self._twin(mark)
            if twin is not None:
                return twin
            task_id, written = issue(), threading.Event()
            try:
                threading.Thread(target=self._run, args=(task_id, tool, params, klass, work, mark, fresh, written),
                                 daemon=True, name=f"task-{task_id}").start()
            except RuntimeError as e:   # система не дала поток (предел задач процесса): работа не запускалась, записи нет
                raise ContractError("WORKER_UNAVAILABLE", facts={"work": tool, "klass": klass}) from e
            written.wait()
        return self._view(task_id)

    def twin(self, tool: str, params: dict, *, key: dict | None = None) -> dict | None:
        """Элемент задания того же вызова в окне повтора (как у `begin`) или None — до отказов входа, которые зависят от
        состояния, меняемого самим двойником (место на диске у установки): повтор отдаёт тот же id, а не отказ.
        `begin` судит двойника снова под своим замком."""
        return self._twin(_fingerprint(tool, params if key is None else key))

    def settle(self, task_id: str, within: float) -> dict:
        """Элемент задания, как только его исполнитель в этом процессе кончил работу, но не позже `within` секунд: ждёт
        событие конца, а не опрашивает запись. Задание другого процесса или уже кончившееся — элемент сразу."""
        pulse = _PULSES.get(task_id)
        if pulse is not None:
            pulse.done.wait(within)
        return self._view(task_id)

    def status(self, task_ids: list[str] | None = None, tool: str | None = None) -> list[dict]:
        """Статусы названных заданий; без id — всех в хранилище (с `tool` — заданий этого инструмента и битых записей:
        их инструмент не прочесть, молча они не пропадают), конченые старше `retain_sec` убираются."""
        found = task_ids or [task_id for task_id, record in self._records()
                             if tool is None or record is None or record.tool == tool]
        return [self._view(task_id) for task_id in found]

    async def waiting(self, task_ids: list[str] | None = None) -> list[dict]:
        """Статусы названных заданий (без id — тех, что есть сейчас), как только статус хоть одного сменился (тик хода
        сменой не считается) или ждать нечего: ни одно не идёт у исполнителя этого процесса (`_round`). Ждёт событие
        узла без потока и без места: слушатель смены будит цикл ожидающего, отмена ожидающего (обрыв клиента) снимает
        слушателя. Записи читаются в потоке пула — цикл не ждёт диск. Смену статуса у исполнителя другого процесса
        покажет следующий вызов."""
        return await self._rounds(task_ids, change=True)

    async def until_end(self, task_ids: list[str], within: float) -> list[dict]:
        """Статусы названных заданий, как только каждое кончилось (конечный статус, снятие — тоже), но не позже `within`
        секунд — бюджета вызова клиента: ждёт событием, без потока и без места; бюджет вышел или ждать нечего (ни одно не
        идёт у исполнителя этого процесса) — статусы как есть."""
        try:
            async with asyncio.timeout(within):
                return await self._rounds(task_ids, change=False)
        except TimeoutError:
            return await asyncio.to_thread(self.status, task_ids)

    def cancel(self, task_ids: list[str]) -> list[dict]:
        """Снять задания — элемент на id (`TaskEntry`). Живое задание исполнителя этого процесса — конечный `cancelled`
        и сигнал работе (`Pulse.cancel`); кончившееся — отказ элемента с конечным статусом (TASK_FINISHED); живое, чей
        исполнитель в другом процессе (держит замок записи), — TASK_FOREIGN: сигнала между процессами нет."""
        return [self._cancel(task_id) for task_id in task_ids]

    def record_file(self, task_id: str) -> Path:
        """Настоящий путь записи задания — для обвязки, которая готовит хранилище (засев сценария)."""
        return self.files.place(self._path(task_id))

    def _cancel(self, task_id: str) -> dict:
        """Причину снятия переводит узел, принявший вызов (`Engine.failure` сервера), а не узел исполнителя: у
        исполнителя приспособления или другого движка своего реестра может не быть."""
        pulse = _PULSES.get(task_id)
        if pulse is not None and pulse.cancel(self._translated(pulse.record.tool, pulse.refusal())):
            return self._view(task_id)
        entry = self._view(task_id)
        if entry["task"] is None:
            return entry
        status = entry["task"]["status"]
        if status in _LIVE:
            return self._refused(task_id, "TASK_FOREIGN", task_id=task_id)
        return self._refused(task_id, "TASK_FINISHED", task_id=task_id, status=status)

    def _run(self, task_id: str, tool: str, params: dict, klass: str, work: Callable[[Pulse], dict], mark: str,
             fresh: bool, written: threading.Event) -> None:
        path, slots = self._path(task_id), self.engine.slots
        try:
            with self.files.lock(path):
                try:
                    now = self.clock()
                    record = _Record(tool=tool, klass=klass, created=now, heartbeat=now, fingerprint=mark, fresh=fresh,
                                     runner=_RUNNER,
                                     task=TaskStatus(task_id=task_id, status="pending", params=redact(params)))
                    pulse = _PULSES[task_id] = Pulse(self, record)  # до записи: живая запись без исполнителя — чужая
                    self._write(record)
                finally:
                    written.set()
                pulse.on_cancel(slots.wake)
                if not slots.wait(klass, pulse.cancelled.is_set):
                    return                      # снято в очереди: запись уже `cancelled`, места не брали
                try:
                    if not pulse.put("начало работы", status="processing"):
                        return                  # снято между местом и началом работы
                    done: dict = {"status": "completed", "result": work(pulse)}
                except Exception as e:
                    done = {"status": "failed", "error": self.engine.failure(tool, e).error}
                finally:
                    slots.give(klass)
                pulse.put("итог работы", finished=True, **done)
        finally:
            ended = _PULSES.pop(task_id, None)
            if ended is not None:
                ended.done.set()
            _changed()                          # исполнителя больше нет: ждущим нечего ждать от него, даже без записи

    def _view(self, task_id: str) -> dict:
        return self._seen(task_id)[0]

    def _seen(self, task_id: str) -> tuple[dict, _Record | None]:
        """Элемент ответа (`TaskEntry`): задание — `task` (незаконченное без исполнителя — прерванное, живое молчащее —
        простой; у живого — факты хода); нет такого или запись не читается — `error` своего элемента, прочие отвечают
        как обычно. Рядом — запись, по которой элемент собран (отказ элемента — None)."""
        if not issued(task_id):
            return self._refused(task_id, "TASK_NOT_FOUND", task_id=task_id), None
        path = self._path(task_id)
        try:
            record = self._load(path)
        except FileNotFoundError:
            return self._refused(task_id, "TASK_NOT_FOUND", task_id=task_id), None
        except ValueError:
            # Статус по битой записи неизвестен: отказ элемента, а не «failed»; запись остаётся уликой.
            log.warning("задание %s: TASK_RECORD_INVALID, запись %s", task_id, path)
            return self._refused(task_id, "TASK_RECORD_INVALID", path=path), None
        current = self._live(path, record) if record.task.status in _LIVE else record
        return TaskEntry(task_id=task_id, task=self._facts(current)).model_dump(), current

    def _facts(self, record: _Record) -> TaskStatus:
        """Статус для клиента; у живого — факты хода: последний признак работы и сколько его нет на момент ответа
        (конечному их снимает контракт). Вердикт по ним выносит клиент."""
        return self._task(record.task, idle_sec=int(self.clock() - record.heartbeat),
                          last_work_at=datetime.fromtimestamp(record.heartbeat, UTC).isoformat(timespec="seconds"))

    def _refused(self, entry: str, code: str, /, **facts) -> dict:
        return TaskEntry(task_id=entry, error=self._error("task_status", code, **facts)).model_dump()

    def _twin(self, mark: str) -> dict | None:
        """Элемент задания того же вызова, запущенного в окне повтора и не кончившегося отказом или снятием (такое
        заводится заново); свежее — первым. Нет такого — None."""
        since = self.clock() - _DECLARED.model.dedupe_window_sec
        same = sorted(((record.created, task_id) for task_id, record in self._records()
                       if record is not None and record.fingerprint == mark and record.created >= since), reverse=True)
        for _, task_id in same:
            entry = self._view(task_id)
            if (entry.get("task") or {}).get("status") not in ("failed", "cancelled"):
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
                for lock in (f"{path}.lock", f"{path}.judge.lock"):      # замок исполнителя и замок судей (`_live`)
                    if self.files.place(lock).exists():
                        self.files.remove(lock)
                continue
            found.append((task_id, record))
        return found

    def _live(self, path: str, record: _Record) -> _Record:
        """Незаконченное задание: замок записи свободен — исполнителя нет ни в одном процессе, задание прервано (это
        пишется, факт, а не срок); держится — исполнитель жив, запись как есть: молчание судит клиент по фактам хода.
        Судьи идут по очереди через свой замок: замок записи держит только исполнитель — занят он, значит, жив исполнитель,
        а не судья, который сейчас пишет прерывание."""
        with self.files.lock(f"{path}.judge"):
            lock = self.files.lock(path)
            try:
                lock.acquire(timeout=0)
            except Timeout:
                return record
            try:
                current = self._load(path)      # исполнитель или прежний судья мог дописать запись до замка
                if current.task.status not in _LIVE:
                    return current
                error = self._error(current.tool, "TASK_INTERRUPTED", task_id=current.task.task_id, tool=current.tool)
                return self._save(current, status="failed", error=error, finished=True)
            finally:
                lock.release()

    def _save(self, record: _Record, *, finished: bool = False, alive: bool = True, **changes) -> _Record:
        """Запись с изменённым статусом: признак работы — сейчас, если работа шла (`alive`); конец — если задание
        кончилось. Смена статуса будит ждущих (`wait`), тик хода — нет."""
        now = self.clock()
        saved = self._write(record.model_copy(update={
            "task": self._task(record.task, **changes), "heartbeat": now if alive else record.heartbeat,
            "finished": now if finished else None}))
        if saved.task.status != record.task.status:
            _changed()
        return saved

    @staticmethod
    def _task(task: TaskStatus, **changes) -> TaskStatus:
        """Новый статус через проверку контракта (инвариант status ↔ result/error), а не копией без суда."""
        return TaskStatus.model_validate({**task.model_dump(), **changes})

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

    async def _rounds(self, task_ids: list[str] | None, *, change: bool) -> list[dict]:
        """Круги ожидания: проверка — чтение записей в потоке пула, дальше роутер по прочитанному (`_round`): новый круг
        на событии смены или ответ; ветки итога и ошибки — у вызывающего. `change` — ответ и на первую смену статуса хоть
        одного. Слушатель встаёт до первого чтения, будильник гасится до каждого: смена во время чтения будит круг."""
        woke = asyncio.Event()
        with _hearing(_soon(asyncio.get_running_loop(), woke.set)):
            ids = task_ids if task_ids else await asyncio.to_thread(lambda: [task_id for task_id, _ in self._records()])
            first = None
            while True:
                woke.clear()
                seen = await asyncio.to_thread(lambda: [self._seen(task_id) for task_id in ids])
                entries = [entry for entry, _ in seen]
                if first is None:
                    first = _said(entries)
                if (change and _said(entries) != first) or not any(_round(record) for _, record in seen):
                    return entries
                await woke.wait()


def _round(record: _Record | None) -> bool:
    """Роутер ожидания по прочитанной записи: новый круг — задание идёт у исполнителя этого процесса, его смену разбудит
    `_changed`. Кончилось или отказ элемента — ответ (итог или ошибка у вызывающего); ушёл без итога — TASK_INTERRUPTED
    судом замка (`_live`); идёт у исполнителя другого процесса — событий здесь нет, ответ сейчас."""
    return record is not None and record.task.status in _LIVE and record.runner == _RUNNER
