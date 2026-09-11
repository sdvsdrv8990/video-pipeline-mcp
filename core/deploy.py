"""core/deploy.py — что установлено на этой машине.

Объявляются ДВА независимых факта: стоит ли здесь сервер и какой конец туннеля здесь работает.
Одной ролью они не выражаются — сочетание «сервер и серверный конец» в прежнем одномерном словаре
эквивалента не имело вовсе. Старый ключ переводится в эту пару, а не живёт рядом с ней: два списка
на одну правду расходятся молча. Отсутствие объявления — рабочая машина, а не поломка.
"""

from pathlib import Path

from core.contracts.errors import ContractError
from core.declaration import Declaration

ENDS = ("client", "server", "both", "none")

# Прежний словарь ролей: только вход перевода, читателей у него больше нет.
_LEGACY = {"local": (True, "client"), "server": (False, "server"), "both": (True, "both")}

_CONFIG = Path(__file__).resolve().parents[1] / "config" / "install.yaml"
_DEFAULT = (True, "client")


class DeployError(ContractError):
    """Отказ объявления установки (маппится обёрткой в ErrorDetail)."""


def _fail(что: str, причина: str) -> DeployError:
    return DeployError("SCHEMA_INVALID", что, reason=причина)


def install(path: str | Path = _CONFIG) -> tuple[bool, str]:
    """Пара «стоит ли MCP, какой конец туннеля». Нет файла — рабочая машина."""
    if not Path(path).exists():
        return _DEFAULT
    data = Declaration(
        path, DeployError, "установки",
        "Запусти ./install.sh — он объявит, что стоит на этой машине.").data

    if "role" in data and "mcp" not in data:
        роль = data["role"]
        if роль not in _LEGACY:
            raise _fail(f"В install.yaml роль `{роль}` неизвестна.",
                        f"Допустимые роли: {', '.join(_LEGACY)}.")
        return _LEGACY[роль]

    if "mcp" not in data or "tunnel" not in data:
        raise _fail("В install.yaml нет объявления установки.",
                    f"Нужны ключи `mcp` (да/нет) и `tunnel` ({'|'.join(ENDS)}). "
                    "Перезапусти ./install.sh.")
    mcp_, конец = data["mcp"], data["tunnel"]
    if not isinstance(mcp_, bool):
        raise _fail(f"В install.yaml `mcp: {mcp_}` — не да/нет.", "Допустимо true или false.")
    if конец not in ENDS:
        raise _fail(f"В install.yaml конец туннеля `{конец}` неизвестен.",
                    f"Допустимые концы: {', '.join(ENDS)}.")
    return bool(mcp_), str(конец)


def mcp(path: str | Path = _CONFIG) -> bool:
    """Объявлен ли на этой машине сервер MCP."""
    return install(path)[0]


def tunnel_end(path: str | Path = _CONFIG) -> str:
    """Какой конец туннеля работает на этой машине."""
    return install(path)[1]
