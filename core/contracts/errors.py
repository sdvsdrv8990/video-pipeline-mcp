"""core/contracts/errors.py — база отказов ядра.

Зовут: contracts, deploy.

Подсистема отказывает КОДОМ реестра, а не текстом: обёртка ловит его и собирает отказ клиенту.
Форма одна на всех — иначе часть зон молча теряет поле, и отказ приходит клиенту беднее.
"""


class ContractError(Exception):
    """Отказ подсистемы в форме контракта: код из `config/server_reactions.yaml` и факты для его шаблона.

    Наследник добавляет только ИМЯ: по нему обёртка отличает свою зону от чужой и не глотает
    чужие исключения. Текст и подсказку даёт реестр по коду, подставляя `facts`; `errors` — ошибки ввода
    `[{field, rule}]`. `message`, `reason`, `suggested_tool` места — переходные: для кода из реестра `reason` и
    `suggested_tool` клиенту не доходят, их счёт ведёт опись мест сборки до нуля.
    """

    def __init__(self, code: str, message: str = "", reason: str = "",
                 suggested_tool: str | None = None, *, facts: dict | None = None,
                 errors: list[dict] | None = None):
        super().__init__(message or code)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool
        self.facts = dict(facts or {})
        self.errors = list(errors or [])

    def with_facts(self, **facts) -> "ContractError":
        """Дополнить отказ фактами слоя, который их знает (лист и строка книги, инструмент), не затирая названные
        местом отказа: `raise e.with_facts(sheet=…, row=…)` — как `add_note`, но фактами для шаблона и клиента."""
        for name, value in facts.items():
            self.facts.setdefault(name, value)
        return self
