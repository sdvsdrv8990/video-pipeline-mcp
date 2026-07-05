"""
core/firewall/rules/rate_limiter.py — Ограничитель частоты

## Назначение
Ограничение количества запросов от одного IP в единицу времени.
"""

from collections import defaultdict
import time


class RateLimiterResult:
    """Результат проверки rate limiter.

    Attributes:
        allowed: Разрешён ли запрос
        remaining: Оставшихся запросов
        reset_in: Секунд до сброса
    """
    def __init__(self, allowed: bool, remaining: int = 0, reset_in: float = 0):
        self.allowed = allowed
        self.remaining = remaining
        self.reset_in = reset_in


class RateLimiter:
    """Ограничитель частоты запросов.

    Attributes:
        max_requests: Максимум запросов в минуту
        ban_after: Через сколько нарушений бан
        window_sec: Окно времени (сек)
    """

    def __init__(self, max_requests: int = 60, ban_after: int = 3, window_sec: float = 60):
        self.max_requests = max_requests
        self.ban_after = ban_after
        self.window_sec = window_sec

        # Состояние
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._violations: dict[str, int] = defaultdict(int)
        self._total_violations = 0

    def check(self, ip: str, timestamp: float) -> RateLimiterResult:
        """Проверка лимита запросов.

        Args:
            ip: IP адрес
            timestamp: Временная метка запроса

        Returns:
            RateLimiterResult
        """
        # Очищаем старые запросы
        self._cleanup(ip, timestamp)

        # Считаем запросы за окно
        requests_in_window = len(self._requests[ip])

        if requests_in_window >= self.max_requests:
            # Превышен лимит
            self._violations[ip] += 1
            self._total_violations += 1
            return RateLimiterResult(
                allowed=False,
                remaining=0,
                reset_in=self.window_sec
            )

        # Добавляем запрос
        self._requests[ip].append(timestamp)

        return RateLimiterResult(
            allowed=True,
            remaining=self.max_requests - requests_in_window - 1,
            reset_in=self.window_sec
        )

    def _cleanup(self, ip: str, current_time: float):
        """Очистка старых запросов и violations.

        D16: violations сбрасываются когда окно запросов полностью протухло
        (нет ни одного запроса за window_sec). Это делает unblock полезным
        и восстанавливает мягкий порог ban_after после стабильного периода.

        Args:
            ip: IP адрес
            current_time: Текущее время
        """
        cutoff = current_time - self.window_sec
        self._requests[ip] = [
            t for t in self._requests[ip]
            if t > cutoff
        ]
        # D16: сброс violations если окно пустое (все запросы протухли).
        if not self._requests[ip] and ip in self._violations:
            del self._violations[ip]

    def get_violations(self) -> int:
        """Получение общего количества нарушений.

        Returns:
            Количество нарушений
        """
        return self._total_violations

    def get_ip_violations(self, ip: str) -> int:
        """Получение количества нарушений по IP.

        Args:
            ip: IP адрес

        Returns:
            Количество нарушений
        """
        return self._violations.get(ip, 0)

    def should_ban(self, ip: str) -> bool:
        """Проверяет нужно ли забанить IP.

        Args:
            ip: IP адрес

        Returns:
            True если нужно забанить
        """
        return self._violations.get(ip, 0) >= self.ban_after
