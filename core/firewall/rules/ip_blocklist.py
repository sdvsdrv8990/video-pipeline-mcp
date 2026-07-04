"""
core/firewall/rules/ip_blocklist.py — Блок-лист IP

## Назначение
Хранение и управление списком заблокированных IP.

## 4 уровня анализа

### 1. Код
- IPBlocklist класс с методами block/unblock/is_blocked
- Автоматический бан при превышении лимита
- Временный бан с авто-разблокировкой

### 2. Поведение
- Проверяет забанен ли IP
- Банит автоматически или вручную
- Разблокирует по истечении времени

### 3. Поток данных
```
IP → IPBlocklist.is_blocked()
   ├── False → пропускаем
   └── True → блокируем
```

### 4. Долгосрочный (6 мес)
- Блок-лист растёт на основе атак
- Автоматическая очистка старых банов

## Порядок полей
1. Конфигурация (auto_ban, ban_duration)
2. Состояние (blocked_ips)
3. Методы (block, unblock, is_blocked)
"""

import time
from typing import Optional


class BlockedIP:
    """Заблокированный IP.

    Attributes:
        ip: IP адрес
        reason: Причина блокировки
        blocked_at: Время блокировки
        expires_at: Время разблокировки (None = навсегда)
    """

    def __init__(self, ip: str, reason: str = "", blocked_at: float = 0, expires_at: Optional[float] = None):
        self.ip = ip
        self.reason = reason
        self.blocked_at = blocked_at or time.time()
        self.expires_at = expires_at


class IPBlocklist:
    """Блок-лист IP адресов.

    Attributes:
        auto_ban: Автоматический бан при нарушениях
        ban_duration_hours: Длительность бана (часы)
    """

    def __init__(self, auto_ban: bool = True, ban_duration_hours: int = 24):
        self.auto_ban = auto_ban
        self.ban_duration_hours = ban_duration_hours

        # Состояние
        self._blocked: dict[str, BlockedIP] = {}

    def is_blocked(self, ip: str) -> bool:
        """Проверяет забанен ли IP.

        Args:
            ip: IP адрес

        Returns:
            True если забанен
        """
        if ip not in self._blocked:
            return False

        blocked = self._blocked[ip]

        # Проверяем не истёк ли бан
        if blocked.expires_at and time.time() > blocked.expires_at:
            # Бан истёк → удаляем
            del self._blocked[ip]
            return False

        return True

    def block(self, ip: str, reason: str = ""):
        """Блокировка IP.

        Args:
            ip: IP для блокировки
            reason: Причина блокировки
        """
        now = time.time()
        expires_at = now + (self.ban_duration_hours * 3600) if self.ban_duration_hours else None

        self._blocked[ip] = BlockedIP(
            ip=ip,
            reason=reason,
            blocked_at=now,
            expires_at=expires_at
        )

    def unblock(self, ip: str):
        """Разблокировка IP.

        Args:
            ip: IP для разблокировки
        """
        if ip in self._blocked:
            del self._blocked[ip]

    def get_blocked_count(self) -> int:
        """Получение количества заблокированных IP.

        Returns:
            Количество заблокированных
        """
        # Очищаем истёкшие
        self._cleanup_expired()
        return len(self._blocked)

    def get_blocked_list(self) -> list[dict]:
        """Получение списка заблокированных IP.

        Returns:
            Список словарей с информацией о банах
        """
        self._cleanup_expired()
        return [
            {
                "ip": b.ip,
                "reason": b.reason,
                "blocked_at": b.blocked_at,
                "expires_at": b.expires_at
            }
            for b in self._blocked.values()
        ]

    def _cleanup_expired(self):
        """Очистка истёкших банов."""
        now = time.time()
        expired = [
            ip for ip, blocked in self._blocked.items()
            if blocked.expires_at and now > blocked.expires_at
        ]
        for ip in expired:
            del self._blocked[ip]
