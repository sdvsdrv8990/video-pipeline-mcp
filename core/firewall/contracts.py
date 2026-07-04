"""
core/firewall/contracts.py — Контракты файрвола

## Назначение
Модели данных файрвола: FirewallRequest, FirewallResult, FirewallDecision.

## Причина выноса
Избегаем циклических импортов: rules/ используют эти модели, а firewall.py использует rules/.

## Порядок (сверху вниз по зависимостям)
1. FirewallDecision — enum решений (базовый)
2. FirewallRequest — входной запрос (базовый)
3. FirewallResult — результат проверки (использует FirewallDecision)
"""

from enum import Enum
from dataclasses import dataclass


class FirewallDecision(Enum):
    """Решение файрвола."""
    ALLOW = "allow"
    BLOCK = "block"
    RATE_LIMIT = "rate_limit"


@dataclass
class FirewallRequest:
    """Запрос для проверки файрволом.

    Attributes:
        ip: IP адрес отправителя
        method: JSON-RPC метод
        params: Параметры запроса
        timestamp: Временная метка
    """
    ip: str
    method: str
    params: dict
    timestamp: float


@dataclass
class FirewallResult:
    """Результат проверки файрвола.

    Attributes:
        decision: Решение (allow/block/rate_limit)
        reason: Причина (для логирования и ответа)
    """
    decision: FirewallDecision
    reason: str = ""
