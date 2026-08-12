"""
core/ids/id_generator.py — Генератор ID

## Назначение
Генерация уникальных ID для фрагментов, строк, задач.
"""

import uuid


class IDGenerator:
    """Генератор уникальных ID.

    Префиксов НЕ знает: таблица типов жила здесь и дублировала объявления шаблонов
    (F41/F46). Единственный источник — `core.ids.Taxonomy` (блок `id:` в `*.tpl.yaml`
    для узлов и `_file_classes.yaml` для файлов); сюда префикс приходит готовым.
    """

    def generate(self, entity_type: str) -> str:
        """Генерация ID по типу без объявления (fallback: тип как префикс).

        Для сущностей workspace префикс берут из `Taxonomy` и зовут `generate_simple`.
        """
        return self.generate_simple(entity_type.upper())

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
