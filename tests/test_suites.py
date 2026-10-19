"""tests/test_suites.py — единый гейт: каждый набор = отдельный процесс, exit-код = вердикт.

Наборы написаны как самостоятельные скрипты (свой счётчик проверок + `sys.exit`). Импортировать их
в pytest нельзя: их `test_*`-функции ничего не ассертят, а тело модуля исполняется при импорте —
именно так и получался ложный зелёный. Поэтому гоняем подпроцессом и проверяем код возврата.

Наборы обнаруживаются автоматически: новый файл `tests/**/test_*.py` попадает в гейт сам,
без правки `ci.yml`.
"""

import ast
import os
import signal
import subprocess
import sys
import tomllib
from itertools import takewhile
from pathlib import Path

import pytest

from core.observability import Logs

ROOT = Path(__file__).resolve().parents[1]
# Граница одного набора — одно объявление (`[tool.vpm] suite_timeout_sec`), его же читает судья мутантов.
SUITE_TIMEOUT_SEC = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["vpm"]["suite_timeout_sec"]

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
# Как наборы-скрипты называют провал — началом строки: `✗` у ok()/check(), `[FAIL]` у аудита, `FAILED` сводки, трасса.
# Те же слова внутри строки — текст проверки («→ AUTH_FAILED», «в консоли НЕТ /Traceback/»), а не провал.
FAIL_MARKS = ("✗", "[FAIL]", "FAILED", "Traceback", "Error:")
# Свой список провалов набора (после итога): его пункты — до пустой строки.
FAILS_HEAD = "ПРОВАЛЫ:"
# Своя строка итога набора — счёт проверок.
SUMMARY_MARKS = ("РЕЗУЛЬТАТ:", "ИТОГО")


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
    """Отчёт о провале набора с номерами строк и хвостом — упавшая проверка видна, где бы ни стояла. Провалы — пункты
    своего списка «ПРОВАЛЫ:»; нет списка — строки, которые начинаются знаком провала; нет и их — своя строка итога."""
    lines = list(enumerate(f"{p.stdout}\n{p.stderr}".splitlines(), 1))
    heads = [i for i, (_, line) in enumerate(lines) if line.strip() == FAILS_HEAD]
    listed = [] if not heads else list(takewhile(lambda nl: nl[1].strip(), lines[heads[-1] + 1:]))
    marked = [(n, line) for n, line in lines if line.lstrip().startswith(FAIL_MARKS)]
    summary = [(n, line) for n, line in lines if line.lstrip("= ").startswith(SUMMARY_MARKS)]
    title, named = (("провалы по списку набора", listed) if listed else ("строки провала", marked) if marked
                    else ("строк провала нет — итог набора", summary))
    return (f"{suite} exit={p.returncode}\n{title} ({len(named)}):\n" + "\n".join(f"{n}: {line}" for n, line in named[:300])
            + f"\n… хвост вывода:\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")


