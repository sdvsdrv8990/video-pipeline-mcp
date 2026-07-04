"""
core/ids/id_generator.py — Генератор ID

## Назначение
Генерация уникальных ID для фрагментов, строк, задач.

## 4 уровня анализа

### 1. Код
- IDGenerator класс с методами generate, get_prefix
- Генерирует ID по префиксу + случайная часть
- Формат: PREFIX_RANDOM

### 2. Поведение
- Сервер генерирует ID, не Claude
- ID уникальны в пределах типа
- ID не повторяются

### 3. Поток данных
```
Вызов → IDGenerator.generate(prefix) → "PREFIX_abc123"
```

### 4. Долгосрочный (6 мес)
- Все ID в системе генерируются через IDGenerator
- Формат стабилен
- Реестр связей растёт

## Порядок полей
1. Префиксы (prefixes)
2. Методы (generate, get_prefix)
"""

import uuid


class IDGenerator:
    """Генератор уникальных ID.

    Attributes:
        prefixes: Префиксы по типам сущностей
    """

    # Стандартные префиксы
    PREFIXES = {
        "video": "VID",
        "channel": "CH",
        "network": "NET",
        "niche": "NICHE",
        "asset": "AST",
        "variation": "VAR",
        "scene": "S",
        "render": "RENDER",
        "task": "TASK",
        "fact": "FACT",
        "technique": "TECH",
        "pattern": "PAT",
        "prediction": "PRED",
        "insight": "INS",
    }

    def __init__(self, prefixes: dict[str, str] | None = None):
        """Инициализация.

        Args:
            prefixes: Кастомные префиксы (опционально)
        """
        self.prefixes = prefixes or self.PREFIXES

    def generate(self, entity_type: str) -> str:
        """Генерация ID для сущности.

        Args:
            entity_type: Тип сущности (video, channel, etc.)

        Returns:
            Уникальный ID (PREFIX_abc123)
        """
        prefix = self.prefixes.get(entity_type, entity_type.upper())

        # Генерируем короткий уникальный код
        unique_part = self._generate_unique()

        return f"{prefix}_{unique_part}"

    def generate_simple(self, prefix: str) -> str:
        """Генерация ID с произвольным префиксом.

        Args:
            prefix: Префикс

        Returns:
            Уникальный ID
        """
        unique_part = self._generate_unique()
        return f"{prefix}_{unique_part}"

    def _generate_unique(self) -> str:
        """Генерация уникальной части ID.

        Returns:
            Уникальная hex-строка (32 символа)
        """
        # D9: НЕ усекаем хеш до 8 hex (32 бита) — по парадоксу дней рождения
        # коллизия ~50% уже на ~77k ID. Берём полный uuid4 (122 бита энтропии):
        # коллизии практически исключены даже без реестра-проверки.
        return uuid.uuid4().hex

    def get_prefix(self, entity_type: str) -> str:
        """Получение префикса для типа сущности.

        Args:
            entity_type: Тип сущности

        Returns:
            Префикс
        """
        return self.prefixes.get(entity_type, entity_type.upper())

    def is_valid_format(self, entity_id: str) -> bool:
        """Проверка формата ID.

        Args:
            entity_id: ID для проверки

        Returns:
            True если формат валидный (PREFIX_xxxxxxxx)
        """
        parts = entity_id.split("_", 1)
        if len(parts) != 2:
            return False
        prefix, unique = parts
        # D9: уникальная часть теперь uuid4.hex (32 hex-символа).
        return len(prefix) > 0 and len(unique) == 32 and all(c in "0123456789abcdef" for c in unique)
