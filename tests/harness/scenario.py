"""tests/harness/scenario.py — исполнитель ОБЪЯВЛЕННЫХ сценариев против живого сервера.

Обвязка «поднять сцену → позвать инструмент → сверить контракт» одинакова в сотнях мест и
различается только условиями и ожиданиями: она здесь, различия — в объявлении. Реестр реакций даёт
СЛОВАРЬ (код есть, класс такой-то), а выбор кода остаётся за сценарием: ожидание, вычисленное из
конфига, по которому отвечает сервер, зелено и на сломанном. Неизвестный ключ, код мимо реестра,
ссылка в никуда — ошибка ЗАГРУЗКИ, а не пропуск. Подготовка с ветвлением остаётся Python-шагом, и
сценарий зовёт её по имени: иначе получается язык программирования на YAML.
"""

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from core.declaration import declarations_root

from .live_server import declarations_copy, spoil_slot
from .net_double import BYPASS, JOURNAL, SEAMS, parse_script

ROOT = Path(__file__).resolve().parents[2]

SCENARIO_KEYS = {"scenario", "why", "род", "given", "when", "then"}
# Роды объявлены СПИСКОМ: свободное слово здесь означало бы, что покрытие зоны родами нельзя
# посчитать — а именно счёт и отличает механизм от понятия.
РОДЫ = ("положительный", "отрицательный", "разрушительный")
# `declarations` — порча боевых деклараций в копии каталога (MCP_CONFIG), `fixtures` — приспособления среды (модули
# `fixtures/`, точка входа — `fixtures/sitecustomize.py`), `network` — сценарий двойника сети (`net_double.py`, только с
# приспособлением `net_double`): такой сценарий получает свой сервер.
GIVEN_KEYS = {"files", "declarations", "fixtures", "network"}
FIXTURES = Path(__file__).with_name("fixtures")
STEP_KEYS = {"call", "python", "rpc", "run", "headers", "token", "repeat", "with", "as", "expect"}
EXPECT_KEYS = {"ok", "code", "class", "recovery", "facts", "data", "data_contains",
               "data_absent", "console", "console_absent", "open", "http", "exit", "net_tasks", "detail", "canary",
               "task_error"}
# Отказ задания в ответе task_status: `in: task` — задание завершилось ошибкой, `in: element` — элемент без задания.
TASK_ERROR_KEYS = {"code", "class", "in", "facts"}
TASK_ERROR_PLACES = ("task", "element")

_REF = re.compile(r"\$\{([A-Za-z_][\w]*)\.([\w.]+)\}")
# Ссылка, не подошедшая под форму имени, раньше уезжала в команду ТЕКСТОМ: сторож получал путь
# `${имя.path}`, отвечал «источника нет», и опечатка выглядела как отказ механизма. Остаток
# подстановки — ошибка объявления, и её называют вслух.
_REF_TAIL = re.compile(r"\$\{[^}]*\}")
# Предел команде: сторож дерева считается секундами, а зависший шаг молчит так же, как пройденный.
COMMAND_TIMEOUT = 300


def known_findings() -> set[str]:
    """Номера находок из реестра: адрес `F###` в объявлении обязан вести в существующую строку."""
    try:
        text = (ROOT / "docs" / "roadmap" / "02_findings.md").read_text(encoding="utf-8")
    except OSError:
        return set()
    return set(re.findall(r"\bF\d+\b", text))


class ScenarioError(Exception):
    """Дефект ОБЪЯВЛЕНИЯ: неизвестный ключ, код мимо реестра, ссылка в никуда."""


# ═══ Словарь реакций: что вообще бывает ═══

class Vocabulary:
    """Реестр реакций как СЛОВАРЬ: код существует, класс у него такой-то. Выбор — за сценарием."""

    def __init__(self, path: Path | None = None):
        raw = yaml.safe_load((path or declarations_root() / "server_reactions.yaml").read_text(encoding="utf-8"))
        self.codes: dict[str, dict] = {k: v for k, v in (raw or {}).items() if isinstance(v, dict)}

    def validate(self, code: str, declared_class: str | None, where: str) -> None:
        # `RPC_-32601` и родня — словарь ПРОТОКОЛА, а не наш реестр реакций: отказ уровня конверта
        # приходит без `result`, и реакции у него нет и быть не может.
        if code.startswith("RPC_"):
            if declared_class:
                raise ScenarioError(f"{where}: у протокольного отказа {code} нет класса реакции — "
                                    "конверт без `result` не проходит через реестр")
            return
        if code not in self.codes:
            raise ScenarioError(f"{where}: код {code!r} не объявлен в config/server_reactions.yaml")
        actual = str(self.codes[code].get("class") or "")
        if declared_class and declared_class != actual:
            raise ScenarioError(
                f"{where}: у кода {code} в реестре класс {actual!r}, а сценарий ждёт {declared_class!r} "
                "— расходится объявление с реестром, а не сервер с ожиданием")


# ═══ Разбор объявления ═══

@dataclass
class Expectation:
    ok: bool
    code: str = ""
    reaction_class: str = ""
    recovery: bool | str | None = None     # `true/false` — есть ли рецепт; имя — КУДА он ведёт
    facts: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    data_contains: dict[str, str] = field(default_factory=dict)
    data_absent: dict[str, str] = field(default_factory=dict)
    console: str = ""
    console_absent: str = ""
    open: str = ""                         # адрес ОТКРЫТОЙ находки: ждём желаемого, сегодня его нет
    http: int | None = None                # код HTTP: отказ авторизации живёт в статусе, не в теле
    exit_code: int | None = None           # код возврата команды: у сторожа отказ живёт в нём
    net_tasks: int | None = None           # сколько задач (POST, дошедший до провайдера) шаг создал у двойника сети
    detail: dict[str, Any] = field(default_factory=dict)   # путь → значение в `error` отказа (`facts.host`, `errors.0.field`)
    canary: str = ""                       # секрет-канарейка: его нет ни в ответе, ни в консоли, ни в журналах сервера
    task_error: dict[str, Any] = field(default_factory=dict)   # отказ задания в элементе ответа task_status


