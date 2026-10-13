"""core/packages/manager.py — пакеты среды расчёта: желаемое — группы проекта, стоящее — этот интерпретатор, план — разница.

Повторная установка не проверяется веткой, а не рождается: второй запуск по той же среде даёт пустой план сам собой, и
исход — «уже стоит» без запуска установщика. Суд — без сети (`importlib.metadata` того интерпретатора, в котором
исполнится сервер); вывод установщика — в журнал сервера, клиенту — код и факты.
"""

import importlib
import logging
import subprocess
import sys
import tomllib
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

from core.contracts import ContractError
from core.environment import child_spawn

log = logging.getLogger(__name__)


class PackageError(ContractError):
    """Отказ среды расчёта: код реестра (`config/server_reactions.yaml`) и факты."""


class Packages:
    """Группы необязательных зависимостей проекта против стоящего в `sys.executable`."""

    def __init__(self, project: str | Path):
        self.project = Path(project)
        declared = tomllib.loads((self.project / "pyproject.toml").read_text(encoding="utf-8"))
        self.groups: dict[str, list[str]] = declared["project"].get("optional-dependencies") or {}

    def wanted(self, names: list[str]) -> list[Requirement]:
        """Требования групп для этой среды: маркер не про неё — требования нет."""
        unknown = [n for n in names if n not in self.groups]
        if unknown:
            raise PackageError("PACKAGE_GROUP_UNKNOWN", facts={"groups": ", ".join(unknown),
                                                              "known": ", ".join(sorted(self.groups))})
        reqs = [Requirement(line) for n in names for line in self.groups[n]]
        return [r for r in reqs if r.marker is None or r.marker.evaluate()]

    def judge(self, names: list[str]) -> dict:
        """Вердикт по каждому требованию: не стоит, стоит не той версии, стоит. План — первые два."""
        wanted, plan = self.wanted(names), []
        for req in wanted:
            try:
                have = metadata.version(req.name)
            except metadata.PackageNotFoundError:
                have = None
            if have is None or not req.specifier.contains(have, prereleases=True):
                plan.append({"requirement": str(req), "have": have})
        return {"plan": plan, "checked": len(wanted)}

    def install(self, names: list[str], index: str = "", announce: Callable[[list[str]], None] = lambda _c: None) -> dict:
        """Поставить ровно план. Пустой план — исход «уже стоит» без установщика; после установки — снова суд."""
        before = self.judge(names)
        if not before["plan"]:
            return {"outcome": "уже стоит", "checked": before["checked"], "installed": []}
        planned = [p["requirement"] for p in before["plan"]]
        command = [sys.executable, "-m", "pip", "install", *planned, *(["--extra-index-url", index] if index else [])]
        announce(command)
        done = subprocess.run(command, cwd=self.project, capture_output=True, check=False, **child_spawn("replace"))
        if done.returncode != 0:
            log.error("установщик пакетов вернул %s: %s", done.returncode, (done.stderr or done.stdout)[-4000:])
            raise PackageError("PACKAGE_INSTALL_FAILED", facts={"exit_code": done.returncode,
                                                               "requirements": ", ".join(planned)})
        importlib.invalidate_caches()
        after = self.judge(names)
        if after["plan"]:
            raise PackageError("PACKAGE_INSTALL_INCOMPLETE",
                               facts={"requirements": ", ".join(p["requirement"] for p in after["plan"])})
        return {"outcome": "поставлено", "checked": after["checked"], "installed": planned}
