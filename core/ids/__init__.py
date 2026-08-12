"""
core/ids/__init__ — Точка входа для генерации ID

## Назначение
Из любого места сервера:
    from core.ids import IDGenerator

## Архитектурные связи
- Используется: tools/filesystem, tools/tables, tools/video
"""

from .chain_resolver import ChainResolver
from .id_generator import IDGenerator
from .link_registry import LinkRegistry, LinkError
from .taxonomy import Taxonomy, TaxonomyError

__all__ = ["IDGenerator", "LinkRegistry", "LinkError", "Taxonomy", "TaxonomyError", "ChainResolver"]
