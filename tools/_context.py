"""
tools/_context.py — общие зависимости тонких обёрток инструментов.

## Зачем
Хендлеры были closures внутри `server.py::register_basic_tools` и резолвили движки
и хелперы `_err`/`_safe` по имени из объемлющей функции. При переезде группы в
`tools/<group>/` замыкание рвётся — все общие ссылки идут через `ToolContext`.

## Что внутри
- `ToolContext` — движки (данные/структура/шаблоны/связи) + `resolve`/`err`/`safe`.
- `build_context` — сборка контекста из `Engine`/`IDGenerator`/`StateManager`.
- `ANNOTATIONS_*` — аннотации MCP, общие для спек-листов всех групп.

Логики продукта здесь нет: контекст только раздаёт зависимости и маппит исключения
ядра в контракт `ToolResult`/`ErrorDetail` (единый путь через реестр реакций, B2/F43).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.contracts import ErrorDetail, Recovery, ToolResult
from core.engine import Engine, TableMaterializerError, TemplateEngine, TemplateError
from core.excel import ExcelEngine, ExcelError
from core.ids import ChainResolver, IDGenerator, LinkError, LinkRegistry, Taxonomy, TaxonomyError
from core.paths import PathEscapeError, safe_resolve
from core.state import StateManager
from core.tables import TableEngine, TableError

# Аннотации MCP для инструментов (помогают клиенту определить уровень доступа)
ANNOTATIONS_READONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
ANNOTATIONS_MODIFY = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
# Резерв, намеренно не назначен: destructiveHint триггерит auth-гейт коннектора Claude.ai.
ANNOTATIONS_DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}


@dataclass
class ToolContext:
    """Зависимости, общие для всех групп инструментов."""

    engine: Engine
    id_generator: IDGenerator
    state_manager: StateManager
    table_engine: TableEngine
    excel_engine: ExcelEngine
    template_engine: TemplateEngine
    link_registry: LinkRegistry
    workspace_path: Path
    config_path: Path

    def resolve(self, path: str) -> Path:
        """D1+D29: путь с containment внутри workspace/ (единая точка — core/paths, G17)."""
        return safe_resolve(path, self.workspace_path)

    @property
    def taxonomy(self) -> Taxonomy:
        """Объявленная иерархия типов (живёт в шаблонах, поднимается движком шаблонов)."""
        return self.template_engine.taxonomy

    @property
    def chain_resolver(self) -> ChainResolver:
        """Резолвер цепочки по каталогу назначения (S18-h) — собран из уже имеющихся частей."""
        return ChainResolver(self.workspace_path, self.link_registry, self.taxonomy)

    def err(self, code: str, message: str = "", reason: str = "",
            suggested_tool: str | None = None) -> ToolResult:
        """Ошибочный ToolResult через реестр реакций (yaml = источник class/recovery, B2/F43).

        Для кода из реестра class/message_template/recovery берутся из server_reactions.yaml
        (raw message сохраняет специфику). reason/suggested_tool — fallback лишь для кодов вне реестра.
        """
        if self.engine.reactions is not None and self.engine.reactions.get_reaction(code) is not None:
            return ToolResult(status="error", error=self.engine.reactions.get_error(code, raw_message=message))
        return ToolResult(status="error", error=ErrorDetail(
            code=code, message=message,
            recovery=Recovery(reason=reason, suggested_tool=suggested_tool)))

    def safe(self, call: Callable[[], Any]) -> tuple[bool, Any]:
        """Выполнить sync-вызов ядра, смаппив исключения в ToolResult.

        Returns (ok, value_or_error_result): при ok=False во втором элементе —
        готовый ошибочный ToolResult, иначе — результат ядра.
        """
        try:
            return True, call()
        except PathEscapeError:
            return False, self.err("PATH_ESCAPE", "Путь выходит за пределы workspace/.",
                                   "Используй путь ВНУТРИ workspace, без '..' и абсолютных путей.")
        except (TableError, ExcelError, TemplateError, TableMaterializerError, LinkError,
                TaxonomyError) as e:
            return False, self.err(e.code, e.message, e.reason, e.suggested_tool)
        except ValueError as e:
            # F37: не-путёвый ValueError из глубины core — честно INTERNAL_ERROR, не мислейбл PATH_ESCAPE.
            return False, self.err("INTERNAL_ERROR", f"Внутренняя ошибка: {e}")


def build_context(engine: Engine, id_generator: IDGenerator, state_manager: StateManager,
                  config_path: Path) -> ToolContext:
    """Собрать контекст: движки поднимаются здесь, группы их только используют."""
    workspace_path = state_manager.workspace_path
    return ToolContext(
        engine=engine,
        id_generator=id_generator,
        state_manager=state_manager,
        # Категория 3 (данные) — TableEngine поверх read.json/write.json.
        table_engine=TableEngine(state_manager, id_generator),
        # Категория 2 (структура) — ExcelEngine поверх .xlsx (openpyxl).
        excel_engine=ExcelEngine(workspace_path),
        # Ф1: композиция по ссылке + контроль глубины.
        template_engine=TemplateEngine(workspace_path, id_generator, config_path / "templates" / "workspace"),
        # Ф2: анонимные → ORPHAN, link() в одном месте.
        link_registry=LinkRegistry(workspace_path),
        workspace_path=workspace_path,
        config_path=config_path,
    )
