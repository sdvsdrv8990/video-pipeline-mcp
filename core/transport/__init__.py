"""
core/transport/__init__ — Точка входа для transport

## Назначение
Из любого места сервера:
    from core.transport import Transport

## Архитектурные связи
- Используется: server.py (подключение к Claude)
"""

from .transport import Transport

__all__ = ["Transport"]
