"""
core/state/__init__ — Точка входа для state

## Назначение
Из любого места сервера:
    from core.state import StateManager

## Архитектурные связи
- Использует: workspace/ (данные)
- Используется: tools/tables, tools/filesystem
"""

from .state_manager import StateManager

__all__ = ["StateManager"]
