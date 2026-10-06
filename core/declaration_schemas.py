"""core/declaration_schemas.py — схемы деклараций `config/*.yaml`: ключи и типы названы один раз.

Модель на файл декларации. Строгий режим: строка «5» не становится числом. Лишний ключ — ошибка, а не тишина.
Объект неизменяем, и отображения внутри — тоже: иначе один читатель поменял бы словарь всем.
Проверки между ключами одного файла живут здесь же и называют путь ключа; дверь — `core.declaration.Declared`.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError

STRICT = ConfigDict(strict=True, frozen=True, extra="forbid")
ReadOnly = AfterValidator(MappingProxyType)


class ResourceClass(BaseModel):
    """Класс ресурса: снимается ли вызов с цикла событий, сколько вызовов идут разом, через сколько повторять «занято».

    Без `max_concurrent` предела нет — это законно только для класса, который с цикла не снимается.
    """

    model_config = STRICT

    offload: bool
    max_concurrent: int | None = Field(default=None, gt=0)
    retry_after_sec: int | None = Field(default=None, gt=0)


class Resources(BaseModel):
    """`config/resources.yaml`: классы ресурсов и класс каждого инструмента."""

    model_config = STRICT

    default: str
    classes: Annotated[Mapping[str, ResourceClass], ReadOnly]
    tools: Annotated[Mapping[str, str], ReadOnly]

    @model_validator(mode="after")
    def _coherent(self) -> "Resources":
        gaps = _resource_gaps(self)
        if gaps:
            raise ValidationError.from_exception_data(type(self).__name__, gaps)
        return self


def _resource_gaps(res: Resources) -> list[InitErrorDetails]:
    """Связи ключей файла: класс по умолчанию и класс каждого инструмента объявлены, у снятого с цикла — предел и срок."""
    gaps = [] if res.default in res.classes else [
        _gap("class_undeclared", "класс не объявлен в classes", ("default",))]
    gaps += [_gap("class_undeclared", "класс не объявлен в classes", ("tools", tool))
             for tool, klass in res.tools.items() if klass not in res.classes]
    for name, body in res.classes.items():
        if body.offload and body.max_concurrent is None:
            gaps.append(_gap("limit_missing", "класс снимается с цикла — предел обязателен",
                             ("classes", name, "max_concurrent")))
        if body.max_concurrent is not None and body.retry_after_sec is None:
            gaps.append(_gap("retry_missing", "у класса с пределом срок повтора обязателен",
                             ("classes", name, "retry_after_sec")))
    return gaps


def _gap(rule: str, expected: str, loc: tuple[str, ...]) -> InitErrorDetails:
    return {"type": PydanticCustomError(rule, expected), "loc": loc, "input": None}
