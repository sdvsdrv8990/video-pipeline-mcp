"""core/capacity.py — узел ёмкости: сколько работы класса идёт разом, решается одной точкой по декларации ресурсов.

Места классов инструментов (`classes`) и внутренней работы сервера (`work`) объявлены в `resources.yaml` и читаются
дверью деклараций; движок, каталог и поиск не решают предел сами, а спрашивают узел.
"""

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
