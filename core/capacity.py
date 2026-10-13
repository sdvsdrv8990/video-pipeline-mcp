"""core/capacity.py — узел ёмкости: сколько работы класса идёт разом, решается одной точкой по декларации ресурсов.

Места классов инструментов (`classes`) и внутренней работы сервера (`work`) объявлены в `resources.yaml` и читаются
дверью деклараций; движок, каталог и поиск не решают предел сами, а спрашивают узел.
"""

import threading
from collections import deque
from collections.abc import Callable, Mapping

from core.contracts import ContractError
from core.declaration import Declared, declarations_root
from core.declaration_schemas import RESOURCES, Resources

_DECLARED = Declared(declarations_root() / RESOURCES.file, ContractError, "«resources»",
                     f"Заведи config/{RESOURCES.file} — места классов работы объявлены там.", schema=RESOURCES.schema)


class Capacity:
    """Места классов работы по декларации ресурсов; без готовой модели — читает декларацию сам через дверь."""

    def __init__(self, resources: Resources | None = None):
        self._given = resources

    @property
    def resources(self) -> Resources:
        return self._given if self._given is not None else _DECLARED.model

    def places(self, name: str) -> int | None:
        """Сколько работы класса `name` идёт разом: внутренняя работа — `work`, класс инструмента — `classes`;
        None — класс инструмента без предела (законно только у неснимаемого с цикла)."""
        resources = self.resources
        if name in resources.work:
            return resources.work[name].max_concurrent
        return resources.classes[name].max_concurrent

    def share(self, name: str) -> int:
        """Сколько ядер этого процесса приходится на одно место работы `name` — потоки внутри одной работы (`-threads`
        ffmpeg): ядра процесса (`process_cpus`) поровну на объявленные места, не меньше одного; класс без предела — все."""
        from core.providers.hardware import process_cpus     # здесь, а не сверху: пакет провайдеров сам зовёт узел ёмкости
        return max(1, process_cpus()[0] // (self.places(name) or 1))

    def slots(self) -> "Slots":
        """Счёт мест классов инструментов и внутренней работы: его держит движок, им же пользуется узел заданий — один
        на процесс (CP-05). Работа задания, которой клиент «занято» не слышит (загрузка весов), ждёт место класса `work`."""
        resources = self.resources
        return Slots({name: n for name in (*resources.classes, *resources.work) if (n := self.places(name))})


class Slots:
    """Места классов — один счёт для вызовов движка и заданий узла (CP-05).

    Вызов берёт место без ожидания (`take`): мест нет — отказ решает движок. Задание ждёт место в очереди класса по
    порядку прихода (`wait`) в своём потоке, и вызов не обгоняет ждущих; снятое задание уходит из очереди, не взяв
    места (`wake` будит ждущих судить уход). Класс без предела — место есть всегда.
    """

    def __init__(self, places: Mapping[str, int]):
        self._places = dict(places)
        self._free = dict(places)
        self._queue: dict[str, deque] = {name: deque() for name in places}
        self._turn = threading.Condition()

    def take(self, klass: str) -> bool:
        """Место сразу или отказ: свободных нет или их ждут задания класса."""
        with self._turn:
            if klass not in self._free:
                return True
            if self._free[klass] and not self._queue[klass]:
                self._free[klass] -= 1
                return True
            return False

    def wait(self, klass: str, gone: Callable[[], bool] = lambda: False) -> bool:
        """Место по порядку прихода: поток ждёт, пока его очередь не первая и место не свободно. `gone()` судится на
        каждом пробуждении: ушёл — False, места не брал, очередь за ним сдвигается."""
        with self._turn:
            if klass not in self._free:
                return not gone()
            ticket = object()
            self._queue[klass].append(ticket)
            self._turn.wait_for(lambda: gone() or (self._queue[klass][0] is ticket and self._free[klass] > 0))
            self._queue[klass].remove(ticket)
            self._turn.notify_all()
            if gone():
                return False
            self._free[klass] -= 1
            return True

    def wake(self) -> None:
        """Разбудить ждущих место: их уход (`gone` у `wait`) судится заново."""
        with self._turn:
            self._turn.notify_all()

    def give(self, klass: str) -> None:
        """Место назад; отдать невзятое — ошибка кода вызывающего, а не лишнее место."""
        with self._turn:
            if klass not in self._free:
                return
            if self._free[klass] >= self._places[klass]:
                raise RuntimeError(f"место класса {klass} отдано без взятия")
            self._free[klass] += 1
            self._turn.notify_all()