@dataclass
class Step:
    index: int
    tool: str = ""
    python: str = ""
    rpc: str = ""
    run: str = ""
    repeat: int = 1
    headers: dict = field(default_factory=dict)
    token: str | None = None
    args: dict = field(default_factory=dict)
    alias: str = ""
    expect: Expectation | None = None

    @property
    def name(self) -> str:
        return self.tool or self.rpc or (f"run:{self.run}" if self.run else f"python:{self.python}")


@dataclass
class Scenario:
    id: str
    why: str
    files: dict[str, str]
    when: list[Step]
    then: list[Step]
    source: Path
    род: str = "положительный"
    declarations: dict[str, dict[str, Any]] = field(default_factory=dict)
    fixtures: tuple[str, ...] = ()
    network: dict[str, Any] = field(default_factory=dict)


def _reject_unknown(got: dict, allowed: set[str], where: str) -> None:
    extra = set(got) - allowed
    if extra:
        raise ScenarioError(f"{where}: неизвестные ключи {sorted(extra)} (разрешены {sorted(allowed)})")


def _expect(raw: dict, vocab: Vocabulary, where: str, command: bool = False) -> Expectation:
    """Ожидание шага. У команды отказ выражается КОДОМ ВОЗВРАТА, а не кодом реакции сервера:
    сторож не отвечает `ErrorDetail`, и подставлять ему реестровый код значило бы врать в словаре."""
    _reject_unknown(raw, EXPECT_KEYS, f"{where}.expect")
    if "ok" not in raw:
        raise ScenarioError(f"{where}.expect: нет ключа `ok` — исход обязан быть объявлен, а не выведен")
    ok = bool(raw["ok"])
    code = str(raw.get("code") or "")
    status = None if raw.get("exit") is None else int(raw["exit"])
    if status is not None and not command:
        raise ScenarioError(f"{where}.expect: `exit` не у шага `run` — код возврата есть только у команды")
    if command and code:
        raise ScenarioError(f"{where}.expect: `code` у шага `run` — у команды отказ живёт в коде "
                            "возврата, реестровых кодов она не отдаёт")
    if code and ok:
        raise ScenarioError(f"{where}.expect: `code` при `ok: true` — успех не несёт кода отказа")
    if not ok and not code and status is None:
        raise ScenarioError(f"{where}.expect: отказ без `code` — «просто упало» не является ожиданием")
    if command and status is not None and (status == 0) != ok:
        raise ScenarioError(f"{where}.expect: `exit: {status}` спорит с `ok: {ok}` — нулевой код "
                            "возврата и есть успех команды")
    if code:
        vocab.validate(code, str(raw.get("class") or "") or None, f"{where}.expect")
    tasks = raw.get("net_tasks")
    if tasks is not None and (command or isinstance(tasks, bool) or not isinstance(tasks, int) or tasks < 0):
        raise ScenarioError(f"{where}.expect: `net_tasks` — целое ≥ 0 у вызова сервера (пришло {tasks!r}"
                            f"{' у шага `run`' if command else ''}): задачи у провайдера создаёт сервер, а не команда")
    detail = raw.get("detail")
    if detail is not None and (ok or command or not isinstance(detail, dict) or not detail):
        raise ScenarioError(f"{where}.expect: `detail` — непустой словарь «путь → значение» у отказа вызова сервера "
                            f"(пришло {detail!r}): форма отказа (`ErrorDetail`) есть только у `ok: false` не команды")
    canary = raw.get("canary")
    if canary is not None and (command or not isinstance(canary, str) or not canary.strip()):
        raise ScenarioError(f"{where}.expect: `canary` — непустая строка-секрет у вызова сервера (пришло {canary!r}): "
                            "у команды нет ни ответа сервера, ни его журналов")
    task_error = raw.get("task_error")
    if task_error is not None:
        if command or not ok or not isinstance(task_error, dict):
            raise ScenarioError(f"{where}.expect: `task_error` — словарь у успешного вызова сервера (пришло {task_error!r}): "
                                "отказ задания живёт в элементе ответа task_status, сам вызов успешен")
        _reject_unknown(task_error, TASK_ERROR_KEYS, f"{where}.expect.task_error")
        if task_error.get("in") not in TASK_ERROR_PLACES or not task_error.get("code") \
                or not isinstance(task_error.get("facts", {}), dict):
            raise ScenarioError(f"{where}.expect.task_error: нужны `code` и `in` из {list(TASK_ERROR_PLACES)}, `facts` — "
                                f"словарь (пришло {task_error!r}): ошибка задания и отказ элемента — разные исходы")
        vocab.validate(str(task_error["code"]), str(task_error.get("class") or "") or None, f"{where}.expect.task_error")
    address = str(raw.get("open") or "")
    known = known_findings()
    if address and known and address not in known:
        raise ScenarioError(f"{where}.expect: находки {address} нет в docs/roadmap/02_findings.md — "
                            "адрес ведёт в пустоту, и «известная дыра» становится необоснованной")
    return Expectation(
        ok=ok, code=code, reaction_class=str(raw.get("class") or ""),
        recovery=raw.get("recovery"),
        facts=list(raw.get("facts") or []),
        data=dict(raw.get("data") or {}),
        data_contains=dict(raw.get("data_contains") or {}),
        data_absent=dict(raw.get("data_absent") or {}),
        console=str(raw.get("console") or ""),
        console_absent=str(raw.get("console_absent") or ""),
        open=str(raw.get("open") or ""),
        http=None if raw.get("http") is None else int(raw["http"]),
        exit_code=status,
        net_tasks=tasks,
        detail=dict(detail or {}),
        canary=str(canary or ""),
        task_error=dict(task_error or {}),
    )


