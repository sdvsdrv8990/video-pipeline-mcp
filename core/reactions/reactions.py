"""
core/reactions/reactions.py — Чтение и маппинг реакций

## Назначение
Чтение server_reactions.yaml и маппинг ошибок на ErrorDetail.
"""

import yaml
from pathlib import Path

from core.contracts import ErrorDetail, Recovery


class Reactions:
    """Читалка и маппер реакций сервера.

    Attributes:
        reactions: Словарь реакций из YAML
    """

    def __init__(self, config_path: str | Path | None = None):
        """Инициализация.

        Args:
            config_path: Путь к server_reactions.yaml
        """
        self.reactions: dict = {}

        if config_path:
            self.load(config_path)

    def load(self, config_path: str | Path):
        """Загрузка реакций из YAML.

        Args:
            config_path: Путь к server_reactions.yaml
        """
        path = Path(config_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                self.reactions = yaml.safe_load(f) or {}

    def get_error(self, code: str, raw_message: str = "", raw_response: dict | None = None) -> ErrorDetail:
        """Получение ErrorDetail по коду ошибки.

        Args:
            code: Код ошибки (из server_reactions.yaml)
            raw_message: Полный текст ошибки
            raw_response: Оригинальный ответ API

        Returns:
            ErrorDetail с recovery
        """
        if code in self.reactions:
            reaction = self.reactions[code]
            recovery_data = reaction.get("recovery", {})

            return ErrorDetail(
                code=code,
                reaction_class=reaction.get("class", "unknown"),
                message=raw_message or reaction.get("message_template", ""),
                recovery=Recovery(
                    suggested_tool=recovery_data.get("suggested_tool"),
                    suggested_params=recovery_data.get("suggested_params"),
                    reason=recovery_data.get("reason", "")
                ),
                raw_response=raw_response
            )

        # Неизвестная ошибка → DEFAULT
        default = self.reactions.get("DEFAULT", {})
        return ErrorDetail(
            code="UNKNOWN_ERROR",
            message=raw_message or "Непредвиденная ошибка",
            recovery=Recovery(
                reason=default.get("recovery", {}).get("reason", "Обратитесь к администратору")
            ),
            raw_response=raw_response
        )

    def get_reaction(self, code: str) -> dict | None:
        """Получение rawData реакции.

        Args:
            code: Код ошибки

        Returns:
            Словарь реакции или None
        """
        return self.reactions.get(code)

    def list_codes(self) -> list[str]:
        """Получение списка всех кодов ошибок.

        Returns:
            Список кодов
        """
        return [code for code in self.reactions.keys() if code != "DEFAULT"]
