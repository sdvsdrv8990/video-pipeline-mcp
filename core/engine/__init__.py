"""
core/engine/__init__ — Точка входа для engine

## Назначение
Из любого места сервера:
    from core.engine import Engine

## Архитектурные связи
- Использует: core.contracts (ToolResult, ErrorDetail, Fact)
- Использует: core.reactions (маппинг ошибок)
- Используется: server.py (обработка запросов)
"""

from .engine import Engine
from .template_engine import TemplateEngine, TemplateError
from .table_materializer import TableMaterializer, TableMaterializerError
from .template_resolver import TemplateResolver

__all__ = ["Engine", "TemplateEngine", "TemplateError", "TableMaterializer", "TableMaterializerError",
           "TemplateResolver"]
