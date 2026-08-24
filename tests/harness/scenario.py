"""
tests/harness/scenario.py — исполнитель ОБЪЯВЛЕННЫХ сценариев против живого сервера.

Обвязка «поднять сцену → позвать инструмент → сверить контракт» одинакова в сотнях мест и
различается только условиями и ожиданиями: она здесь, различия — в `tests/scenarios/*.yaml`.

## Границы
- Реестр реакций даёт СЛОВАРЬ (код есть, класс такой-то), выбор кода остаётся за сценарием:
  ожидание, вычисленное из конфига, по которому отвечает сервер, зелено и на сломанном.
- Неизвестный ключ, код мимо реестра, ссылка в никуда — ошибка ЗАГРУЗКИ, а не пропуск.
- Подготовка с ветвлением остаётся Python-шагом (`tests/scenarios/steps.py`), сценарий зовёт
  её по имени: иначе получается язык программирования на YAML.
"""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[2]

SCENARIO_KEYS = {"scenario", "why", "given", "when", "then"}
GIVEN_KEYS = {"files"}
STEP_KEYS = {"call", "python", "with", "as", "expect"}
EXPECT_KEYS = {"ok", "code", "class", "recovery", "facts", "data", "data_contains",
               "console", "console_absent", "open"}

_REF = re.compile(r"\$\{([A-Za-z_][\w]*)\.([\w.]+)\}")


class ScenarioError(Exception):
    """Дефект ОБЪЯВЛЕНИЯ: неизвестный ключ, код мимо реестра, ссылка в никуда."""


# ═══ Словарь реакций: что вообще бывает ═══

class Vocabulary:
    """Реестр реакций как СЛОВАРЬ: код существует, класс у него такой-то. Выбор — за сценарием."""

    def __init__(self, path: Path | None = None):
        raw = yaml.safe_load((path or ROOT / "config" / "server_reactions.yaml").read_text(encoding="utf-8"))
        self.codes: dict[str, dict] = {k: v for k, v in (raw or {}).items() if isinstance(v, dict)}

    def validate(self, code: str, declared_class: str | None, where: str) -> None:
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
    recovery: bool | None = None
    facts: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    data_contains: dict[str, str] = field(default_factory=dict)
    console: str = ""
    console_absent: str = ""
    open: str = ""                         # адрес ОТКРЫТОЙ находки: ждём желаемого, сегодня его нет


@dataclass
class Step:
    index: int
    tool: str = ""
    python: str = ""
    args: dict = field(default_factory=dict)
    alias: str = ""
    expect: Expectation | None = None

    @property
    def name(self) -> str:
        return self.tool or f"python:{self.python}"


@dataclass
class Scenario:
    id: str
    why: str
    files: dict[str, str]
    when: list[Step]
    then: list[Step]
    source: Path


def _reject_unknown(got: dict, allowed: set[str], where: str) -> None:
    extra = set(got) - allowed
    if extra:
        raise ScenarioError(f"{where}: неизвестные ключи {sorted(extra)} (разрешены {sorted(allowed)})")


def _expect(raw: dict, vocab: Vocabulary, where: str) -> Expectation:
    _reject_unknown(raw, EXPECT_KEYS, f"{where}.expect")
    if "ok" not in raw:
        raise ScenarioError(f"{where}.expect: нет ключа `ok` — исход обязан быть объявлен, а не выведен")
    ok = bool(raw["ok"])
    code = str(raw.get("code") or "")
    if code and ok:
        raise ScenarioError(f"{where}.expect: `code` при `ok: true` — успех не несёт кода отказа")
    if not ok and not code:
        raise ScenarioError(f"{where}.expect: отказ без `code` — «просто упало» не является ожиданием")
    if code:
        vocab.validate(code, str(raw.get("class") or "") or None, f"{where}.expect")
    return Expectation(
        ok=ok, code=code, reaction_class=str(raw.get("class") or ""),
        recovery=raw.get("recovery"),
        facts=list(raw.get("facts") or []),
        data=dict(raw.get("data") or {}),
        data_contains=dict(raw.get("data_contains") or {}),
        console=str(raw.get("console") or ""),
        console_absent=str(raw.get("console_absent") or ""),
        open=str(raw.get("open") or ""),
    )