@pytest.mark.parametrize("suite", list(_params()))  # pytest 10 снимет поддержку генераторов
def test_suite_passes(suite: Path, repo_root: Path, suite_env: dict[str, str], owner_logs: dict):
    """Набор отрабатывает с exit 0; иначе — строки его провала и хвост вывода попадают в отчёт."""
    # Вывод наборов — UTF-8 при любой локали: на Windows (cp1252) поток читался в None, и падал уже отчёт о провале.
    proc = subprocess.Popen(_cmd(suite), cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", env=suite_env)
    try:
        out, err = proc.communicate(timeout=SUITE_TIMEOUT_SEC)
        hung = ""
    except subprocess.TimeoutExpired:
        out, err, hung = _stack_of_hung(proc)
    p = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    assert not hung and p.returncode == 0, hung + _failure(suite, p)


def _stack_of_hung(proc: subprocess.Popen) -> tuple[str, str, str]:
    """Набор не кончился за срок: на POSIX SIGABRT — faulthandler набора (PYTHONFAULTHANDLER) печатает стек всех потоков,
    и отчёт называет место зависания; на Windows сигнала нет — снятие, как у subprocess.run."""
    if os.name == "posix":
        proc.send_signal(signal.SIGABRT)
    else:
        proc.kill()
    try:
        out, err = proc.communicate(timeout=60)    # стек печатается сразу; держать трубу могут только внуки набора
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    return out, err, f"набор не кончился за {SUITE_TIMEOUT_SEC} с — стек зависания (faulthandler) в выводе ниже\n"


@pytest.fixture(scope="session")
def suite_env(tmp_path_factory) -> dict[str, str]:
    """Среда наборов: свой корень журналов и свой дом инстанса. Сторож последней линии к двери `tests/harness/inprocess.py`.
    UTF-8 Mode (PEP 540): вывод набора уходит в канал, а у канала Windows кодировка локали — cp1252, и русский print
    ронял набор. Сервер свой вывод решает сам (`server.py` main), его событие идёт без этой переменной."""
    return {**os.environ, "MCP_LOGS": os.environ.get("MCP_LOGS") or str(tmp_path_factory.mktemp("logs")),
            "MCP_HOME": os.environ.get("MCP_HOME") or str(tmp_path_factory.mktemp("home")), "PYTHONUTF8": "1",
            "PYTHONFAULTHANDLER": "1"}   # зависший набор на SIGABRT печатает стек (_stack_of_hung)


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


# Формы вывода наборов из CI 72a3b0f: named — обязано попасть в провалы отчёта, quiet — не обязано (проходящая строка
# со знаком провала внутри текста, дубль списка). Новая форма — строка здесь.
FAILURE_CASES = [
    pytest.param("  ✓ insp_copy · 3. excel_copy_sheet → в консоли НЕТ /Traceback/\n"
                 "  ✓ obs_identity · 1. tools/list → AUTH_FAILED\n"
                 "— rcode_net_401: отказ ключа у провайдера своим кодом, а не AUTH_FAILED сервера\n"
                 "  ✗ rcode_table · 2. table_find_row → INVALID_ACTION → пришло VALIDATION_ERROR\n"
                 "РЕЗУЛЬТАТ: 3/4 прошло\nПРОВАЛЫ:\n  - rcode_table · 2. table_find_row → INVALID_ACTION\n", "",
                 ["- rcode_table · 2. table_find_row"], ["НЕТ /Traceback/", "AUTH_FAILED", "✗ rcode_table"],
                 id="list-after-summary"),
    pytest.param("[PASS] чужой ключ → AUTH_FAILED\n[PASS] суррогаты не роняют обработчик → AUTH_FAILED - AUTH_FAILED\n"
                 "[FAIL] каждое исключение зоны наследует ContractError\nИТОГО регрессий: 2/3 проверок пройдено\n", "",
                 ["[FAIL] каждое исключение зоны"], ["[PASS]"], id="status-marks-without-list"),
    pytest.param("  ✓ шаг 1 → FAILED у провайдера назван\n",
                 "Traceback (most recent call last):\n  File \"suite.py\", line 3, in <module>\nValueError: сломано\n",
                 ["Traceback (most recent call last)"], ["✓ шаг 1"], id="crash-before-summary"),
    pytest.param("  ✓ проверка → FAILED у провайдера назван\n  ✓ в консоли НЕТ /Traceback/\n"
                 "ИТОГО: 1/2 проверок пройдено\n", "",
                 ["ИТОГО: 1/2"], ["✓ проверка", "✓ в консоли"], id="summary-only"),
]


@pytest.mark.parametrize(("out", "err", "named", "quiet"), FAILURE_CASES)
def test_failure_names_suite_verdict(out: str, err: str, named: list[str], quiet: list[str]):
    """Провалы отчёта — вердикт самого набора: его список, его знаки в начале строки или его итог; знак внутри текста
    проходящей проверки провалом не называется."""
    report = _failure(Path("stub_suite.py"), subprocess.CompletedProcess(["stub"], 1, out, err))
    head = report.split("\n… хвост вывода:")[0]
    missing = [n for n in named if n not in head]
    noisy = [q for q in quiet if q in head]
    assert not missing and not noisy, f"не названы {missing}, названы лишние {noisy}:\n{head}"


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
    (HOME, XDG_STATE_HOME, LOCALAPPDATA); ожидаемый корень считает тот же platformdirs в той же среде. На Windows
    platformdirs берёт каталог у ОС, а не из LOCALAPPDATA; подмена — его WIN_PD_OVERRIDE_LOCAL_APPDATA."""
    local = str(tmp_path / "local")
    env = {k: v for k, v in os.environ.items() if k != "MCP_LOGS"} | {
        "HOME": str(tmp_path / "home"), "XDG_STATE_HOME": str(tmp_path / "state"), "LOCALAPPDATA": local,
        "WIN_PD_OVERRIDE_LOCAL_APPDATA": local}
    probe = subprocess.run([sys.executable, "-c", "from core.observability import Logs; "
                            "print(Logs.from_config('config', None).root)"],
                           cwd=repo_root, env=env, capture_output=True, text=True, timeout=60)
    owner = Path(probe.stdout.strip())
    assert probe.returncode == 0 and owner.is_relative_to(tmp_path), \
        f"корень журналов по умолчанию не подменился средой на этой ОС — замер невозможен: {owner} {probe.stderr[-300:]}"
    for suite in (Path("tests/quick/test_audit_fixes.py"), Path("tests/cache_overflow/test_cache_overflow.py")):
        p = subprocess.run(_cmd(suite), cwd=repo_root, env=env, capture_output=True, text=True, timeout=SUITE_TIMEOUT_SEC)
        assert p.returncode == 0, _failure(suite, p)
    assert not owner.exists(), f"набор без MCP_LOGS создал журналы владельца: {sorted(map(str, owner.rglob('*')))}"


def test_owner_logs_untouched(owner_logs: dict):
    """Последним: за прогон наборов каталог журналов владельца не изменился — тесты пишут только в свой корень."""
    after = _owner_logs()
    changed = sorted(k for k in set(owner_logs) | set(after) if owner_logs.get(k) != after.get(k))
    assert not changed, f"наборы тронули журналы владельца: {changed}"
