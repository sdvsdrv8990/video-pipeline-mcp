"""core/firewall/contracts.py — модели фильтра: запрос, решение, исход.

Вынесены отдельно из-за направления импорта: правила берут модели, а сам фильтр берёт правила —
общий модуль разрывает круг.
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
    """Запрос для проверки файрволом."""

    ip: str
    method: str
    params: dict
    timestamp: float


@dataclass
class FirewallResult:
    """Результат проверки файрвола.

    Attributes:
        decision: Решение (allow/block/rate_limit)
        reason: Причина для консоли и следа: в ней адрес клиента, в ответ клиенту она не идёт
        rule: Сработавшее правило — имя его раздела в firewall.yaml (факт отказа для клиента)
        retry_after_sec: Через сколько секунд повторять — срок лимитера; у остальных решений 0
    """
    decision: FirewallDecision
    reason: str = ""
    rule: str = ""
    retry_after_sec: int = 0
