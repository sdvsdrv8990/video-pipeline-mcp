"""scripts/door_deps.py — требования базовых зависимостей проекта по именам, только stdlib: машине без MCP установщик
ставит минимальный .venv — пакеты двери загрузки, — а версии берёт из того же pyproject.toml, что и полная установка.

Запуск: python3 scripts/door_deps.py ИМЯ… — по требованию на строку (для `pip install -r`). Имени нет в
[project.dependencies] — отказ с перечнем: список установщика не расходится с pyproject молча.
"""
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _normal(name: str) -> str:
    """Имя пакета по PEP 503: регистр и разделители `-_.` не различают пакеты."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirements(names: list[str]) -> list[str]:
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    by_name = {_normal(re.split(r"[\s<>=!~;\[(]", line, maxsplit=1)[0]): line for line in declared}
    missing = [name for name in names if _normal(name) not in by_name]
    if missing:
        raise SystemExit(f"нет в [project.dependencies]: {', '.join(missing)} — объявлены: {', '.join(sorted(by_name))}")
    return [by_name[_normal(name)] for name in names]


if __name__ == "__main__":
    print("\n".join(requirements(sys.argv[1:])))
