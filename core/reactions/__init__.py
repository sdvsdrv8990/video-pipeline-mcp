"""
core/reactions/__init__ — Точка входа для reactions

## Назначение
Из любого места сервера:
    from core.reactions import Reactions

## Архитектурные связи
- Использует: config/server_reactions.yaml (конфигурация)
- Использует: core.contracts (ErrorDetail, Recovery)
"""

from .reactions import Reactions

__all__ = ["Reactions"]
