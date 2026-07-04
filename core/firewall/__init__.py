"""
core/firewall/__init__ — точка входа для файрвола

## Назначение
Из любого места сервера:
    from core.firewall import Firewall, FirewallRequest, FirewallResult

## Архитектурные связи
- Использует: config/firewall.yaml (правила)
- Используется: server.py (перед ядром)
"""

from .firewall import Firewall
from .contracts import FirewallRequest, FirewallResult, FirewallDecision

__all__ = ["Firewall", "FirewallRequest", "FirewallResult", "FirewallDecision"]
