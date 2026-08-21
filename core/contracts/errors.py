"""
core/contracts/errors.py — база отказов ядра

## Назначение
Подсистема отказывает КОДОМ реестра, а не текстом: обёртка ловит и собирает `ErrorDetail`.
Форма одна на всех — иначе часть зон молча теряет поле, и отказ приходит клиенту беднее.
"""


class ContractError(Exception):
    """Отказ подсистемы в форме контракта: код из `config/server_reactions.yaml` + пояснение.

    Наследник добавляет только ИМЯ: по нему обёртка отличает свою зону от чужой и не глотает
    чужие исключения. Поля общие, поэтому `suggested_tool` доступен каждой зоне одинаково.
    """

    def __init__(self, code: str, message: str, reason: str = "",
                 suggested_tool: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool
