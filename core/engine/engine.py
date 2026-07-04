"""
core/engine/engine.py — Основной класс Engine

## Назначение
Реестр и исполнитель инструментов. Claude отправляет запрос → Engine находит инструмент → выполняет.

## 4 уровня анализа

### 1. Код
- Engine класс с методами register, call, list_tools
- Хранит реестр инструментов (name → handler)
- Каждый инструмент = callable

### 2. Поведение
- Claude отправляет tools/list → Engine возвращает список
- Claude отправляет tools/call → Engine находит и выполняет
- Ошибки маппятся на ErrorDetail

### 3. Поток данных
```
Claude → tools/list → Engine.list_tools() → список инструментов
Claude → tools/call → Engine.call(name, params) → ToolResult
```

### 4. Долгосрочный (6 мес)
- Все инструменты регистрируются через Engine
- Добавление нового инструмента = register + handler
- Engine не меняется при добавлении инструментов

## Порядок полей
1. Реестр инструментов (instruments)
2. Методы (register, call, list_tools)
"""

from typing import Any, Callable, Awaitable
from dataclasses import dataclass, field

from core.contracts import ToolResult, ErrorDetail, Recovery, Fact


@dataclass
class ToolDefinition:
    """Определение инструмента.

    Attributes:
        name: Имя инструмента
        description: Описание (для Claude)
        input_schema: JSON Schema входных параметров
        handler: Функция-обработчик
        group: Группа инструмента (filesystem, tables, и т.д.)
        annotations: Аннотации MCP (readOnlyHint, destructiveHint, и т.д.)
    """
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Awaitable[ToolResult]]
    group: str = "general"
    annotations: dict | None = None


class Engine:
    """Движок инструментов MCP-сервера.

    Регистрирует и выполняет инструменты. Claude общается с сервером через Engine.

    Attributes:
        tools: Реестр инструментов
    """

    def __init__(self, reactions=None, state_manager=None):
        """Инициализация движка.

        Args:
            reactions: Реестр реакций (D4) — если задан, ошибки движка
                собираются через server_reactions.yaml, а не хардкодом.
            state_manager: Менеджер состояния (D24) — для логирования facts в _SESSION_LOG.
        """
        self.tools: dict[str, ToolDefinition] = {}
        self.reactions = reactions
        self._state_manager = state_manager  # D24

    def _error(self, code: str, message: str, recovery: "Recovery") -> ToolResult:
        """Сборка ошибки: через реестр реакций, если он подключён (D4).

        Реестр даёт единый message_template/recovery по коду. Если кода нет
        в реестре или реестр не задан — используем переданный fallback.
        """
        if self.reactions is not None and self.reactions.get_reaction(code) is not None:
            return ToolResult(status="error", error=self.reactions.get_error(code, raw_message=message))
        return ToolResult(status="error", error=ErrorDetail(code=code, message=message, recovery=recovery))

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable[..., Awaitable[ToolResult]],
        group: str = "general",
        annotations: dict | None = None
    ):
        """Регистрация инструмента.

        Args:
            name: Имя инструмента (уникальное)
            description: Описание для Claude
            input_schema: JSON Schema входных параметров
            handler: Функция-обработчик
            group: Группа инструмента (filesystem, tables, и т.д.)
            annotations: Аннотации MCP (readOnlyHint, destructiveHint, и т.д.)
        """
        self.tools[name] = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            group=group,
            annotations=annotations
        )

    async def call(self, name: str, params: dict) -> ToolResult:
        """Вызов инструмента.

        Args:
            name: Имя инструмента
            params: Входные параметры

        Returns:
            ToolResult с результатом
        """
        if name not in self.tools:
            return self._error(
                "TOOL_NOT_FOUND",
                f"Инструмент '{name}' не найден",
                Recovery(suggested_tool="tools/list", reason="Посмотри доступные инструменты"),
            )

        tool = self.tools[name]

        # D5: валидация params против input_schema ДО вызова хендлера.
        # Раньше схема была декоративной (только витрина в tools/list).
        error = self._validate(tool, params)
        if error is not None:
            return error

        try:
            result = await tool.handler(**params)
            # D24: логируем facts в _SESSION_LOG (если state_manager подключён).
            if self._state_manager and isinstance(result, ToolResult) and result.facts:
                for fact in result.facts:
                    try:
                        self._state_manager.log_event(fact.type, fact.data)
                    except Exception:
                        pass  # логирование не должно ломать основной поток
            return result
        except TypeError as e:
            # D26: лишние/неизвестные params → VALIDATION_ERROR (клиентская ошибка),
            # а не INTERNAL_ERROR (серверная). Claude должен исправить params и повторить.
            return self._error(
                "VALIDATION_ERROR",
                f"Неверные параметры: {e}",
                Recovery(suggested_tool="tools/list", reason="Сверь параметры со схемой инструмента"),
            )
        except Exception as e:
            return self._error(
                "INTERNAL_ERROR",
                str(e),
                Recovery(reason="Внутренняя ошибка сервера"),
            )

    def _validate(self, tool: ToolDefinition, params: dict) -> ToolResult | None:
        """D5: проверка params по JSON Schema инструмента.

        Возвращает ToolResult(error) при несоответствии, иначе None.
        Использует jsonschema, если доступен; при его отсутствии —
        облегчённая проверка required/типа object, чтобы не падать.
        """
        schema = tool.input_schema or {}
        try:
            import jsonschema
            try:
                jsonschema.validate(instance=params, schema=schema)
            except jsonschema.ValidationError as e:
                return self._error(
                    "VALIDATION_ERROR",
                    f"Параметры не прошли валидацию схемы: {e.message}",
                    Recovery(suggested_tool="tools/list", reason="Исправь параметры по input_schema инструмента"),
                )
        except ImportError:
            # Fallback без зависимости: проверяем обязательные поля.
            missing = [k for k in schema.get("required", []) if k not in params]
            if missing:
                return self._error(
                    "VALIDATION_ERROR",
                    f"Отсутствуют обязательные параметры: {', '.join(missing)}",
                    Recovery(suggested_tool="tools/list", reason="Добавь обязательные параметры"),
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