def _steps(raw: list, vocab: Vocabulary, where: str) -> list[Step]:
    out = []
    for i, item in enumerate(raw or [], 1):
        if not isinstance(item, dict):
            raise ScenarioError(f"{where}[{i}]: шаг должен быть словарём, а не {type(item).__name__}")
        _reject_unknown(item, STEP_KEYS, f"{where}[{i}]")
        kinds = [k for k in ("call", "python", "rpc", "run") if item.get(k)]
        if len(kinds) != 1:
            raise ScenarioError(f"{where}[{i}]: шаг — ровно одно из `call` (инструмент), "
                                f"`rpc` (метод протокола), `python` (шаг-помощник), `run` (команда "
                                f"репозитория); указано {kinds or 'ничего'}")
        if (item.get("with") or {}).get("stdin") is not None and not item.get("run"):
            raise ScenarioError(f"{where}[{i}]: `stdin` — только у шага `run`: вход на стандартный "
                                f"поток есть у команды, у остальных он был бы принят и потерян")
        expect = item.get("expect")
        if int(item.get("repeat") or 1) > 1 and isinstance(expect, dict) and expect.get("net_tasks") is not None:
            raise ScenarioError(f"{where}[{i}]: `net_tasks` у шага с `repeat` — ряд сверяется по последнему ответу, "
                                f"а задачи копятся всем рядом; счёт задач — у одиночного вызова")
        out.append(Step(
            index=i, tool=str(item.get("call") or ""), python=str(item.get("python") or ""),
            rpc=str(item.get("rpc") or ""), run=str(item.get("run") or ""),
            repeat=int(item.get("repeat") or 1),
            headers=dict(item.get("headers") or {}),
            token=None if item.get("token") is None else str(item.get("token")),
            args=dict(item.get("with") or {}), alias=str(item.get("as") or ""),
            expect=(_expect(expect, vocab, f"{where}[{i}]", command=bool(item.get("run")))
                    if expect is not None else None),
        ))
    return out


def read_yaml(path: Path) -> Any:
    """Кривой YAML — дефект ОБЪЯВЛЕНИЯ наравне с неизвестным ключом.

    Без перехвата разборщик валит весь прогон трейсом, в котором файл-виновник не назван, а самая
    частая причина — двоеточие с пробелом внутри незакавыченной строки (`why`, `means`).
    """
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path.name}: не разбирается как YAML — {exc}. Двоеточие с пробелом "
                            "внутри строки требует кавычек") from exc


def scenario_files(directory: Path) -> list[Path]:
    """Файлы сценариев каталога. Карты (`*.map.yaml`) — другой формат и другой загрузчик."""
    return [p for p in sorted(directory.glob("*.yaml")) if not p.name.endswith(".map.yaml")]


def _ждёт_отказа(item: dict) -> bool:
    """Хоть один шаг объявления ждёт отказа — вызова (`ok: false`) или задания в элементе ответа (`task_error`). Разбор по
    ОБЪЯВЛЕНИЮ, а не по прогону: род сценария обязан читаться до сервера, иначе покрытие зоны узнаётся только после
    полного прогона."""
    return any(isinstance(ждёт := (шаг or {}).get("expect"), dict) and (ждёт.get("ok") is False or bool(ждёт.get("task_error")))
               for ключ in ("when", "then") for шаг in (item.get(ключ) or []))


def род_сценария(item: dict, where: str) -> str:
    """Род сценария: ОБЪЯВЛЕННЫЙ, либо выведенный из ожиданий его шагов.

    Разрушительный не выводится ничем: разрушение — это то, что сценарий ДЕЛАЕТ с состоянием
    (сносит зарегистрированный файл, режет колонку под формулой, вырезает источник улики), а не
    то, чего он ждёт в ответ. Поэтому он объявляется, а объявление, спорящее с ожиданиями, —
    отказ загрузки: род, которым можно назвать любой сценарий, не отличает ни одного.
    """
    отказы = _ждёт_отказа(item)
    объявлен = str(item.get("род") or "").strip()
    if объявлен and объявлен not in РОДЫ:
        raise ScenarioError(f"{where}: род `{объявлен}` не объявлен; известны {list(РОДЫ)}")
    if объявлен == "положительный" and отказы:
        raise ScenarioError(f"{where}: род объявлен положительным, а шаг ждёт отказа — "
                            "объявление спорит с ожиданием, и счёт покрытия родами станет ложью")
    if объявлен == "отрицательный" and not отказы:
        raise ScenarioError(f"{where}: род объявлен отрицательным, а отказа не ждёт ни один шаг — "
                            "назови шаг с `ok: false` либо не объявляй род")
    return объявлен or ("отрицательный" if отказы else "положительный")


def роды(directory: Path) -> dict[str, dict[str, str]]:
    """{файл: {сценарий: род}} по каталогу объявлений — читатель покрытия зоны родами.

    Сервер не поднимается и словарь реакций не нужен: род живёт в объявлении, и спрашивать его
    прогоном значило бы платить за вопрос «что у нас вообще есть» полным кругом сценариев.
    """
    свод: dict[str, dict[str, str]] = {}
    for path in scenario_files(directory):
        raw = read_yaml(path)
        if not isinstance(raw, list):
            continue
        свод[path.name] = {str((item or {}).get("scenario") or "?"):
                           род_сценария(item or {}, f"{path.name}:{(item or {}).get('scenario')}")
                           for item in raw}
    return свод


