"""tests/test_suites.py — единый гейт: каждый набор = отдельный процесс, exit-код = вердикт.

Наборы написаны как самостоятельные скрипты (свой счётчик проверок + `sys.exit`). Импортировать их
в pytest нельзя: их `test_*`-функции ничего не ассертят, а тело модуля исполняется при импорте —
именно так и получался ложный зелёный. Поэтому гоняем подпроцессом и проверяем код возврата.

Наборы обнаруживаются автоматически: новый файл `tests/**/test_*.py` попадает в гейт сам,
без правки `ci.yml`.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.observability import Logs

ROOT = Path(__file__).resolve().parents[1]

# Наборы, которым нужен ЖИВОЙ сервер/туннель. Пусто: харнесс `tests/harness` поднимает
# сервер сам — свой порт, своя временная область, настоящий ключ, — поэтому «живые» наборы больше
# не требуют запущенного вручную сервера и идут в общем гейте наравне.
LIVE: set[str] = set()
# Adversarial-симуляции: маркер для селекции (`-m sim`), в гейте участвуют наравне.
SIM_DIRS = {"virus_injection", "cache_injection", "cache_overflow", "bot_army", "config_change",
            "agent_swarm"}
# Не наборы: этот раннер и его инфраструктура.
SKIP_FILES = {"test_suites.py", "conftest.py"}
# Не наборы: харнесс — библиотека для наборов, а не набор (в нём нет своих проверок).
SKIP_DIRS = {"harness"}
# Как наборы-скрипты называют провал: `✗` у ok()/check(), `[FAIL]` у аудита, сводки «ПРОВАЛЫ»/«FAILED», трасса.
FAIL_MARKS = ("✗", "[FAIL]", "ПРОВАЛЫ", "FAILED", "Traceback", "Error:")


def _discover() -> list[Path]:
    return sorted(p for p in (ROOT / "tests").rglob("test_*.py")
                  if p.name not in SKIP_FILES and not (set(p.parts) & SKIP_DIRS))


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
    """`VPM_COVERAGE=1` → набор гоняется под `coverage run --parallel-mode`.

    Вся работа происходит в подпроцессе, поэтому обычный `--cov` мерит только родителя и
    показывает 0%. Явный флаг честнее авто-магии: `coverage combine && coverage report`
    после прогона. Настройки — `[tool.coverage.run]` в pyproject.
    """
    if os.environ.get("VPM_COVERAGE") == "1":
        return [sys.executable, "-m", "coverage", "run", "--parallel-mode", str(suite)]
    return [sys.executable, str(suite)]


def _failure(suite: Path, p: subprocess.CompletedProcess) -> str:
    """Отчёт о провале набора: строки провала из всего вывода с номерами и хвост — упавшая проверка видна, где бы ни стояла."""
    lines = f"{p.stdout}\n{p.stderr}".splitlines()
    marked = [f"{n}: {line}" for n, line in enumerate(lines, 1) if any(m in line for m in FAIL_MARKS)]
    return (f"{suite} exit={p.returncode}\nстроки провала ({len(marked)}):\n" + "\n".join(marked[:300])
            + f"\n… хвост вывода:\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")


@pytest.mark.parametrize("suite", list(_params()))  # pytest 10 снимет поддержку генераторов
def test_suite_passes(suite: Path, repo_root: Path, suite_env: dict[str, str], owner_logs: dict):
    """Набор отрабатывает с exit 0; иначе — строки его провала и хвост вывода попадают в отчёт."""
    p = subprocess.run(_cmd(suite), cwd=repo_root, capture_output=True, text=True, timeout=900, env=suite_env)
    assert p.returncode == 0, _failure(suite, p)


@pytest.fixture(scope="session")
def suite_env(tmp_path_factory) -> dict[str, str]:
    """Среда наборов: свой корень журналов и свой дом инстанса. Сторож последней линии к двери `tests/harness/inprocess.py`.
    UTF-8 Mode (PEP 540): вывод набора уходит в канал, а у канала Windows кодировка локали — cp1252, и русский print
    ронял набор. Сервер свой вывод решает сам (`server.py` main), его событие идёт без этой переменной."""
    return {**os.environ, "MCP_LOGS": os.environ.get("MCP_LOGS") or str(tmp_path_factory.mktemp("logs")),
            "MCP_HOME": os.environ.get("MCP_HOME") or str(tmp_path_factory.mktemp("home")), "PYTHONUTF8": "1"}


@pytest.fixture(scope="session")
def owner_logs() -> dict[str, tuple[int, int]]:
    """Снимок каталога журналов владельца до первого набора."""
    return _owner_logs()


def _owner_logs() -> dict[str, tuple[int, int]]:
    """Каталог журналов владельца (корень по умолчанию, без MCP_LOGS): файл → (размер, время правки)."""
    root = Logs.from_config(ROOT / "config", None).root
    if root is None or not root.is_dir():
        return {}
    return {str(f.relative_to(root)): (f.stat().st_size, f.stat().st_mtime_ns) for f in root.rglob("*") if f.is_file()}


def test_discovery_is_not_empty():
    """Гейт не должен «позеленеть» из-за того, что наборы перестали находиться."""
    assert len(_discover()) >= 12


def test_failure_names_check_deep_in_output(tmp_path: Path, repo_root: Path):
    """Провал на 5000-й строке из 6000 назван в отчёте гейта с номером строки, а сводка хвоста осталась рядом."""
    stub = tmp_path / "stub_suite.py"
    stub.write_text("import sys\n"
                    "for i in range(1, 6001):\n"
                    "    print('  ✗ проверка-иголка' if i == 5000 else f'  ✓ строка {i}')\n"
                    "print('РЕЗУЛЬТАТ: 5999/6000 прошло')\n"
                    "sys.exit(1)\n", encoding="utf-8")
    p = subprocess.run(_cmd(stub), cwd=repo_root, capture_output=True, text=True, timeout=60)
    report = _failure(stub, p)
    assert p.returncode == 1 and "5000:   ✗ проверка-иголка" in report and "РЕЗУЛЬТАТ: 5999/6000" in report, report[:600]
    assert "проверка-иголка" not in f"{p.stdout[-4000:]}", "заглушка обязана прятать провал за прежней обрезкой"


def test_server_built_through_harness_door():
    """Ни один набор не собирает сервер мимо `tests/harness/inprocess.py`: забытый вызов вернул бы запись наборов в журналы
    владельца и чтение его личности при прямом запуске. Имя модуля берётся из импорта файла, а не угадывается."""
    door = ROOT / "tests" / "harness" / "inprocess.py"
    builders = {"create_server", "register_basic_tools"}
    bypass = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        if path == door:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
                   if a.name == "server"}
        for node in ast.walk(tree):
            direct = (isinstance(node, ast.ImportFrom) and node.module == "server"
                      and any(a.name in builders for a in node.names))
            through = (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and node.func.attr in builders and isinstance(node.func.value, ast.Name)
                       and node.func.value.id in aliases)
            if direct or through:
                bypass.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not bypass, f"сборка сервера мимо двери tests/harness/inprocess.py: {bypass}"


def test_direct_run_writes_no_owner_logs(tmp_path: Path, repo_root: Path):
    """Набор, запущенный руками без MCP_LOGS, не создаёт журналов владельца. Владелец — подменённые каталоги пользователя
    (HOME, XDG_STATE_HOME, LOCALAPPDATA); ожидаемый корень считает тот же platformdirs в той же среде."""
    env = {k: v for k, v in os.environ.items() if k != "MCP_LOGS"} | {
        "HOME": str(tmp_path / "home"), "XDG_STATE_HOME": str(tmp_path / "state"), "LOCALAPPDATA": str(tmp_path / "local")}
    probe = subprocess.run([sys.executable, "-c", "from core.observability import Logs; "
                            "print(Logs.from_config('config', None).root)"],
                           cwd=repo_root, env=env, capture_output=True, text=True, timeout=60)
    owner = Path(probe.stdout.strip())
    assert probe.returncode == 0 and owner.is_relative_to(tmp_path), \
        f"корень журналов по умолчанию не подменился средой на этой ОС — замер невозможен: {owner} {probe.stderr[-300:]}"
    for suite in (Path("tests/quick/test_audit_fixes.py"), Path("tests/cache_overflow/test_cache_overflow.py")):
        p = subprocess.run(_cmd(suite), cwd=repo_root, env=env, capture_output=True, text=True, timeout=900)
        assert p.returncode == 0, _failure(suite, p)
    assert not owner.exists(), f"набор без MCP_LOGS создал журналы владельца: {sorted(map(str, owner.rglob('*')))}"


def test_owner_logs_untouched(owner_logs: dict):
    """Последним: за прогон наборов каталог журналов владельца не изменился — тесты пишут только в свой корень."""
    after = _owner_logs()
    changed = sorted(k for k in set(owner_logs) | set(after) if owner_logs.get(k) != after.get(k))
    assert not changed, f"наборы тронули журналы владельца: {changed}"