def _steps(raw: list, vocab: Vocabulary, where: str) -> list[Step]:
    out = []
    for i, item in enumerate(raw or [], 1):
        if not isinstance(item, dict):
            raise ScenarioError(f"{where}[{i}]: шаг должен быть словарём, а не {type(item).__name__}")
        _reject_unknown(item, STEP_KEYS, f"{where}[{i}]")
        if bool(item.get("call")) == bool(item.get("python")):
            raise ScenarioError(f"{where}[{i}]: шаг — либо `call` (инструмент), либо `python` (шаг-помощник)")
        expect = item.get("expect")
        out.append(Step(
            index=i, tool=str(item.get("call") or ""), python=str(item.get("python") or ""),
            args=dict(item.get("with") or {}), alias=str(item.get("as") or ""),
            expect=_expect(expect, vocab, f"{where}[{i}]") if expect is not None else None,
        ))
    return out


def scenario_files(directory: Path) -> list[Path]:
    """Файлы сценариев каталога. Карты (`*.map.yaml`) — другой формат и другой загрузчик."""
    return [p for p in sorted(directory.glob("*.yaml")) if not p.name.endswith(".map.yaml")]


def load(path: Path, vocab: Vocabulary) -> list[Scenario]:
    """Сценарии файла. Кривое объявление падает ЗДЕСЬ — до сервера, до первого вызова."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
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
            id=sid, why=str(item["why"]).strip(),
            files={str(k): str(v) for k, v in (given.get("files") or {}).items()},
            when=_steps(item.get("when") or [], vocab, f"{where}.when"),
            then=_steps(item.get("then") or [], vocab, f"{where}.then"),
            source=path,
        ))
    if not out:
        raise ScenarioError(f"{path.name}: ни одного сценария — пустой файл красит прогон зелёным ни за что")
    return out


# ═══ Исполнение ═══

@dataclass
class Check:
    scenario: str
    label: str
    ok: bool
    detail: str = ""


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
    return _REF.sub(lambda m: str(_lookup(m.group(1), m.group(2), results)), value)


def _lookup(alias: str, path: str, results: dict[str, Any]) -> Any:
    if alias not in results:
        raise ScenarioError(f"ссылка ${{{alias}.{path}}}: шага с именем {alias!r} не было выше")
    found = dig(results[alias], path)
    if found == "_НЕТ_":
        raise ScenarioError(f"ссылка ${{{alias}.{path}}}: у шага {alias!r} нет такого поля ({results[alias]})")
    return found


class Journal:
    """Журнал прогона: запрос → ответ → код → строки консоли, одна строка JSON на шаг."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("w", encoding="utf-8")

    def write(self, **entry) -> None:
        self.file.write(json.dumps({"ts": round(time.time(), 3), **entry}, ensure_ascii=False) + "\n")
        self.file.flush()

    def close(self) -> None:
        self.file.close()


