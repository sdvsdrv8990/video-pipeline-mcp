"""tests/test_suites.py — единый гейт: каждый набор = отдельный процесс, exit-код = вердикт (T0/F50).

Наборы написаны как самостоятельные скрипты (свой счётчик проверок + `sys.exit`). Импортировать их
в pytest нельзя: их `test_*`-функции ничего не ассертят, а тело модуля исполняется при импорте —
именно так и получался ложный зелёный. Поэтому гоняем подпроцессом и проверяем код возврата.

Наборы обнаруживаются автоматически: новый файл `tests/**/test_*.py` попадает в гейт сам,
без правки `ci.yml` (это и была вторая половина F50).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Наборы, которым нужен ЖИВОЙ сервер/туннель — отдельный маркер, вне основного гейта (до харнесса T2).
LIVE = {"test_firewall.py"}
# Adversarial-симуляции: маркер для селекции (`-m sim`), в гейте участвуют наравне.
SIM_DIRS = {"virus_injection", "cache_injection", "cache_overflow", "bot_army", "config_change",
            "render_draft_final", "agent_swarm"}
# Не наборы: этот раннер и его инфраструктура.
SKIP_FILES = {"test_suites.py", "conftest.py"}


def _discover() -> list[Path]:
    return sorted(p for p in (ROOT / "tests").rglob("test_*.py") if p.name not in SKIP_FILES)


def _params():
    for path in _discover():
        rel = path.relative_to(ROOT)
        marks = []
        if path.name in LIVE:
            marks.append(pytest.mark.live)
        if path.parent.name in SIM_DIRS:
            marks.append(pytest.mark.sim)
        yield pytest.param(rel, marks=marks, id=str(rel))


def _cmd(suite: Path) -> list[str]:
    """`VPM_COVERAGE=1` → набор гоняется под `coverage run --parallel-mode` (T1).

    Вся работа происходит в подпроцессе, поэтому обычный `--cov` мерит только родителя и
    показывает 0%. Явный флаг честнее авто-магии: `coverage combine && coverage report`
    после прогона. Настройки — `[tool.coverage.run]` в pyproject.
    """
    if os.environ.get("VPM_COVERAGE") == "1":
        return [sys.executable, "-m", "coverage", "run", "--parallel-mode", str(suite)]
    return [sys.executable, str(suite)]


@pytest.mark.parametrize("suite", list(_params()))  # pytest 10 снимет поддержку генераторов
def test_suite_passes(suite: Path, repo_root: Path):
    """Набор отрабатывает с exit 0; иначе — его stdout попадает в отчёт."""
    p = subprocess.run(_cmd(suite), cwd=repo_root, capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, f"{suite} exit={p.returncode}\n{p.stdout[-4000:]}\n{p.stderr[-2000:]}"


def test_discovery_is_not_empty():
    """Гейт не должен «позеленеть» из-за того, что наборы перестали находиться."""
    assert len(_discover()) >= 12
