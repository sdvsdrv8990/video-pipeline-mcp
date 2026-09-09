"""
core/deploy.py — роль машины из config/install.yaml: что поднимает ./run.sh.

## Назначение
Одно место читает объявленный режим установки (local/server/both). install.sh пишет файл
голым bash (venv ещё нет), run.sh читает роль отсюда.

## Границы
- секретов тут не бывает: данные сервера/домен/ключи — config/deploy.local.yaml, вне git;
- неизвестная роль — код реестра (SCHEMA_INVALID), а не тихий дефолт; нет файла — роль
  `local` (текущее поведение по умолчанию): отсутствие выбора это не поломка.
"""

from pathlib import Path

from core.contracts.errors import ContractError
from core.declaration import Declaration

ROLES = ("local", "server", "both")
_CONFIG = Path(__file__).resolve().parents[1] / "config" / "install.yaml"


class DeployError(ContractError):
    """Отказ объявления режима установки (маппится обёрткой в ErrorDetail)."""


def role(path: str | Path = _CONFIG) -> str:
    """Объявленная роль машины. Нет файла — `local`; роль вне ROLES — громкий отказ."""
    if not Path(path).exists():
        return "local"
    decl = Declaration(
        path, DeployError, "режима установки",
        "Запусти ./install.sh — он объявит роль машины в config/install.yaml.")
    value = decl.need("role")
    if value not in ROLES:
        raise DeployError(
            "SCHEMA_INVALID", f"В install.yaml роль `{value}` неизвестна.",
            reason=f"Допустимые роли: {', '.join(ROLES)}.")
    return str(value)
