"""core/declaration_schemas.py — схемы деклараций `config/*.yaml`: ключи и типы названы один раз.

Модель на файл декларации. Строгий режим: строка «5» не становится числом. Лишний ключ — ошибка, а не тишина.
Объект неизменяем, и отображения внутри — тоже: иначе один читатель поменял бы словарь всем.
Проверки между ключами одного файла живут здесь же и называют путь ключа; дверь — `core.declaration.Declared`.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Generic, NamedTuple, TypeVar, get_args, get_origin

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, Strict, StrictInt, StrictStr, ValidationError,
                      model_validator)
from pydantic_core import InitErrorDetails, PydanticCustomError

STRICT = ConfigDict(strict=True, frozen=True, extra="forbid")
ReadOnly = AfterValidator(MappingProxyType)
Listed = Strict(False)                                  # список YAML принимается кортежем; элементы проверяются строго
HttpStatus = Annotated[StrictInt, Field(ge=100, le=599)]  # все коды ответа — 100…599 (RFC 9110 §15)


class RegistryCode:
    """Метка типа: значение — код реестра server_reactions.yaml, которым потребитель декларации отказывает; по ней
    сторожа находят коды-данные без разбора YAML."""


ReactionCode = Annotated[StrictStr, RegistryCode()]


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


class RequestCodes(BaseModel):
    """Коды реестра, которыми загрузчик отказывает за потребителя на пути запроса и повтора."""

    model_config = STRICT

    failed: ReactionCode


class FetchCodes(RequestCodes):
    """…и при приёме тела: порча (длина или отпечаток не сошлись)."""

    corrupt: ReactionCode


class RequestPolicy(BaseModel):
    """Раздел `fetch` потребителя загрузчика `core.providers.download.Fetcher`, которому тело не отдаётся (длина по HEAD).

    Суд схемы, хоста и адреса, срок, переходы и повтор — общие для всех приёмников двери. Списки YAML становятся
    кортежами, элементы проверяются строго. `codes` — коды реестра реакций, которыми загрузчик отказывает за потребителя.
    """

    model_config = STRICT

    schemes: Annotated[tuple[StrictStr, ...], Listed]
    hosts: Annotated[tuple[StrictStr, ...], Listed]
    block_private: bool
    timeout_sec: float = Field(gt=0)
    redirects: int = Field(ge=0)
    attempts: int = Field(ge=1)
    backoff_sec: float = Field(ge=0)
    backoff_max_sec: float = Field(ge=0)
    retry_after_max_sec: float = Field(ge=0)
    jitter: float = Field(ge=0, le=1)
    retry_on: Annotated[tuple[HttpStatus, ...], Listed]
    codes: RequestCodes

    @model_validator(mode="after")
    def _coherent(self) -> "RequestPolicy":
        gaps = _fetch_gaps(self)
        if gaps:
            raise ValidationError.from_exception_data(type(self).__name__, gaps)
        return self


class FetchPolicy(RequestPolicy):
    """Раздел `fetch` потребителя, которому загрузчик отдаёт тело (файл, память): плюс предел, мера скорости и код порчи."""

    max_bytes: int = Field(ge=0)
    speed_floor_bytes_per_sec: float = Field(ge=0)
    speed_window_sec: float = Field(gt=0)
    codes: FetchCodes


class ResultFetchPolicy(FetchPolicy):
    """`media_tasks.yaml` `download.fetch`: загрузка результатов поставщиков; плюс поля ответа, где искать ссылку на файл."""

    url_fields: Annotated[tuple[StrictStr, ...], Listed]


def _fetch_gaps(policy: RequestPolicy) -> list[InitErrorDetails]:
    """Связь ключей раздела: пауза повтора начинается не выше своего потолка."""
    if policy.backoff_sec > policy.backoff_max_sec:
        return [_gap("backoff_over_ceiling", "пауза повтора не выше backoff_max_sec", ("backoff_sec",))]
    return []


class PackageIndex(BaseModel):
    """Индекс пакетов: как сервер спрашивает его о колесе (длина до установки)."""

    model_config = STRICT

    fetch: RequestPolicy


class PackagesDecl(BaseModel):
    """`config/packages.yaml`: обращения сервера к индексу пакетов."""

    model_config = STRICT

    index: PackageIndex


M = TypeVar("M", bound=BaseModel)


class Slot(NamedTuple, Generic[M]):
    """Что читается схемой: файл в каталоге деклараций, путь раздела (пусто — файл целиком) и схема. Пара «файл —
    схема» названа здесь один раз: читатели и сторожа берут её отсюда."""

    file: str
    section: tuple[str, ...]
    schema: type[M]


RESOURCES = Slot("resources.yaml", (), Resources)
PACKAGES = Slot("packages.yaml", (), PackagesDecl)
MODEL_FETCH = Slot("providers.yaml", ("local", "install", "fetch"), FetchPolicy)
RESULT_FETCH = Slot("media_tasks.yaml", ("download", "fetch"), ResultFetchPolicy)
SLOTS = (RESOURCES, PACKAGES, MODEL_FETCH, RESULT_FETCH)


def registry_codes(model: BaseModel) -> frozenset[str]:
    """Коды реестра, которые декларация отдаёт потребителю: значения полей с меткой `RegistryCode` — по типу поля,
    а не по виду строки."""
    return frozenset(_codes(model, type(model)))


def _codes(value: object, kind: object) -> set[str]:
    if get_origin(kind) is Annotated:
        inner, *metadata = get_args(kind)
        return {str(value)} if any(isinstance(m, RegistryCode) for m in metadata) else _codes(value, inner)
    if isinstance(value, BaseModel):
        # pydantic уносит метаданные Annotated поля в FieldInfo.metadata — возвращаем их к типу.
        return set().union(*(_codes(getattr(value, name), Annotated[(field.annotation, *field.metadata)]
                                    if field.metadata else field.annotation)
                             for name, field in type(value).model_fields.items()))
    args = get_args(kind)
    if isinstance(value, Mapping):
        return set().union(*(_codes(item, args[-1] if args else None) for item in value.values()))
    if isinstance(value, tuple):
        return set().union(*(_codes(item, args[0] if args else None) for item in value))
    return set()