def load(path: Path, vocab: Vocabulary) -> list[Scenario]:
    """Сценарии файла. Кривое объявление падает ЗДЕСЬ — до сервера, до первого вызова."""
    raw = read_yaml(path)
    if not isinstance(raw, list):
        raise ScenarioError(f"{path.name}: файл — список сценариев, получено {type(raw).__name__}")
    out = []
    for item in raw:
        sid = str((item or {}).get("scenario") or "")
        where = f"{path.name}:{sid or '<без имени>'}"
        if not sid:
            raise ScenarioError(f"{where}: у сценария нет имени (`scenario`)")
        _reject_unknown(item, SCENARIO_KEYS, where)
        if not str(item.get("why") or "").strip():
            raise ScenarioError(f"{where}: нет `why` — сценарий без утверждаемого требования не нужен")
        given = dict(item.get("given") or {})
        _reject_unknown(given, GIVEN_KEYS, f"{where}.given")
        out.append(Scenario(
            id=sid, why=str(item["why"]).strip(), род=род_сценария(item, where),
            files={str(k): str(v) for k, v in (given.get("files") or {}).items()},
            when=_steps(item.get("when") or [], vocab, f"{where}.when"),
            then=_steps(item.get("then") or [], vocab, f"{where}.then"),
            source=path,
            declarations=_declarations(dict(given.get("declarations") or {}), f"{where}.given.declarations"),
            fixtures=_fixtures(given.get("fixtures") or [], f"{where}.given.fixtures"),
            network=_network(given.get("network"), given.get("fixtures") or [], f"{where}.given.network"),
        ))
        if not out[-1].network and any(s.expect and s.expect.net_tasks is not None for s in out[-1].when + out[-1].then):
            raise ScenarioError(f"{where}: `net_tasks` без `given.network` — журнала двойника нет, и ноль задач был бы "
                                "не замером, а пустым входом")
    if not out:
        raise ScenarioError(f"{path.name}: ни одного сценария — пустой файл красит прогон зелёным ни за что")
    return out


def _declarations(raw: dict, where: str) -> dict[str, dict[str, Any]]:
    """Порча боевых деклараций: файл и путь ключа обязаны быть в каталоге деклараций — порча несуществующего
    проверяла бы не то."""
    out = {}
    for name, spoils in raw.items():
        real = declarations_root() / str(name)
        if not real.is_file() or not isinstance(spoils, dict) or not spoils:
            raise ScenarioError(f"{where}: декларации `{name}` нет в каталоге деклараций или порча не названа")
        data = read_yaml(real)
        for key in spoils:
            try:
                parent, last = spoil_slot(data, str(key))
            except KeyError:
                parent, last = {}, ""
            if isinstance(parent, dict) and last not in parent:
                raise ScenarioError(f"{where}: в {name} нет ключа `{key}` — порча проверяла бы не то")
        out[str(name)] = {str(k): v for k, v in spoils.items()}
    return out


def _fixtures(raw: object, where: str) -> tuple[str, ...]:
    """Приспособления — имена модулей каталога `fixtures/`; точка входа приспособлением не бывает."""
    names = tuple(str(name) for name in raw) if isinstance(raw, list) else ()
    unknown = [n for n in names if n == "sitecustomize" or not (FIXTURES / f"{n}.py").is_file()]
    if not isinstance(raw, list) or unknown:
        known = sorted(p.stem for p in FIXTURES.glob("*.py") if p.stem != "sitecustomize")
        raise ScenarioError(f"{where}: приспособления — список имён из {known}, получено {raw!r}")
    return names


def _network(raw: object, fixtures: object, where: str) -> dict[str, Any]:
    """Сценарий сети — по форме двойника (`parse_script`) и только с приспособлением `net_double`: один источник истины
    о том, что сеть сервера подменена."""
    if not raw:
        return {}
    if not isinstance(fixtures, list) or "net_double" not in fixtures:
        raise ScenarioError(f"{where}: сценарий сети без приспособления `net_double` в `given.fixtures` — сеть боевая")
    try:
        return parse_script(raw)
    except ValueError as exc:
        raise ScenarioError(f"{where}: {exc}") from None


def server_env(scenario: Scenario) -> dict[str, str]:
    """Среда своего сервера сценария: копия деклараций с порчей (`MCP_CONFIG`) и приспособления (`VPM_FIXTURES`, каталог
    точки входа первым на `PYTHONPATH`). Пусто — сценарий идёт на общем сервере файла."""
    env = {}
    if scenario.declarations or scenario.network:   # сценарий сети пишет состояние у корня проекта — в копии, не в дереве
        env["MCP_CONFIG"] = str(declarations_copy(scenario.declarations))
    if scenario.fixtures:
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(FIXTURES), os.environ.get("PYTHONPATH", ""))))
        env["VPM_FIXTURES"] = ",".join(scenario.fixtures)
    if scenario.network:
        script = Path(tempfile.mkdtemp(prefix="vpm-net-")) / "network.json"
        atexit.register(shutil.rmtree, script.parent, ignore_errors=True)
        script.write_text(json.dumps(scenario.network, ensure_ascii=False), encoding="utf-8")
        env["NET_DOUBLE"] = str(script)
    return env


# ═══ Исполнение ═══

@dataclass
class Check:
    scenario: str
    label: str
    ok: bool
    detail: str = ""


def _as_text(found: Any) -> str:
    """Текст ответа по адресу. У структуры «содержит» значит «встречается в её записи»: очередь и
    список приходят списком словарей, и требовать от них строки означало бы не спрашивать вовсе."""
    return found if isinstance(found, str) else json.dumps(found, ensure_ascii=False, sort_keys=True)


def dig(data: Any, path: str) -> Any:
    """Значение по пути `a.b.0.c`. Промах — `_НЕТ_`, чтобы отличать его от настоящего `None`."""
    node = data
    for part in path.split("."):
        if isinstance(node, dict):
            if part not in node:
                return "_НЕТ_"
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return "_НЕТ_"
    return node


