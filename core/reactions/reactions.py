"""core/reactions/reactions.py — код отказа превращается в ответ клиенту.

Паспорт: зовут по проводу с сервера и прямым импортом из tools · условия reactions, code, UNKNOWN_ERROR ·
  не молчит · поток reaction_to_client.
Зовёт: ContractError, Declared (схема ServerReactions), ErrorDetail, Recovery.
Зовут: из кода никто. Реестр поднимают через пакет `core.reactions`.

Отсутствие реестра — отказ, а не пустой реестр: без него КАЖДЫЙ код выродился бы в неизвестную
ошибку, и класс реакции, по которому клиент выбирает поведение, пропал бы молча; присвоение только
после успеха, поэтому перечитывание битого держит прежний. Неизвестный код идёт той же дорогой, что
известный: класс, текст и рецепт всё равно приезжают из реестра, а не из кода.
"""

import logging
import string
from pathlib import Path

from core.contracts import ContractError, ErrorDetail, Recovery
from core.declaration import Declared
from core.declaration_schemas import ServerReactions

log = logging.getLogger(__name__)


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
        """Прочитать реестр. Отсутствие файла — отказ, а не пустой реестр: без него КАЖДЫЙ код
        вырождается в `UNKNOWN_ERROR`, и класс реакции, по которому клиент выбирает поведение,
        пропадает молча. Присвоение только после успеха — перечитывание битого держит прежний.

        Args:
            config_path: Путь к server_reactions.yaml
        """
        door = Declared(config_path, ContractError, "реакций сервера",
                        "Заведи config/server_reactions.yaml — коды отказов и рецепты объявлены там.", ServerReactions)
        door.model                              # строка реестра — по схеме: битая — DECLARATION_INVALID с путём ключа
        self.reactions = door.data

    def get_error(self, code: str, raw_message: str = "", raw_response: dict | None = None,
                  facts: dict | None = None, errors: list[dict] | None = None) -> ErrorDetail:
        """Отказ по коду в форме ответа — единственная сборка на сервере. Класс, текст и подсказка — из реестра, факты
        подставляются в текст и подсказку и едут полем; `raw_message` места — переходный, он стоит поверх шаблона.
        Неизвестный код идёт записью реестра UNKNOWN_ERROR: ключ записи — тот код, который получает клиент."""
        name = code if code in self.reactions else "UNKNOWN_ERROR"
        entry = self.reactions.get(name, {})
        recovery, facts = entry.get("recovery", {}), dict(facts or {})
        params = recovery.get("suggested_params")
        return ErrorDetail(
            code=name,
            reaction_class=entry.get("class", "unknown"),
            message=raw_message or _filled(entry.get("message_template", ""), facts, name),
            recovery=Recovery(
                suggested_tool=recovery.get("suggested_tool"),
                suggested_params={k: _filled(v, facts, name) if isinstance(v, str) else v
                                  for k, v in params.items()} if params else params,
                reason=_filled(recovery.get("reason", ""), facts, name),
            ),
            facts=facts,
            errors=list(errors or []),
            raw_response=raw_response,
        )

    def get_reaction(self, code: str) -> dict | None:
        """Получение rawData реакции.

        Args:
            code: Код ошибки

        Returns:
            Словарь реакции или None
        """
        return self.reactions.get(code)


def _filled(text: str, facts: dict, code: str) -> str:
    """Шаблон реестра с фактами отказа. Факта, которого шаблон ждёт, нет — разрыв реестра и места сборки: владелец
    видит его в журнале с кодом и именами, клиент — «—» на месте пропуска, а не фигурные скобки."""
    missing = sorted({f for _, f, _, _ in string.Formatter().parse(text) if f} - set(facts))
    if missing:
        log.error("шаблон реакции %s ждёт фактов, которых отказ не принёс: %s", code, ", ".join(missing))
    return text.format_map({**dict.fromkeys(missing, "—"), **facts})


def cli_text(exc: ContractError, registry: str | Path) -> str:
    """Отказ для терминала — той же сборкой, что ответ сервера: код, текст и подсказка реестра с фактами отказа; строка
    `recovery.owner` — что делает владелец у консоли (команда хоста, адрес объявления): клиенту сервера она не уходит."""
    reactions = Reactions(registry)
    detail = reactions.get_error(exc.code, exc.message, facts=exc.facts, errors=exc.errors)
    owner = ((reactions.get_reaction(detail.code) or {}).get("recovery") or {}).get("owner", "")
    text = f"✗ {detail.code}: {detail.message}\n  {detail.recovery.reason}"
    return text + (f"\n  {_filled(owner, dict(exc.facts), detail.code)}" if owner else "")
