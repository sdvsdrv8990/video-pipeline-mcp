"""core/declaration_schemas.py — схемы деклараций `config/*.yaml`: ключи и типы названы один раз.

Модель на файл декларации. Строгий режим: строка «5» не становится числом. Лишний ключ — ошибка, а не тишина.
Объект неизменяем, и отображения внутри — тоже: иначе один читатель поменял бы словарь всем.
Проверки между ключами одного файла живут здесь же и называют путь ключа; дверь — `core.declaration.Declared`.
"""

import string
import threading
import warnings
from collections.abc import Collection, Mapping
from functools import cache
from types import MappingProxyType
from typing import Annotated, Generic, Literal, NamedTuple, TypeVar, get_args, get_origin
from urllib.parse import urlsplit

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, Strict, StrictBool, StrictFloat, StrictInt,
                      RootModel, StrictStr, ValidationError, model_validator)
from pydantic_core import InitErrorDetails, PydanticCustomError

STRICT = ConfigDict(strict=True, frozen=True, extra="forbid")
ReadOnly = AfterValidator(MappingProxyType)
Listed = Strict(False)                                  # список YAML принимается кортежем; элементы проверяются строго
HttpStatus = Annotated[StrictInt, Field(ge=100, le=599)]  # все коды ответа — 100…599 (RFC 9110 §15)
FieldName = Annotated[StrictStr, Field(pattern=r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")]  # имя поля — token (RFC 9110 §5.1, §5.6.2)


class RegistryCode:
    """Метка типа: значение — код реестра server_reactions.yaml, которым потребитель декларации отказывает; по ней
    сторожа находят коды-данные без разбора YAML."""


ReactionCode = Annotated[StrictStr, RegistryCode()]
REACTIONS = "server_reactions.yaml"                     # реестр кодов отказа — файл в каталоге деклараций


class ResourceClass(BaseModel):
    """Класс ресурса: снимается ли вызов с цикла событий, сколько вызовов идут разом, через сколько повторять «занято».

    Без `max_concurrent` предела нет — это законно только для класса, который с цикла не снимается.
    """

    model_config = STRICT

    offload: bool
    max_concurrent: int | None = Field(default=None, gt=0)
    retry_after_sec: int | None = Field(default=None, gt=0)


class WorkClass(BaseModel):
    """Внутренняя работа сервера (описания каталога, поиск): сколько её идёт разом. Клиенту «занято» она не отвечает."""

    model_config = STRICT

    max_concurrent: int = Field(gt=0)


class Resources(BaseModel):
    """`config/resources.yaml`: классы ресурсов, класс каждого инструмента и места внутренней работы."""

    model_config = STRICT

    default: str
    classes: Annotated[Mapping[str, ResourceClass], ReadOnly]
    tools: Annotated[Mapping[str, str], ReadOnly]
    work: Annotated[Mapping[str, WorkClass], ReadOnly]

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


class CallCodes(FetchCodes):
    """…и при вызове API: запрос не ушёл (имя, адрес, соединение), истёк срок фазы или обращения, ответ неповторяемого
    вызова оборван после отправки — исход неизвестен (`corrupt` — обрыв ответа повторяемого вызова)."""

    unreached: ReactionCode
    timeout: ReactionCode
    outcome_unknown: ReactionCode


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

    @property
    def silence_sec(self) -> float:
        """Наибольшая законная тишина загрузчика между отметками работы: соединение и ожидание ответа (2 × timeout_sec,
        httpx меряет их порознь), своя пауза перед повтором (backoff_max_sec × (1 + jitter)) или срок, названный
        сервером (до retry_after_max_sec); попыток — `attempts`."""
        return max(2 * self.timeout_sec, self.backoff_max_sec * (1 + self.jitter), self.retry_after_max_sec)


class FetchPolicy(RequestPolicy):
    """Раздел `fetch` потребителя, которому загрузчик отдаёт тело (файл, память): плюс предел, мера скорости и код порчи."""

    max_bytes: int = Field(ge=0)
    speed_floor_bytes_per_sec: float = Field(ge=0)
    speed_window_sec: float = Field(gt=0)
    codes: FetchCodes


class ResultFetchPolicy(FetchPolicy):
    """`media_tasks.yaml` `download.fetch`: загрузка результатов поставщиков; плюс поля ответа, где искать ссылку на файл."""

    url_fields: Annotated[tuple[StrictStr, ...], Listed]


class AddressedPolicy(RequestPolicy):
    """Обращение к одному объявленному адресу: срок `deadline_sec` — на всё обращение вместе с повторами (срок
    вызывающего его только сокращает). Хост списком не объявляется — он один, хост объявленного адреса
    (`declared_host`); его ставит читатель `core.providers.download.answers`, явный ключ `hosts` — отказ схемы."""

    deadline_sec: float = Field(gt=0)
    hosts: Annotated[tuple[StrictStr, ...], Listed] = ()

    @model_validator(mode="before")
    @classmethod
    def _host_derived(cls, data: object) -> object:
        if isinstance(data, Mapping) and "hosts" in data:
            raise ValidationError.from_exception_data(cls.__name__, [_gap(
                "hosts_derived", "хост ответа — из объявленного адреса провайдера, списком не объявляется", ("hosts",))])
        return data

    @model_validator(mode="after")
    def _attempt_fits(self) -> "AddressedPolicy":
        # Фаза запроса — меньшее из timeout_sec и половины остатка срока: короче 2 × timeout_sec объявленная фаза не исполнится.
        if self.deadline_sec < 2 * self.timeout_sec:
            raise ValidationError.from_exception_data(type(self).__name__, [_gap(
                "deadline_under_attempt", "срок обращения вмещает одну полную попытку: не меньше 2 × timeout_sec",
                ("deadline_sec",))])
        return self


class AnswerPolicy(AddressedPolicy, FetchPolicy):
    """Раздел `answer` потребителя, которому загрузчик отдаёт ответ API в память (`Fetcher.answer`): предел обязателен
    (NI-06). Коды — как у приёма тела: pydantic собирает поля баз по полному набору каждой, и коды пути запроса из
    `AddressedPolicy` иначе перекрыли бы коды приёма."""

    max_bytes: int = Field(gt=0)
    codes: FetchCodes


class ProbePolicy(AddressedPolicy):
    """Раздел прозвонки: код и заголовки ответа без тела (`Fetcher.status`) — ключей тела у неё нет."""


class CallPolicy(AnswerPolicy):
    """Раздел `call` — вызов API методом с телом (`Fetcher.call`): ответ в память или потоком в сток. Повторяемый вызов —
    метод из `idempotent_methods` или объявленный ключ идемпотентности `idempotency_header` (null — провайдер ключа не
    объявляет); неповторяемый повторяется, только если запрос не применён (RFC 9110 §9.2.2): не ушёл или ответ из
    `retry_on ∩ unprocessed`."""

    idempotent_methods: Annotated[tuple[StrictStr, ...], Listed]
    unprocessed: Annotated[tuple[HttpStatus, ...], Listed]
    idempotency_header: FieldName | None
    codes: CallCodes


def declared_host(url: str) -> str:
    """Хост объявленного адреса провайдера. Подстановка `{…}` (значение пишет строка канала) законна в пути и запросе, а
    хост, который она собирает, судить нечем, — отказ, как и адрес без хоста."""
    parts = urlsplit(url)
    if not parts.hostname or "{" in parts.netloc:
        raise PydanticCustomError("host_templated", "хост адреса провайдера — объявленный, без подстановки {…}")
    return parts.hostname


def _declared_url(url: str) -> str:
    declared_host(url)
    return url


ProviderUrl = Annotated[StrictStr, AfterValidator(_declared_url)]   # адрес провайдера: хост объявлен, а не подставлен


class InstallProgress(BaseModel):
    """Наблюдение за установкой: как часто пересчитывать легшие байты; ход — в факты статуса задания узла, срока
    молчания нет (простой — не вердикт сервера)."""

    model_config = STRICT

    heartbeat_sec: float = Field(gt=0)


class InstallPolicy(BaseModel):
    """`providers.yaml` `local.install`: откуда и что ставить, сколько места, как тянуть и как часто смотреть на ход.

    `revisions` — закреплённая ревизия модели (репозиторий → коммит): часть происхождения, пустое отображение — явное
    «не закреплено».
    """

    model_config = STRICT

    allow_sources: Annotated[tuple[StrictStr, ...], Listed]
    allow_suffixes: Annotated[tuple[StrictStr, ...], Listed]
    deny_suffixes: Annotated[tuple[StrictStr, ...], Listed]
    max_total_mb: float = Field(gt=0)
    fetch: FetchPolicy
    prefer_files: Annotated[tuple[StrictStr, ...], Listed]
    progress: InstallProgress
    revisions: Annotated[Mapping[StrictStr, StrictStr], ReadOnly]


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


ColumnType = Literal["string", "date", "enum", "integer", "float", "list", "boolean"]
Cell = StrictStr | StrictInt | StrictFloat | StrictBool


class BookColumn(BaseModel):
    """Столбец листа книги: тип значения (по нему — правило деградации формулы), флаг роли (id — ключ, W — вводится,
    F — вычисляется формулой, fk — ссылка), допустимые значения перечисления, формула и запасное значение формулы."""

    model_config = STRICT

    name: StrictStr = Field(min_length=1)
    type: ColumnType
    flag: Literal["id", "W", "F", "fk"]
    enum: Annotated[tuple[StrictStr, ...], Listed] = ()
    formula: StrictStr = ""
    on_empty: Cell | None = None


def _xlsx_title(name: str) -> str:
    """Имя листа годится для книги .xlsx — как его судит библиотека записи книги (openpyxl у цели): запретный знак — её
    ValueError, длина за пределом формата — её предупреждение. Предела и знаков в нашем коде нет: судит тот, кто пишет."""
    refusal = _title_refusal(name)
    if refusal:
        raise PydanticCustomError("xlsx_sheet_title", "{refusal}", {"refusal": refusal})
    return name


_TITLE_PROBE = threading.Lock()


@cache
def _title_refusal(name: str) -> str:
    # Под замком: фильтр предупреждений глобален, две пробы разом смешали бы свои записи.
    with _TITLE_PROBE, warnings.catch_warnings(record=True) as said:
        warnings.simplefilter("always")
        from openpyxl import Workbook       # по требованию: схемы деклараций импортируют и там, где книг нет
        try:
            Workbook().active.title = name
        except ValueError as error:
            return str(error)
    return "; ".join(str(warning.message) for warning in said)


SheetTitle = Annotated[StrictStr, Field(min_length=1), AfterValidator(_xlsx_title)]


class BookSheet(BaseModel):
    """Лист книги: столбцы по порядку и стартовые строки (значения по именам столбцов, список — в ячейку через запятую)."""

    model_config = STRICT

    name: SheetTitle
    columns: Annotated[tuple[BookColumn, ...], Listed] = Field(min_length=1)
    rows: Annotated[tuple[Annotated[Mapping[StrictStr, Cell | Annotated[tuple[Cell, ...], Listed]], ReadOnly], ...],
                    Listed] = ()


class BookSchema(BaseModel):
    """`config/templates/tables/<книга>.schema.yaml`: книга таблиц — уровень структуры, источник, листы; своё правило
    деградации формул перекрывает общее `_defaults.yaml` по типу столбца."""

    model_config = STRICT

    book: StrictStr
    level: StrictStr
    source: StrictStr
    sheets: Annotated[tuple[BookSheet, ...], Listed] = Field(min_length=1)
    degrade: Annotated[Mapping[ColumnType, Cell], ReadOnly] = Field(default_factory=lambda: MappingProxyType({}))


class BookDefaults(BaseModel):
    """`config/templates/tables/_defaults.yaml`: общее правило деградации формул по типу столбца — под всеми книгами."""

    model_config = STRICT

    degrade: Annotated[Mapping[ColumnType, Cell], ReadOnly]


class AdviceItem(BaseModel):
    """Совет в успешном ответе: что, каким инструментом и с какими параметрами — исполним, а не проза."""

    model_config = STRICT

    id: StrictStr
    text: StrictStr
    tool: StrictStr
    params: Annotated[Mapping[StrictStr, object], ReadOnly]


class Recommendations(RootModel[Annotated[Mapping[StrictStr, Annotated[tuple[AdviceItem, ...], Listed]], ReadOnly]]):
    """`config/recommendations.yaml`: ключ совета → советы."""

    model_config = ConfigDict(strict=True, frozen=True)


class ReactionRecovery(BaseModel):
    """Рецепт строки реестра реакций: `reason` читает ИИ-клиент, `owner` — владелец у консоли (команда хоста, адрес
    объявления); инструмент, его параметры и действие владельца есть не у всякого кода."""

    model_config = STRICT

    reason: StrictStr
    suggested_tool: StrictStr | None = None
    suggested_params: Annotated[Mapping[StrictStr, object], ReadOnly] | None = None
    owner: StrictStr | None = None


class ReactionEntry(BaseModel):
    """Строка `config/server_reactions.yaml`. Плейсхолдер `owner` — факт, который код несёт: названный в тексте, рецепте
    или параметрах строки; иначе консоль печатала бы «—» на месте пропуска."""

    model_config = STRICT

    kind: Literal["ai_recoverable", "server_recoverable", "human_required", "integrity", "unknown"] = Field(alias="class")
    message_template: StrictStr
    recovery: ReactionRecovery

    @model_validator(mode="after")
    def _owner_facts(self) -> "ReactionEntry":
        params = [v for v in (self.recovery.suggested_params or {}).values() if isinstance(v, str)]
        known = {f for text in (self.message_template, self.recovery.reason, *params)
                 for _, f, _, _ in string.Formatter().parse(text) if f}
        loose = {f for _, f, _, _ in string.Formatter().parse(self.recovery.owner or "") if f} - known
        if loose:
            raise ValidationError.from_exception_data(type(self).__name__, [_gap(
                "owner_fact_unknown", f"плейсхолдер owner — факт кода из текста или рецепта строки, а не {sorted(loose)}",
                ("recovery", "owner"))])
        return self


class ServerReactions(RootModel[Annotated[Mapping[StrictStr, ReactionEntry], ReadOnly]]):
    """`config/server_reactions.yaml`: код отказа → класс, текст, рецепт клиенту и действие владельца."""

    model_config = ConfigDict(strict=True, frozen=True)


class JobId(BaseModel):
    """Форма id задания: алфавит и длина. Выдаёт и судит id одна точка — узел заданий (`core/jobs.py`)."""

    model_config = STRICT

    alphabet: StrictStr = Field(min_length=2)
    length: StrictInt = Field(gt=0)


class Jobs(BaseModel):
    """`config/jobs.yaml`: узел заданий сервера — форма id, каталог записей от дома инстанса, срок хранения конченых и
    окно, в котором повтор того же вызова — то же задание. Срока молчания нет: простой — не вердикт сервера."""

    model_config = STRICT

    id: JobId
    store: StrictStr = Field(min_length=1)
    retain_sec: StrictInt = Field(gt=0)
    dedupe_window_sec: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def _window_kept(self) -> "Jobs":
        # Окно длиннее хранения: уборка снимет запись раньше, чем кончится окно, и повтор заведёт вторую задачу.
        if self.dedupe_window_sec > self.retain_sec:
            raise ValidationError.from_exception_data(type(self).__name__, [_gap(
                "window_over_retain", "окно повтора не длиннее срока хранения retain_sec", ("dedupe_window_sec",))])
        return self


class RateLimit(BaseModel):
    """`config/firewall.yaml → rate_limit`: частота запросов с одного адреса — окно, предел запросов в нём и число
    превышений подряд до бана адреса. Единица времени — в имени ключа окна, а не в имени предела."""

    model_config = STRICT

    enabled: StrictBool
    window_sec: StrictInt = Field(gt=0)
    max_requests: StrictInt = Field(gt=0)
    ban_after_violations: StrictInt = Field(gt=0)


class TunnelSupervision(BaseModel):
    """`config/tunnel_diagnostics.yaml → надзор`: перезапуск cloudflared супервизором (пауза вдвое от `retry_base_seconds`
    до потолка `retry_max_seconds`, прогон дольше `retry_reset_seconds` сбрасывает разгон) и первый подъём туннеля (предел
    одной попытки и число попыток). Политика сервера, а не машины: закоммичена, умолчаний в коде нет."""

    model_config = STRICT

    retry_base_seconds: float = Field(gt=0)
    retry_max_seconds: float = Field(gt=0)
    retry_reset_seconds: float = Field(gt=0)
    ready_timeout_seconds: float = Field(gt=0)
    first_attempts: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def _coherent(self) -> "TunnelSupervision":
        if self.retry_base_seconds > self.retry_max_seconds:
            raise ValidationError.from_exception_data(type(self).__name__, [_gap(
                "backoff_over_ceiling", "пауза перезапуска не выше retry_max_seconds", ("retry_base_seconds",))])
        return self


class RunnerDockerPoll(BaseModel):
    """`config/providers.yaml → local.runner.docker_poll`: живой опрос готовности раннера в контейнере — знака готовности
    через трубку там нет (дескриптор не пересекает границу контейнера). Пауза вдвое от `pause_sec` до `pause_max_sec`,
    к ней добавка доли `jitter` от шва генератора."""

    model_config = STRICT

    pause_sec: float = Field(gt=0)
    pause_max_sec: float = Field(gt=0)
    jitter: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _coherent(self) -> "RunnerDockerPoll":
        if self.pause_sec > self.pause_max_sec:
            raise ValidationError.from_exception_data(type(self).__name__, [_gap(
                "pause_over_ceiling", "пауза опроса не выше pause_max_sec", ("pause_sec",))])
        return self


class TunnelBinaryFetch(BaseModel):
    """`config/tunnel_diagnostics.yaml → загрузка_клиента`: готовый бинарь клиентского конца туннеля — какой из `бинари`
    (`binary`: путь в дереве — его), адрес с подстановками `{system}` и `{arch}` (имена поставщика по системе и архитектуре
    машины: `systems`, `arches`) и политика загрузки двери `core.providers.download.Fetcher`. Системы или архитектуры нет
    в списке — готового бинаря нет."""

    model_config = STRICT

    binary: StrictStr = Field(min_length=1)
    url: StrictStr = Field(min_length=1)
    systems: Annotated[Mapping[StrictStr, StrictStr], ReadOnly]
    arches: Annotated[Mapping[StrictStr, StrictStr], ReadOnly]
    fetch: FetchPolicy

    @model_validator(mode="after")
    def _url_named(self) -> "TunnelBinaryFetch":
        # Адрес без подстановки взял бы один бинарь на все машины — чужая архитектура упала бы уже при запуске.
        missing = [part for part in ("{system}", "{arch}") if part not in self.url]
        if missing:
            raise ValidationError.from_exception_data(type(self).__name__, [_gap(
                "url_unbound", f"адрес с подстановками {', '.join(missing)}", ("url",))])
        return self


class Origins(BaseModel):
    """Источники страниц (Origin), которым сервер разрешает звать себя вдобавок к петле."""

    model_config = STRICT

    allowed: Annotated[tuple[StrictStr, ...], Listed]

    @model_validator(mode="after")
    def _origins_only(self) -> "Origins":
        # Origin — схема и хост без пути: строка с путём или слешем не совпала бы ни с одним запросом молча.
        gaps = [_gap("not_origin", "Origin: http(s)://хост[:порт] без пути", ("allowed", str(i)))
                for i, value in enumerate(self.allowed)
                if urlsplit(value).scheme not in ("http", "https") or not urlsplit(value).hostname
                or urlsplit(value).path or urlsplit(value).query or urlsplit(value).fragment]
        if gaps:
            raise ValidationError.from_exception_data(type(self).__name__, gaps)
        return self


class Client(BaseModel):
    """Клиент MCP и его пределы в его же единицах: как он доходит до сервера (`tunnel` — через клиентский конец туннеля,
    `loopback` — с этой машины), сколько ответа берёт (знаки или байты тела — ровно одно), сколько знаков текста отказа
    принимает целым (не объявлено — не режет) и срок вызова (не объявлен — ожидания нет)."""

    model_config = STRICT

    reached: Literal["tunnel", "loopback"]
    answer_chars: int | None = Field(default=None, gt=0)
    answer_bytes: int | None = Field(default=None, gt=0)
    error_chars: int | None = Field(default=None, gt=0)
    call_sec: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _one_budget(self) -> "Client":
        # Бюджет ответа — в одной единице клиента: без него ответ не с чем сравнить, две — двусмысленность.
        if (self.answer_chars is None) == (self.answer_bytes is None):
            raise ValidationError.from_exception_data(type(self).__name__, [_gap(
                "budget_one", "ровно один из answer_chars, answer_bytes", ())])
        return self


class TransportDeclaration(BaseModel):
    """`config/transport.yaml`: что сервер обещает клиенту на проводе — кто может звать его из браузера и какие клиенты
    сколько ответа берут и сколько ждут вызов."""

    model_config = STRICT

    origins: Origins
    clients: Annotated[Mapping[StrictStr, Client], Field(min_length=1), ReadOnly]


class EnginePlaces(BaseModel):
    """`config/engines.yaml → <движок>`: имена бинаря и места установки по ОС, где искать после PATH, по порядку. Место —
    шаблон пути с переменными среды ОС; ключ ОС — значение `sys.platform` (linux, darwin, win32)."""

    model_config = STRICT

    names: Annotated[tuple[StrictStr, ...], Listed, Field(min_length=1)]
    linux: Annotated[tuple[StrictStr, ...], Listed]
    darwin: Annotated[tuple[StrictStr, ...], Listed]
    win32: Annotated[tuple[StrictStr, ...], Listed]


M = TypeVar("M", bound=BaseModel)


class Slot(NamedTuple, Generic[M]):
    """Что читается схемой: файл в каталоге деклараций, путь раздела (пусто — файл целиком) и схема. Пара «файл —
    схема» названа здесь один раз: читатели и сторожа берут её отсюда."""

    file: str
    section: tuple[str, ...]
    schema: type[M]


RESOURCES = Slot("resources.yaml", (), Resources)
PACKAGES = Slot("packages.yaml", (), PackagesDecl)
MODEL_INSTALL = Slot("providers.yaml", ("local", "install"), InstallPolicy)
MODEL_FETCH = Slot("providers.yaml", ("local", "install", "fetch"), FetchPolicy)
RESULT_FETCH = Slot("media_tasks.yaml", ("download", "fetch"), ResultFetchPolicy)
ONLINE_ANSWER = Slot("providers.yaml", ("online", "answer"), AnswerPolicy)
ONLINE_CALL = Slot("providers.yaml", ("online", "call"), CallPolicy)
RESULT_ANSWER = Slot("media_tasks.yaml", ("download", "answer"), AnswerPolicy)
RUNNER_HEALTH = Slot("providers.yaml", ("local", "runner", "health"), AnswerPolicy)
RUNNER_DOCKER_POLL = Slot("providers.yaml", ("local", "runner", "docker_poll"), RunnerDockerPoll)
TUNNEL_PROBE = Slot("tunnel_diagnostics.yaml", ("прозвонка",), ProbePolicy)
JOBS = Slot("jobs.yaml", (), Jobs)
RATE_LIMIT = Slot("firewall.yaml", ("rate_limit",), RateLimit)
TUNNEL_BINARY_FETCH = Slot("tunnel_diagnostics.yaml", ("загрузка_клиента",), TunnelBinaryFetch)
TUNNEL_SUPERVISION = Slot("tunnel_diagnostics.yaml", ("надзор",), TunnelSupervision)
TRANSPORT = Slot("transport.yaml", (), TransportDeclaration)
RECALC_ENGINE = Slot("engines.yaml", ("recalc",), EnginePlaces)
SLOTS = (RESOURCES, PACKAGES, MODEL_INSTALL, MODEL_FETCH, RESULT_FETCH, ONLINE_ANSWER, ONLINE_CALL, RESULT_ANSWER,
         RUNNER_HEALTH, RUNNER_DOCKER_POLL, TUNNEL_PROBE, JOBS, RATE_LIMIT, TUNNEL_BINARY_FETCH, TUNNEL_SUPERVISION,
         TRANSPORT, RECALC_ENGINE)


def registry_codes(model: BaseModel) -> frozenset[str]:
    """Коды реестра, которые декларация отдаёт потребителю: значения полей с меткой `RegistryCode` — по типу поля,
    а не по виду строки."""
    return frozenset(code for _, code in _codes(model, type(model), ()))


def unregistered_codes(model: BaseModel, known: Collection[str]) -> list[InitErrorDetails]:
    """Поля-коды, чьего кода нет в реестре: опечатка прошла бы дверь, и клиент получил бы UNKNOWN_ERROR вместо
    объявленной реакции. Путь — от корня модели."""
    return [_gap("registry_code", f"код, объявленный в {REACTIONS}", loc)
            for loc, code in _codes(model, type(model), ()) if code not in known]


def _codes(value: object, kind: object, loc: tuple) -> list[tuple[tuple, str]]:
    if get_origin(kind) is Annotated:
        inner, *metadata = get_args(kind)
        return [(loc, str(value))] if any(isinstance(m, RegistryCode) for m in metadata) else _codes(value, inner, loc)
    if isinstance(value, BaseModel):
        # pydantic уносит метаданные Annotated поля в FieldInfo.metadata — возвращаем их к типу.
        return [pair for name, field in type(value).model_fields.items()
                for pair in _codes(getattr(value, name), Annotated[(field.annotation, *field.metadata)]
                                   if field.metadata else field.annotation, (*loc, name))]
    args = get_args(kind)
    if isinstance(value, Mapping):
        return [pair for key, item in value.items() for pair in _codes(item, args[-1] if args else None, (*loc, key))]
    if isinstance(value, tuple):
        return [pair for i, item in enumerate(value) for pair in _codes(item, args[0] if args else None, (*loc, i))]
    return []
