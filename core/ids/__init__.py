"""
core/ids/__init__ — Точка входа для генерации ID

## Назначение
Из любого места сервера:
    from core.ids import IDGenerator

## Архитектурные связи
- Используется: tools/filesystem, tools/tables, tools/video
"""

from .id_generator import IDGenerator

__all__ = ["IDGenerator"]
