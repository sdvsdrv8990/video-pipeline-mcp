"""tests/quick/test_packages.py — пакеты среды расчёта: суд «что уже стоит» без сети, действие только по плану.

Стоящее подставляется распределениями (`*.dist-info`) во временном каталоге на `sys.path`, установщик — записью вызова:
сети нет и на машину ничего не ставится. Отказы судятся через путь доставки (`Engine.refusal`), как их видит клиент.
"""

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.engine.engine import Engine
from core.packages import PackageError, Packages
from core.packages import manager
from core.reactions import Reactions

_checks = 0
_fails: list[str] = []


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def _dist(site: Path, name: str, version: str) -> None:
    """Стоящий пакет — так, как его видит importlib.metadata."""
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")


class _Installer:
    """Подмена установщика: записывает вызовы; по желанию «ставит» (кладёт dist-info) и отвечает кодом."""

    def __init__(self, site: Path, puts: dict[str, str], code: int = 0, said: str = ""):
        self.site, self.puts, self.code, self.said, self.calls = site, puts, code, said, []

    def __call__(self, command, **_):
        self.calls.append(command)
        if self.code == 0:
            for name, version in self.puts.items():
                _dist(self.site, name, version)
        return subprocess.CompletedProcess(command, self.code, "", self.said)


class _Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _project(groups: dict[str, list[str]]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="vpm-packages-"))
    lines = ["[project]", 'name = "проба"', 'version = "0"', "", "[project.optional-dependencies]"]
    lines += [f"{g} = [{', '.join(json.dumps(r, ensure_ascii=False) for r in reqs)}]" for g, reqs in groups.items()]
    (root / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


site = Path(tempfile.mkdtemp(prefix="vpm-site-"))
sys.path.insert(0, str(site))
engine = Engine(reactions=Reactions(ROOT / "config" / "server_reactions.yaml"))
project = _project({"probe": ["vpm-probe-alpha>=1.0", "vpm-probe-beta==2.0", "vpm-probe-gamma>=1; python_version < '3'"]})
packages = Packages(project)
_dist(site, "vpm-probe-alpha", "1.5")
_dist(site, "vpm-probe-beta", "1.0")
real_run = manager.subprocess.run

print("== 1. Суд без сети: стоит, не та версия, маркер не про эту среду ==")
seen = packages.judge(["probe"])
ok(seen["checked"] == 2, f"маркер не про эту среду — требования нет (сверено {seen['checked']})")
ok([p["requirement"] for p in seen["plan"]] == ["vpm-probe-beta==2.0"] and seen["plan"][0]["have"] == "1.0",
   f"в плане только то, что стоит не той версией ({seen['plan']})")

print("\n== 2. Всё на месте — исход «уже стоит», установщик не зван ==")
_dist(site, "vpm-probe-beta", "2.0")
for old in site.glob("vpm_probe_beta-1.0.dist-info"):
    for f in old.iterdir():
        f.unlink()
    old.rmdir()
installer = _Installer(site, {})
manager.subprocess.run = installer
try:
    got = packages.install(["probe"], announce=lambda _c: None)
except PackageError as e:
    got = {"outcome": f"отказ {e.code}"}
ok(got["outcome"] == "уже стоит" and not installer.calls,
   f"повтор по той же среде — пустой план, установщик не запущен ({got['outcome']}, вызовов {len(installer.calls)})")

print("\n== 3. План не пуст — ставится ровно план, команда печатается до запуска ==")
project2 = _project({"probe": ["vpm-probe-alpha>=1.0", "vpm-probe-delta>=3"]})
installer = _Installer(site, {"vpm-probe-delta": "3.1"})
manager.subprocess.run = installer
told: list[list[str]] = []
got = Packages(project2).install(["probe"], index="https://индекс.пример/simple", announce=told.append)
ok(got["outcome"] == "поставлено" and got["installed"] == ["vpm-probe-delta>=3"],
   f"поставлено ровно недостающее ({got})")
ok(len(installer.calls) == 1 and "vpm-probe-delta>=3" in installer.calls[0]
   and "vpm-probe-alpha>=1.0" not in installer.calls[0] and "--extra-index-url" in installer.calls[0],
   f"установщик получил только план и объявленный индекс ({installer.calls})")
ok(told == installer.calls, "команда названа до запуска — её можно повторить руками")

print("\n== 4. Установщик отказал — код и требование клиенту, его текст — в журнал ==")
project3 = _project({"probe": ["vpm-probe-epsilon>=1"]})
installer = _Installer(site, {}, code=1, said="ERROR: boom /home/owner/.cache/pip SECRET-TOKEN")
manager.subprocess.run = installer
journal = _Records()
logging.getLogger("core.packages.manager").addHandler(journal)
try:
    Packages(project3).install(["probe"])
    refused = None
except PackageError as e:
    refused = engine.refusal(e).error
logging.getLogger("core.packages.manager").removeHandler(journal)
said = (refused.message + " " + refused.recovery.reason) if refused else ""
ok(refused is not None and refused.code == "PACKAGE_INSTALL_FAILED" and "vpm-probe-epsilon>=1" in said and "1" in said,
   f"отказ кодом с требованием и кодом выхода ({said[:90]!r})")
ok("SECRET-TOKEN" not in said and "/home/owner" not in said, "вывод установщика клиенту не уходит")
ok(any("SECRET-TOKEN" in r.getMessage() for r in journal.records), "вывод установщика — в журнале сервера")

print("\n== 5. Установщик «успел», а среда не та — отказ, а не «поставлено» ==")
installer = _Installer(site, {})
manager.subprocess.run = installer
try:
    Packages(project3).install(["probe"])
    code = None
except PackageError as e:
    code = engine.refusal(e).error.code
ok(code == "PACKAGE_INSTALL_INCOMPLETE", f"суд после установки видит, что пакета нет ({code})")

print("\n== 6. Неизвестная группа — отказ называет объявленные ==")
try:
    packages.install(["нет-такой"])
    refused = None
except PackageError as e:
    refused = engine.refusal(e).error
ok(refused is not None and refused.code == "PACKAGE_GROUP_UNKNOWN" and "probe" in refused.recovery.reason
   and "нет-такой" in refused.message, f"названы и неизвестная, и объявленные группы ({refused.message if refused else ''})")

manager.subprocess.run = real_run
print(f"\nпакеты среды расчёта: проверок {_checks}, провалов {len(_fails)}")
sys.exit(1 if _fails else 0)