def _shape(node: Any) -> str:
    """Фраза для строки провала (не имя типа): ключи словаря, длина списка, тип и значение прочего."""
    if isinstance(node, dict):
        return f"словарь с ключами {sorted(node)}"
    if isinstance(node, list):
        return f"список из {len(node)}, первый — {_shape(node[0]) if node else 'пусто'}"
    return f"{type(node).__name__} = {node!r}"


def _where_it_broke(data: Any, path: str) -> str:
    """На каком колене путь оборвался и что там лежит; пусто — значит путь целый.

    Форму ответа не угадывают, её показывают: иначе промах по полю читается как отказ сервера,
    хотя почти всегда неверно объявлено ожидание.
    """
    node, walked = data, []                    # type: Any, list[str]
    for part in path.split("."):
        step = dig(node, part)
        if step == "_НЕТ_":
            return f"путь оборвался на `{'.'.join(walked) or 'корне ответа'}` — там {_shape(node)}"
        walked.append(part)
        node = step
    return ""


def _advice(data: Any, path: str, found: Any) -> str:
    """Совет добавляется, только когда поля НЕ НАШЛОСЬ: при несовпавшем значении подсказывать нечего."""
    return f" · совет: {_where_it_broke(data, path)}" if found == "_НЕТ_" else ""


_VOCAB: Vocabulary | None = None


def vocabulary() -> Vocabulary:
    """Реестр реакций — он же словарь советов."""
    global _VOCAB
    if _VOCAB is None:
        # Читается один раз на процесс: второй читатель реестра разошёлся бы с первым,
        # а правка YAML посреди прогона не подхватывается намеренно.
        _VOCAB = Vocabulary()
    return _VOCAB


def _resolve(value: Any, results: dict[str, Any]) -> Any:
    """`${шаг.путь}` → значение прошлого шага. Ссылка в никуда — ошибка объявления, не пустая строка."""
    if isinstance(value, dict):
        return {k: _resolve(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, results) for v in value]
    if not isinstance(value, str):
        return value
    match = _REF.fullmatch(value.strip())
    if match:
        return _lookup(match.group(1), match.group(2), results)
    подставлено = _REF.sub(lambda m: str(_lookup(m.group(1), m.group(2), results)), value)
    if (хвост := _REF_TAIL.search(подставлено)):
        raise ScenarioError(
            f"ссылка {хвост.group(0)} не разобрана: имя шага пишется латиницей и начинается с "
            f"буквы или подчёркивания (`${{live.path}}`). Неразобранная ссылка уезжает в команду "
            f"текстом, и отказ выглядит как поломка сторожа")
    return подставлено


def _lookup(alias: str, path: str, results: dict[str, Any]) -> Any:
    if alias not in results:
        raise ScenarioError(f"ссылка ${{{alias}.{path}}}: шага с именем {alias!r} не было выше")
    found = dig(results[alias], path)
    if found == "_НЕТ_":
        raise ScenarioError(f"ссылка ${{{alias}.{path}}}: у шага {alias!r} нет такого поля ({results[alias]})")
    return found


def _revision() -> tuple[str, bool]:
    """Ревизия и чистота дерева: улика обязана говорить, ЧТО именно она проверяла."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                                text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "", True
    return head, bool(status)


class Journal:
    """Журнал прогона: запрос → ответ → код → строки консоли, одна строка JSON на шаг."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("w", encoding="utf-8")

    def write(self, **entry) -> None:
        self.file.write(json.dumps({"ts": round(time.time(), 3), **entry}, ensure_ascii=False) + "\n")
        self.file.flush()

    def verdict(self, ok: bool, total: int, failed: int, scenarios: list[str],
                crashed: bool = False) -> None:
        """Итог прогона последней строкой: журнал обязан отвечать сам, без стенограммы и без слов.

        Сторож не должен верить утверждению «прогнал» — он читает артефакт: что гонялось, чем
        кончилось и когда. Улика, которой нет, считается провалом (контракт «по умолчанию ПРОВАЛ»).
        """
        head, dirty = _revision()
        self.write(scenario="__run__", ok=bool(ok) and not crashed, crashed=crashed,
                   total=total, failed=failed, scenarios=sorted(scenarios), head=head, dirty=dirty)

    def close(self) -> None:
        self.file.close()