class Runner:
    """Гоняет сценарии против ПОДНЯТОГО сервера. Сервер живёт снаружи — его владелец решает изоляцию."""

    def __init__(self, srv, journal: Journal | None = None, steps: dict[str, Callable] | None = None):
        self.srv = srv
        self.journal = journal
        self.steps = steps or {}
        self.trace: list[dict] = []            # тот же след в памяти: по нему проверяются маршруты

    def run(self, scenario: Scenario) -> list[Check]:
        checks: list[Check] = []
        results: dict[str, Any] = {}
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
        args = _resolve(step.args, results)
        console_from = len(self.srv.console.lines)
        if step.python:
            if step.python not in self.steps:
                raise ScenarioError(f"шаг-помощник {step.python!r} не объявлен в tests/scenarios/steps.py")
            outcome = self.steps[step.python](self.srv, **args) or {}
            observed = {"ok": bool(outcome.get("ok", True)), "code": str(outcome.get("code") or ""),
                        "message": str(outcome.get("message") or ""), "data": outcome.get("data") or {},
                        "reaction_class": "", "recovery": {}, "facts": []}
        else:
            env = self.srv.rpc.call_tool(step.tool, args)
            structured = self.srv.rpc.structured(env["envelope"])
            observed = {"ok": not env["is_error"], "code": env["code"] or "",
                        "message": self.srv.rpc.text(env["envelope"])[:400], "data": env["data"] or {},
                        "reaction_class": str(structured.get("reaction_class") or ""),
                        "recovery": structured.get("recovery") or {},
                        "facts": [str(f.get("type") or "") for f in (env["facts"] or [])]}
        if step.alias:
            results[step.alias] = observed["data"]
        console = self.srv.console.lines[console_from:]
        entry = {"scenario": scenario.id, "step": step.index, "tool": step.name, "args": args,
                 "ok": observed["ok"], "code": observed["code"], "message": observed["message"],
                 "reaction_class": observed["reaction_class"], "recovery": observed["recovery"],
                 "facts": observed["facts"], "data": observed["data"], "console": console}
        self.trace.append(entry)
        if self.journal:
            self.journal.write(**entry)
        return self._verify(scenario, step, observed, "\n".join(console))

    def _verify(self, scenario: Scenario, step: Step, got: dict, console: str) -> list[Check]:
        exp = step.expect
        if exp is None:
            return []
        tag = f"{step.index}. {step.name}"
        out: list[Check] = []

        if exp.open:
            # Объявлено ЖЕЛАЕМОЕ поведение при открытой находке: сегодня его нет, и это baseline.
            # Совпало — значит находка закрыта, и прогон обязан покраснеть, иначе реестр отстанет.
            matched = got["code"] == exp.code if exp.code else got["ok"] == exp.ok
            return [Check(scenario.id, f"{tag} → [ОТКРЫТО {exp.open}] ждём {exp.code or 'успеха'}", not matched,
                          f"поведение сошлось с желаемым (пришло {got['code'] or 'успех'}) — находка "
                          f"{exp.open} закрыта: сними `open` и обнови docs/roadmap/02_findings.md")]

        # Исход и причина: при расхождении печатается ФАКТИЧЕСКИЙ код и сообщение сервера.
        if exp.ok:
            out.append(Check(scenario.id, f"{tag} → успех", got["ok"],
                             f"вместо успеха {got['code'] or '(без кода)'}: {got['message']}"))
        else:
            out.append(Check(scenario.id, f"{tag} → {exp.code}", got["code"] == exp.code,
                             f"пришло {got['code'] or 'УСПЕХ'}: {got['message']}"))
            if exp.reaction_class:
                out.append(Check(scenario.id, f"{tag} → класс {exp.reaction_class}",
                                 got["reaction_class"] == exp.reaction_class,
                                 f"класс на проводе: {got['reaction_class'] or 'НЕ ДОЕХАЛ'}"))
            if exp.recovery is not None:
                has = bool(got["recovery"] and (got["recovery"].get("reason") or got["recovery"].get("suggested_tool")))
                out.append(Check(scenario.id, f"{tag} → рецепт восстановления", has == exp.recovery,
                                 f"recovery: {got['recovery'] or 'пусто'}"))
        for fact in exp.facts:
            out.append(Check(scenario.id, f"{tag} → факт {fact}", fact in got["facts"],
                             f"факты ответа: {got['facts']}"))
        for path, want in exp.data.items():
            found = dig(got["data"], path)
            out.append(Check(scenario.id, f"{tag} → {path} = {want!r}", found == want, f"пришло {found!r}"))
        for path, part in exp.data_contains.items():
            found = dig(got["data"], path)
            out.append(Check(scenario.id, f"{tag} → {path} содержит {part!r}",
                             isinstance(found, str) and part in found, f"пришло {found!r}"))
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
