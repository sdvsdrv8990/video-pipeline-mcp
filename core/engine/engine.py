"""core/engine/engine.py — реестр инструментов и их исполнение.

Снимаемость с цикла объявляется КЛАССОМ инструмента, а не решается здесь: класс без `offload`
идёт inline, остальное уходит в поток со своим циклом. След пишется в `call`, а не в ветках
разбора, — иначе вызов, отбитый на валидации схемы, не оставлял бы следа вовсе.
"""

import asyncio
import contextvars
import inspect
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Awaitable
from dataclasses import dataclass

import jsonschema

from core.contracts import ContractError, Later, ToolResult, ErrorDetail, Recovery
from core.capacity import Capacity, Slots
from core.declaration_schemas import Resources

log = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """Определение инструмента.

    Порядок отображения подписи в MCP: `title` → `annotations.title` → `name`.
    """
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Awaitable[ToolResult | Later]]
    title: str = ""
    group: str = "general"
    annotations: dict | None = None


class Engine:
    """Движок инструментов MCP-сервера.

    Регистрирует и выполняет инструменты. Claude общается с сервером через Engine.

    Attributes:
        tools: Реестр инструментов
    """

    def __init__(self, reactions=None, state_manager=None, resources: Resources | None = None, trail=None):
        """Инициализация движка.

        Каждая зависимость необязательна, и отсутствие каждой МЕНЯЕТ поведение, а не отключает
        удобство: без `reactions` отказ уходит клиенту без класса и рецепта, без `resources`
        тяжёлый вызов морозит цикл событий, без `trail` сервер не оставляет после себя условий,
        по которым отказ воспроизводят. Что именно объявлено — в `config/`, не здесь; `resources` уже прошёл
        схему у двери деклараций — класс инструмента объявлен, у снятого с цикла есть предел и срок повтора.
        Места классов решает узел ёмкости (`Capacity`), а не движок.
        """
        self.tools: dict[str, ToolDefinition] = {}
        self.reactions = reactions
        self._state_manager = state_manager
        self._log_broken = False   # об отказе журнала говорим ОДИН раз за процесс
        self.trail = trail
        self.stopping = False      # сервер останавливается (метка server.py до runner.cleanup): отмена вызова — его, а не клиента
        classes = resources.classes if resources else {}
        self._klass = dict(resources.tools) if resources else {}
        self._offload = {name for name, klass in self._klass.items() if classes[klass].offload}
        # Места — один счёт на процесс у узла ёмкости: его же берут задания узла заданий (CP-05).
        self.slots = Capacity(resources).slots() if resources else Slots({})
        # Поток под каждое место снятого с цикла класса: пул строит тот же узел ёмкости (B16), а не формула библиотеки.
        self._threads = Capacity(resources).offload_threads() if resources else 0
        self._pool = ThreadPoolExecutor(self._threads, thread_name_prefix="offload") if self._threads else None
        self._retry_after = {name: body.retry_after_sec for name, body in classes.items() if body.retry_after_sec}

    async def _invoke(self, tool: ToolDefinition, params: dict) -> ToolResult:
        """Тяжёлый вызов уходит в поток со своим циклом, лёгкий исполняется здесь.

        Тела хендлеров синхронны (ни одного `await` внутри), поэтому inline-вызов держит ВЕСЬ
        сервер: пока идёт рендер, не отвечает даже `tools/list`. Поток возвращает циклу
        способность обслуживать остальных; предел одновременности — место класса в `slots`.
        """
        # Мест в классе нет — отказываем СРАЗУ. Ожидание внутри вызова уходит за предел клиента
        # и возвращается отказом без кода: решение о повторе принимает вызывающий, а не сервер.
        klass = self._klass.get(tool.name, "")
        if not self.slots.take(klass):
            wait = self._retry_after[klass]
            return self._error("RESOURCE_BUSY",
                               f"Класс ресурса '{klass}' занят: свободных мест нет, "
                               f"повтор не раньше чем через {wait} с.",
                               Recovery(reason=f"Повтори не раньше чем через {wait} с либо займись другой задачей."))
        if tool.name not in self._offload:
            try:
                answer = await tool.handler(**params)
            finally:
                self.slots.give(klass)
            return await self._told(answer)
        try:
            # `handler` объявлен как Awaitable, а не Coroutine: обёртка делает из любого ожидаемого
            # настоящую корутину, которую можно запустить своим циклом в потоке.
            awaitable = tool.handler(**params)

            async def _run() -> ToolResult | Later:
                return await awaitable

            # Свой пул движка, а не пул цикла по умолчанию: поток есть у каждого места (B16); контекст — как у to_thread.
            try:
                work = asyncio.get_running_loop().run_in_executor(self._pool, contextvars.copy_context().run,
                                                                  lambda: asyncio.run(_run()))
            except RuntimeError as e:
                # Система не дала поток (предел задач процесса): инструмент не запускался — отказ кодом, а не «упал».
                if inspect.iscoroutine(awaitable):
                    awaitable.close()
                raise ContractError("WORKER_UNAVAILABLE",
                                    facts={"work": tool.name, "klass": klass, "threads": self._threads}) from e
        except BaseException:
            self.slots.give(klass)
            raise
        # Место отдаёт конец потока, а не выход ожидающего: отменённый вызов (обрыв клиента) поток не останавливает,
        # и место, отданное раньше его конца, пустило бы вторую работу поверх живой (на карте — OOM).
        work.add_done_callback(lambda done: self._released(klass, done))
        return await self._told(await asyncio.shield(work))

    def _released(self, klass: str, done: asyncio.Future) -> None:
        """Место назад по концу потока; исход брошенного вызова забирается здесь — не «исключение, которое никто не
        прочёл» в журнале цикла."""
        self.slots.give(klass)
        if not done.cancelled():
            done.exception()

    @staticmethod
    async def _told(answer: ToolResult | Later) -> ToolResult:
        """Ответ обработчика как есть; `Later` — досказать на цикле движка: место и поток вызова уже отданы, ожидание
        итога идёт событием (AD-20 б)."""
        return await answer.finish() if isinstance(answer, Later) else answer

    def _error(self, code: str, message: str, recovery: "Recovery", facts: dict | None = None,
               errors: list[dict] | None = None) -> ToolResult:
        """Сборка ошибки через реестр реакций (`Reactions.get_error`): текст и подсказка по коду, факты подставлены.
        Кода нет в реестре или реестр не задан — переданный `recovery` (запасной путь вне контракта)."""
        if self.reactions is not None and self.reactions.get_reaction(code) is not None:
            return ToolResult(status="error", error=self.reactions.get_error(
                code, raw_message=message, facts=facts, errors=errors))
        return ToolResult(status="error", error=ErrorDetail(code=code, message=message, recovery=recovery,
                                                            facts=facts or {}, errors=errors or []))

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable[..., Awaitable[ToolResult | Later]],
        title: str = "",
        group: str = "general",
        annotations: dict | None = None
    ):
        """Регистрация инструмента; смысл полей — в `ToolDefinition`."""
        self.tools[name] = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            title=title,
            group=group,
            annotations=annotations
        )

    async def call(self, name: str, params: dict) -> ToolResult:
        """Вызов инструмента. След пишется здесь, а не в ветках `_dispatch`.

        У диспетчера пять путей возврата (нет инструмента, невалидные параметры, успех, TypeError,
        внутренняя ошибка), и запись в каждом держалась бы дисциплиной: шестой путь допишут без неё.

        Args:
            name: Имя инструмента
            params: Входные параметры

        Returns:
            ToolResult с результатом
        """
        started = time.monotonic()
        try:
            result = await self._dispatch(name, params)
        except asyncio.CancelledError:
            # Исхода нет — пишется улика отмены: живой оракул брошенного вызова (обрыв клиента).
            if self.trail is not None:
                self.trail.cancelled(name, self._klass.get(name, ""), time.monotonic() - started,
                                     "остановка сервера" if self.stopping else "обрыв клиента")
            raise
        if self.trail is not None:
            self.trail.write(name, params, result)
        return result

    async def _dispatch(self, name: str, params: dict) -> ToolResult:
        """Разбор вызова: реестр, валидация схемы, исполнение, перевод исключений в отказы."""
        if name not in self.tools:
            return self._error(
                "TOOL_NOT_FOUND",
                f"Инструмент '{name}' не найден",
                Recovery(suggested_tool="tools/list", reason="Посмотри доступные инструменты"),
            )

        tool = self.tools[name]

        # Валидация params против input_schema ДО вызова хендлера.
        # Раньше схема была декоративной (только витрина в tools/list).
        error = self._validate(tool, params)
        if error is not None:
            return error
        mismatch = _unbindable(tool.handler, params)
        if mismatch:
            # Вина клиента — только привязка параметров, и называются имена, а не значения; TypeError из тела — падение.
            return self._error(
                "VALIDATION_ERROR",
                f"Неверные параметры: {mismatch}",
                Recovery(suggested_tool="tools/list", reason="Сверь параметры со схемой инструмента"),
            )

        try:
            result = await self._invoke(tool, params)
            # Факты — в журнал фактов под корнем журналов (Logs.session), если state_manager подключён.
            if self._state_manager and isinstance(result, ToolResult) and result.facts:
                for fact in result.facts:
                    try:
                        self._state_manager.log_event(fact.type, fact.data)
                    except (OSError, ValueError, TypeError) as e:
                        # Основной поток не роняем, но и не молчим: иначе журнал перестаёт
                        # писаться незаметно, и понимаешь это, когда он уже понадобился.
                        if not self._log_broken:
                            self._log_broken = True
                            print(f"⚠️  [journal] запись факта не удалась ({e!r}) — "
                                  f"журнал фактов неполон; дальше молчим, чтобы не залить консоль")
            return result
        except Exception as e:
            return self.failure(name, e)

    def _validate(self, tool: ToolDefinition, params: dict) -> ToolResult | None:
        """Проверка params по JSON Schema инструмента: отказ называет место и правило схемы, а не присланное значение
        (текст jsonschema его цитирует). Возвращает ToolResult(error) при несоответствии, иначе None."""
        try:
            jsonschema.validate(instance=params, schema=tool.input_schema or {})
        except jsonschema.ValidationError as e:
            where = ".".join(map(str, e.absolute_path)) or "параметры"
            rule = f"{e.validator}: {e.validator_value}"
            return self._error(
                "VALIDATION_ERROR",
                f"Параметры не прошли валидацию схемы: {where} — правило «{rule}»",
                Recovery(suggested_tool="tools/list", reason="Исправь параметры по input_schema инструмента"),
                errors=[{"field": where, "rule": rule}],
            )
        return None

    def list_tools(self) -> list[dict]:
        """Получение плоского списка инструментов (для MCP протокола).

        Returns:
            Список определений инструментов (плоский)
        """
        result = []
        for tool in self.tools.values():
            item = {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema
            }
            # title — человекочитаемая подпись (приоритет отображения над name).
            if tool.title:
                item["title"] = tool.title
            if tool.annotations:
                item["annotations"] = tool.annotations
            result.append(item)
        return result

    def list_tools_grouped(self) -> list[dict]:
        """Получение списка инструментов, сгруппированных по group (для отображения).

        Returns:
            Список групп: [{"group": "filesystem", "tools": [{name, description, inputSchema}, ...]}, ...]
            Инструменты внутри каждой группы отсортированы по имени.
        """
        from collections import defaultdict
        groups: dict[str, list[dict]] = defaultdict(list)

        for tool in self.tools.values():
            groups[tool.group].append({
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "inputSchema": tool.input_schema
            })

        result = []
        for group_name in sorted(groups.keys()):
            tools_sorted = sorted(groups[group_name], key=lambda t: t["name"])
            result.append({
                "group": group_name,
                "tools": tools_sorted
            })

        return result

    def get_tool(self, name: str) -> ToolDefinition | None:
        """Получение инструмента по имени.

        Args:
            name: Имя инструмента

        Returns:
            ToolDefinition или None
        """
        return self.tools.get(name)

    def has_tool(self, name: str) -> bool:
        """Проверка наличия инструмента.

        Args:
            name: Имя инструмента

        Returns:
            True если инструмент зарегистрирован
        """
        return name in self.tools

    def refusal(self, exc: BaseException, name: str = "") -> ToolResult | None:
        """Единственный перевод исключения в отказ: объявленное ядром (`ContractError`, и отказы двери пути тоже) —
        своим кодом, текст и подсказка из реестра с фактами; чужое — None (падение решает граница). Отказ ожидаем: его
        чужая причина (`raise … from e`) — в журнал строкой без трассировки, клиенту — номер события фактом `event`."""
        if not isinstance(exc, ContractError):
            return None
        if exc.__cause__ is not None and not isinstance(exc.__cause__, ContractError):
            exc.with_facts(event=self._incident(name, exc.__cause__, exc.code))
        return self._error(exc.code, exc.message, Recovery(reason=exc.reason, suggested_tool=exc.suggested_tool),
                           facts=exc.facts, errors=exc.errors)

    def failure(self, name: str, exc: BaseException) -> ToolResult:
        """Исключение работы инструмента — отказ: объявленное — `refusal`, чужое — INTERNAL_ERROR с номером события.
        Один перевод для вызова и для задания узла заданий (EB-11)."""
        refused = self.refusal(exc, name)
        if refused is not None:
            return refused
        # Клиенту — общий текст с номером события, владельцу — причина с трассировкой: текст исключения несёт пути.
        event = self._incident(name, exc)
        return self._error("INTERNAL_ERROR", f"Инструмент {name} упал, событие {event}: причина в журнале сервера",
                           Recovery(reason="Внутренняя ошибка сервера"))

    def _incident(self, name: str, exc: BaseException, code: str = "") -> str:
        """Причина — в журнал сервера; номер события связывает ответ клиенту с записью. Падение — с трассировкой,
        чужая причина объявленного отказа `code` — строкой: отказ ожидаем."""
        event = uuid.uuid4().hex[:8]
        if code:
            log.info("инструмент %s: отказ %s, событие %s, причина: %s: %s", name, code, event, type(exc).__name__,
                     exc, extra={"event": event})
        else:
            log.error("инструмент %s, событие %s", name, event, exc_info=exc, extra={"event": event})
        return event


def _unbindable(handler: Callable[..., Awaitable[ToolResult | Later]], params: dict) -> str:
    """Чем параметры не ложатся на сигнатуру обработчика — только имена, без значений; пусто — ложатся."""
    signature = inspect.signature(handler)
    try:
        signature.bind(**params)
    except TypeError:
        declared = signature.parameters.values()
        extra = [] if any(p.kind is p.VAR_KEYWORD for p in declared) else sorted(set(params) - set(signature.parameters))
        missing = [p.name for p in declared if p.default is p.empty and p.name not in params
                   and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
        named = [f"лишние: {', '.join(extra)}"] * bool(extra) + [f"нет обязательных: {', '.join(missing)}"] * bool(missing)
        return "; ".join(named) or "параметры не ложатся на сигнатуру"
    return ""