class Runner:
    """Гоняет сценарии против ПОДНЯТОГО сервера. Сервер живёт снаружи — его владелец решает изоляцию."""

    def __init__(self, srv, journal: Journal | None = None, steps: dict[str, Callable] | None = None):
        self.srv = srv
        self.journal = journal
        self.steps = steps or {}
        self.trace: list[dict] = []            # тот же след в памяти: по нему проверяются маршруты
        self.vocab = vocabulary()

    def _hint(self, arrived: str, expected: str) -> str:
        """Совет при расхождении кода: что реестр говорит о ПРИШЕДШЕМ и куда смотреть дальше.

        Провал обязан называть следующий шаг: «пришло не то» без адреса стоит того же разбора
        заново на каждом прогоне.
        """
        if not arrived:
            return ("отказ не наступил вовсе — условие шага не создано (аргументы, `given.files`) "
                    "либо путь перестал быть запретным")
        entry = self.vocab.codes.get(arrived) or {}
        recovery = entry.get("recovery") or {}
        said = f"реестр о {arrived}: класс {entry.get('class') or '—'}"
        if recovery.get("suggested_tool"):
            said += f", рецепт ведёт в `{recovery['suggested_tool']}`"
        if not expected:
            return said
        return (f"{said}. Прав сервер — поправь `code:` в объявлении; прав сценарий — "
                f"ищи, кто бросает {expected}: grep -rn '\"{expected}\"' core tools")

    def run(self, scenario: Scenario) -> list[Check]:
        checks: list[Check] = []
        results: dict[str, Any] = {}
        if scenario.network:
            unguarded = self._net_unguarded()
            checks.append(Check(scenario.id, "двойник сети поставлен до первого шага", not unguarded, unguarded))
            if unguarded:   # ни одного шага: без двойника первый вызов ушёл бы в настоящую сеть (и с ключом провайдера)
                return checks
        for rel, content in scenario.files.items():
            self.srv.write(rel, content)
        for step in list(scenario.when) + list(scenario.then):
            try:
                checks += self._step(scenario, step, results)
            except ScenarioError as exc:
                checks.append(Check(scenario.id, f"{step.name}: объявление", False, str(exc)))
                break
        return checks

    def _step(self, scenario: Scenario, step: Step, results: dict[str, Any]) -> list[Check]:
        if step.repeat > 1:
            return self._repeat(scenario, step, results)
        return self._once(scenario, step, results)

    def _repeat(self, scenario: Scenario, step: Step, results: dict[str, Any]) -> list[Check]:
        """Повтор вызова: предмет проверки — не отдельный ответ, а ПОВЕДЕНИЕ РЯДА.

        Без него зона частоты объявлением не берётся: один вызов предела не достигает, а писать
        цикл питоном значит вернуть набор туда, откуда его убирали. В журнал идёт одна запись со
        сводкой — тысяча строк на шквал сделала бы улику нечитаемой.
        """
        single = Step(index=step.index, tool=step.tool, python=step.python, rpc=step.rpc,
                      headers=step.headers, token=step.token, args=step.args, alias=step.alias)
        ok_count, first_refusal, first_code, first_detail, last = 0, None, None, None, None
        for attempt in range(1, step.repeat + 1):
            last = self._once(scenario, single, results, return_observed=True)
            if last["ok"]:
                ok_count += 1
            elif first_refusal is None:
                first_refusal, first_code, first_detail = attempt, last["code"], last.get("detail") or {}
        summary = {"times": step.repeat, "ok_count": ok_count, "first_refusal": first_refusal, "first_code": first_code,
                   "first_detail": first_detail}
        if isinstance(last.get("data"), dict):
            last["data"] = {**last["data"], "_repeat": summary}
        else:
            last["data"] = {"_repeat": summary}
        entry = {"scenario": scenario.id, "step": step.index, "tool": f"{step.name} ×{step.repeat}",
                 "args": _resolve(step.args, results), "ok": last["ok"], "code": last["code"],
                 "message": last["message"], "reaction_class": last["reaction_class"],
                 "recovery": last["recovery"], "facts": last["facts"], "data": last["data"],
                 "console": []}
        self.trace.append(entry)
        if self.journal:
            self.journal.write(**entry)
        return self._verify(scenario, step, last, "")

    def _once(self, scenario: Scenario, step: Step, results: dict[str, Any],
              return_observed: bool = False):
        args = _resolve(step.args, results)
        console_from = len(self.srv.console.lines)
        tasks_from = self._net_tasks() if step.expect and step.expect.net_tasks is not None else None
        if step.run:
            observed = self._command(step, args)
        elif step.python:
            if step.python not in self.steps:
                raise ScenarioError(f"шаг-помощник {step.python!r} не объявлен в tests/scenarios/steps.py")
            outcome = self.steps[step.python](self.srv, **args) or {}
            observed = {"ok": bool(outcome.get("ok", True)), "code": str(outcome.get("code") or ""),
                        "message": str(outcome.get("message") or ""), "data": outcome.get("data") or {},
                        "reaction_class": "", "recovery": {}, "facts": []}
        else:
            # Транспортные условия объявляются шагом: подменённый `Origin`, чужой `Host`, пустой
            # ключ. Без них периметр (файрвол, авторизация) декларацией недостижим вовсе.
            kw = {}
            if step.headers:
                kw["extra_headers"] = step.headers
            if step.token is not None:
                kw["token"] = step.token
            method = "tools/call" if step.tool else step.rpc
            params = {"name": step.tool, "arguments": args} if step.tool else args
            envelope, status = self.srv.rpc.raw_with_status(method, params, **kw)
            rpc = self.srv.rpc
            # Класс и рецепт — из ErrorDetail отказа: у инструмента он в structuredContent, у периметра — в `error.data`
            # конверта (вызов инструмента, отбитый до диспетчера, structuredContent не несёт).
            detail = rpc.detail(envelope)
            if step.tool:
                observed = {"ok": not rpc.is_error(envelope), "code": rpc.error_code(envelope) or "",
                            "message": str(detail.get("message") or rpc.text(envelope))[:400],
                            "data": rpc.data(envelope) or {},
                            "reaction_class": str(detail.get("reaction_class") or ""),
                            "recovery": detail.get("recovery") or {},
                            "facts": [str(f.get("type") or "") for f in (rpc.facts(envelope) or [])],
                            "detail": detail, "top": sorted(rpc.structured(envelope))}
            else:
                # Ответ протокола — это конверт целиком: у `tools/list` нет фактов успеха, а у отказа транспорта — ни
                # класса, ни рецепта, и подставлять их пустыми честнее, чем притворяться вызовом инструмента.
                failure = rpc.protocol_error(envelope)
                observed = {"ok": not failure and "result" in envelope,
                            "code": rpc.error_code(envelope),
                            "message": str(failure.get("message", ""))[:400],
                            "data": envelope.get("result") or {},
                            "reaction_class": str(detail.get("reaction_class") or ""),
                            "recovery": detail.get("recovery") or {}, "facts": [], "detail": detail}
            observed["http"] = status
            observed["wire"] = json.dumps(envelope, ensure_ascii=False)
        if tasks_from is not None:
            observed["net_tasks"] = self._net_tasks() - tasks_from
        if step.expect and step.expect.task_error:
            observed["task_error"] = _resolve(step.expect.task_error, results)   # `${шаг.путь}` в фактах — id прошлых шагов
        if step.alias:
            results[step.alias] = observed["data"]
        # У команды своей консоли сервера нет: её вывод И ЕСТЬ то, что читают глазами, поэтому
        # он подставляется на место консоли — иначе `console:` в объявлении был бы недостижим.
        console = ([observed.pop("output")] if step.run
                   else self.srv.console.lines[console_from:])
        entry = {"scenario": scenario.id, "step": step.index, "tool": step.name, "args": args,
                 "ok": observed["ok"], "code": observed["code"], "message": observed["message"],
                 "reaction_class": observed["reaction_class"], "recovery": observed["recovery"],
                 "facts": observed["facts"], "data": observed["data"], "console": console}
        if return_observed:
            return observed
        self.trace.append(entry)
        if self.journal:
            self.journal.write(**entry)
        return self._verify(scenario, step, observed, "\n".join(console))

    def _command(self, step: Step, args: dict) -> dict:
        """Команда РЕПОЗИТОРИЯ: сторож судит дерево, и его вердикт приходит кодом возврата.

        Оболочки нет, цель обязана лежать в дереве: объявление исполняемо, и «запусти что угодно»
        превратило бы карту в дверь наружу.
        """
        target = (ROOT / step.run).resolve()
        if not str(target).startswith(str(ROOT) + os.sep) or not target.is_file():
            raise ScenarioError(f"шаг `run: {step.run}` ведёт вне репозитория или в пустоту — "
                                "объявление исполняемо, поэтому цель обязана быть своей")
        argv = ([sys.executable, str(target)] if target.suffix == ".py" else [str(target)])
        argv += [str(a) for a in (args.get("args") or [])]
        поток = args.get("stdin")
        done = subprocess.run(argv, input=None if поток is None else str(поток),
                              capture_output=True, text=True, timeout=COMMAND_TIMEOUT, cwd=ROOT)
        return {"ok": done.returncode == 0, "code": "",
                "message": (done.stdout + done.stderr).strip()[-400:],
                "data": {"exit": done.returncode}, "reaction_class": "", "recovery": {},
                "facts": [], "output": done.stdout + done.stderr}

    def _net_records(self) -> list[dict]:
        """Журнал двойника сети сервера (`JOURNAL` в его журналах); нет файла — пусто."""
        try:
            lines = (Path(self.srv.logs) / JOURNAL).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def _net_tasks(self) -> int:
        """Задачи у провайдера: записи журнала двойника с `task` (POST дошёл, даже если ответ оборван)."""
        return sum(1 for record in self._net_records() if record.get("task") is True)

    def _net_unguarded(self) -> str:
        """Чего не хватает до сети под двойником: первая запись журнала — объявление установки (`installed` ⊇ SEAMS,
        `tripwires` ⊇ BYPASS); пусто — двойник стоит."""
        records = self._net_records()
        if not records:
            return f"журнала `{JOURNAL}` нет — приспособление net_double не поставило двойник"
        first = records[0]
        missing = sorted(set(SEAMS) - set(first.get("installed") or [])) + sorted(
            set(BYPASS) - set(first.get("tripwires") or []))
        return f"двойник net_double не закрыл {missing} — первая запись журнала: {first}" if missing else ""

    def _canary_in_logs(self, canary: str) -> list[str]:
        """Файлы журналов сервера, где лежит канарейка — как есть или в экранировании JSON (`\\uXXXX`)."""
        forms = {canary.encode("utf-8"), json.dumps(canary)[1:-1].encode("utf-8")}
        root = Path(self.srv.logs)
        return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                      if p.is_file() and any(form in p.read_bytes() for form in forms))

    def _verify(self, scenario: Scenario, step: Step, got: dict, console: str) -> list[Check]:
        exp = step.expect
        if exp is None:
            return []
        tag = f"{step.index}. {step.name}"
        out: list[Check] = []

        if exp.http is not None:
            out.append(Check(scenario.id, f"{tag} → HTTP {exp.http}", got.get("http") == exp.http,
                             f"пришёл {got.get('http')}"))
        if exp.net_tasks is not None:
            out.append(Check(scenario.id, f"{tag} → задач у провайдера {exp.net_tasks}",
                             got.get("net_tasks") == exp.net_tasks,
                             f"дошло {got.get('net_tasks')} (журнал двойника `{JOURNAL}` в журналах сервера) · совет: "
                             "больше — вызов повторён после отправки, меньше — запрос не ушёл или ушёл мимо маршрута"))

        if exp.open:
            # Объявлено ЖЕЛАЕМОЕ поведение при открытой находке: сегодня его нет, и это baseline.
            # Совпало — значит находка закрыта, и прогон обязан покраснеть, иначе реестр отстанет.
            matched = got["code"] == exp.code if exp.code else got["ok"] == exp.ok
            return [Check(scenario.id, f"{tag} → [ОТКРЫТО {exp.open}] ждём {exp.code or 'успеха'}", not matched,
                          f"поведение сошлось с желаемым (пришло {got['code'] or 'успех'}) — находка "
                          f"{exp.open} закрыта: сними `open` и обнови docs/roadmap/02_findings.md")]

        # Исход и причина: при расхождении печатается ФАКТИЧЕСКИЙ код и сообщение сервера.
        if exp.exit_code is not None:
            came = (got.get("data") or {}).get("exit")
            out.append(Check(scenario.id, f"{tag} → код возврата {exp.exit_code}",
                             came == exp.exit_code,
                             f"вернулось {came} · вывод: {got['message'][-200:]}"))
        elif exp.ok:
            out.append(Check(scenario.id, f"{tag} → успех", got["ok"],
                             f"вместо успеха {got['code'] or '(без кода)'}: {got['message']} · "
                             f"совет: {self._hint(got['code'], '')}"))
        else:
            out.append(Check(scenario.id, f"{tag} → {exp.code}", got["code"] == exp.code,
                             f"пришло {got['code'] or 'УСПЕХ'}: {got['message']} · "
                             f"совет: {self._hint(got['code'], exp.code)}"))
            if step.tool:
                # Одно имя — один тип: `facts` наверху — список фактов успеха; факты причины отказа живут под `error`.
                out.append(Check(scenario.id, f"{tag} → у отказа нет `facts` наверху", "facts" not in got.get("top", []),
                                 f"ключи structuredContent: {got.get('top')}"))
            if exp.reaction_class:
                # Класс объявлен реестром: если на проводе другой, теряется он по дороге,
                # а не «сценарий ошибся» — это разрыв паритета контракта, а не опечатка.
                declared = (self.vocab.codes.get(got["code"]) or {}).get("class") or "—"
                out.append(Check(scenario.id, f"{tag} → класс {exp.reaction_class}",
                                 got["reaction_class"] == exp.reaction_class,
                                 f"класс на проводе: {got['reaction_class'] or 'НЕ ДОЕХАЛ'} · "
                                 f"совет: реестр объявляет для {got['code'] or '—'} класс {declared}"))
            if isinstance(exp.recovery, str):
                # Рецепт, который ВЕДЁТ не туда, — это не «рецепт есть»: клиент делает следующий
                # вызов по этому имени, поэтому имя и есть предмет проверки.
                went = str((got["recovery"] or {}).get("suggested_tool") or "")
                out.append(Check(scenario.id, f"{tag} → рецепт ведёт в {exp.recovery}",
                                 went == exp.recovery,
                                 f"рецепт ведёт в {went or 'НИКУДА'} · совет: рецепт объявлен в "
                                 f"config/server_reactions.yaml → {got['code']}.recovery, "
                                 "аргумент `suggested_tool` в движке его не перекрывает"))
            elif exp.recovery is not None:
                has = bool(got["recovery"] and (got["recovery"].get("reason") or got["recovery"].get("suggested_tool")))
                out.append(Check(scenario.id, f"{tag} → рецепт восстановления", has == exp.recovery,
                                 f"recovery: {got['recovery'] or 'пусто'}"))
        for fact in exp.facts:
            out.append(Check(scenario.id, f"{tag} → факт {fact}", fact in got["facts"],
                             f"факты ответа: {got['facts']}"))
        if exp.canary:
            # Значение секрета в улике не печатается: строка провала называет только место.
            out.append(Check(scenario.id, f"{tag} → канарейки нет в ответе", exp.canary not in got.get("wire", ""),
                             "секрет в ответе сервера (конверт JSON-RPC: текст, structuredContent)"))
            out.append(Check(scenario.id, f"{tag} → канарейки нет в консоли", exp.canary not in console,
                             "секрет в консоли сервера"))
            leaked = self._canary_in_logs(exp.canary)
            out.append(Check(scenario.id, f"{tag} → канарейки нет в журналах сервера", not leaked,
                             f"секрет в файлах журналов: {leaked}"))
        if exp.task_error:
            want = got.get("task_error") or exp.task_error
            tasks = got["data"].get("tasks") if isinstance(got["data"], dict) else None
            one = tasks[0] if isinstance(tasks, list) and len(tasks) == 1 and isinstance(tasks[0], dict) else {}
            task = one.get("task") or {}
            # Ошибка задания — у задания, завершённого отказом или снятием (причина cancelled тоже в `error`); отказ
            # элемента — у элемента без задания.
            error = ((task.get("error") if task.get("status") in ("failed", "cancelled") and not one.get("error") else None)
                     if want["in"] == "task" else (one.get("error") if not task else None)) or {}
            out.append(Check(scenario.id, f"{tag} → {want['in']}: {want['code']}",
                             error.get("code") == want["code"]
                             and (not want.get("class") or error.get("reaction_class") == want["class"]),
                             f"элемент ответа: {json.dumps(one or tasks, ensure_ascii=False)[:400]}"))
            for key, value in (want.get("facts") or {}).items():
                found = (error.get("facts") or {}).get(key)
                out.append(Check(scenario.id, f"{tag} → {want['in']}: факт {key} = {value!r}", found == value,
                                 f"пришло {found!r}"))
        detail = got.get("detail") or {}
        for path, want in exp.detail.items():
            found = dig(detail, path)
            out.append(Check(scenario.id, f"{tag} → отказ: {path} = {want!r}", found == want,
                             f"пришло {found!r}{_advice(detail, path, found)}"))
        for path, want in exp.data.items():
            found = dig(got["data"], path)
            out.append(Check(scenario.id, f"{tag} → {path} = {want!r}", found == want,
                             f"пришло {found!r}{_advice(got['data'], path, found)}"))
        for path, part in exp.data_contains.items():
            found = dig(got["data"], path)
            out.append(Check(scenario.id, f"{tag} → {path} содержит {part!r}",
                             part in _as_text(found),
                             f"пришло {found!r}{_advice(got['data'], path, found)}"))
        for path, part in exp.data_absent.items():
            # Отрицание — не роскошь: «предмета ещё нет» иначе неотличимо от «ответ пуст», и
            # состояние ДО появления предмета выражать нечем, а без него нет и предиката.
            found = dig(got["data"], path)
            out.append(Check(scenario.id, f"{tag} → в {path} НЕТ {part!r}",
                             part not in _as_text(found),
                             f"пришло {found!r}"))
        if exp.console:
            out.append(Check(scenario.id, f"{tag} → консоль /{exp.console}/",
                             re.search(exp.console, console, re.I | re.M) is not None,
                             f"вывод шага: {console[-300:] or 'пусто'}"))
        if exp.console_absent:
            # Отказ обязан быть ОТВЕТОМ, а не падением: трейс в выводе означает, что сервер
            # сломался внутри, даже если клиенту что-то вернулось.
            found = re.search(exp.console_absent, console, re.I | re.M)
            out.append(Check(scenario.id, f"{tag} → в консоли НЕТ /{exp.console_absent}/", found is None,
                             f"нашлось: {found.group(0) if found else ''} … {console[-300:]}"))
        return out
