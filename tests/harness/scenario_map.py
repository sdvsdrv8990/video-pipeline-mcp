"""tests/harness/scenario_map.py — карта сценария: состояния, переходы, пути.

Сценарий описывает ОДИН маршрут по системе, карта — все. Путь по карте и есть сценарий, поэтому
новый переход даёт новые сценарии без единой строчки кода.

## Границы
- Состояние — ПРЕДИКАТ над живым сервером, а не ярлык: у каждого свой `check`.
- Переход обязан менять наблюдаемое: после него предикат прежнего состояния не должен держаться,
  иначе состояния неразличимы и карта описывает вымысел.
- Разбор карты (недостижимое, тупики, непокрытые переходы) печатается ДО прогона: видно, к чему
  приведёт, ещё не сходив.
"""

from dataclasses import dataclass, field
from pathlib import Path


from .scenario import (ROOT, Check, Scenario, ScenarioError, Vocabulary, _reject_unknown, _steps,
                       dig, read_yaml)

MAP_KEYS = {"map", "why", "context", "start", "states", "transitions", "walk"}
STATE_KEYS = {"means", "check", "terminal"}
TRANSITION_KEYS = {"from", "to", "router", "via", "carries"}
# `by`, а не `on`: YAML читает `on:` как булево True, и ключ роутера пропадал бы молча.
ROUTER_KEYS = {"by", "table", "covers", "skip"}
WALK_KEYS = {"budget", "coverage"}


@dataclass
class State:
    name: str
    means: str
    check: list
    terminal: bool = False


@dataclass
class Transition:
    source: str
    target: str
    via: list
    carries: list[str] = field(default_factory=list)
    route: str = ""

    @property
    def name(self) -> str:
        return f"{self.source}→{self.target}" + (f" [{self.route}]" if self.route else "")


@dataclass
class ScenarioMap:
    id: str
    why: str
    context: dict
    start: str
    states: dict[str, State]
    transitions: list[Transition]
    budget: int
    coverage: str = "edges"


def _routes_of(body: dict, states: dict, where: str) -> list[tuple[str, str, dict, list]]:
    """Исходы перехода: один у обычного, по строке таблицы у роутера.

    Роутер — не «if покрасивее»: `if` ветвится ВНУТРИ одного пути, а таблица обязана быть пройдена
    ЦЕЛИКОМ — каждая строка становится своей веткой обхода. Забытая строка видна как непройденный
    переход, забытая ветка `if` не видна никак.
    """
    if not body.get("router"):
        target = str(body["to"])
        if target not in states:
            raise ScenarioError(f"{where}: состояние {target!r} не объявлено — обрыв в карте")
        return [("", target, {}, [])]
    router = body["router"]
    _reject_unknown(router or {}, ROUTER_KEYS, f"{where}.router")
    table = (router or {}).get("table") or {}
    if not router.get("by") or not table:
        raise ScenarioError(f"{where}.router: нужны `by` (что выбирает маршрут) и непустая `table`")
    _check_table_is_complete(router, table, where)
    out = []
    for key, row in table.items():
        # Строка таблицы — либо просто исход, либо исход со своими значениями: ветки роутера
        # отличаются не только именем маршрута, и подмена одного `${route}` их не выражает.
        target = str(row.get("to") or "") if isinstance(row, dict) else str(row)
        overrides = dict(row.get("with") or {}) if isinstance(row, dict) else {}
        facts = list(row.get("facts") or []) if isinstance(row, dict) else []
        if target not in states:
            raise ScenarioError(f"{where}.router.table[{key!r}]: состояние {target!r} не объявлено")
        out.append((str(key), target, overrides, facts))
    return out


def _check_table_is_complete(router: dict, table: dict, where: str) -> None:
    """Таблица роутера сверяется с ОБЪЯВЛЕННЫМ перечнем: забытая строка иначе просто не пойдёт в
    обход и промолчит — та же слепота, ради которой роутер и заводился.

    Пропуск разрешён, но только ИМЕНОВАННЫЙ: `skip: {значение: почему}` — умолчание «не покрываем»
    возвращает молчание через заднюю дверь.
    """
    source = str(router.get("covers") or "")
    if not source:
        return
    expected = {p.name.split(".")[0] for p in sorted(ROOT.glob(source))}
    if not expected:
        raise ScenarioError(f"{where}.router.covers: по {source!r} не нашлось ни одного объявления — "
                            "перечень пуст, и полнота таблицы ничем не проверяется")
    skip = dict(router.get("skip") or {})
    missing = sorted(expected - set(table) - set(skip))
    if missing:
        raise ScenarioError(f"{where}.router.table: перечень {source!r} даёт {sorted(expected)}, "
                            f"а в таблице нет {missing} — либо строка, либо `skip` с причиной")
    unknown = sorted((set(table) | set(skip)) - expected)
    if unknown:
        raise ScenarioError(f"{where}.router.table: {unknown} нет в перечне {source!r} — опечатка "
                            "либо перечень назван неверно")


def _substitute(via: list, key: str) -> list:
    """`${route}` в объявлении перехода — значение строки таблицы; подстановка статическая."""
    if not key:
        return via
    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            return node.replace("${route}", key)
        return node
    return walk(via)


def _coverage_level(value: str, where: str) -> str:
    if value not in ("edges", "pairs"):
        raise ScenarioError(f"{where}.walk.coverage: только `edges` или `pairs`, получено {value!r}")
    return value


def load_map(path: Path, vocab: Vocabulary) -> ScenarioMap:
    raw = read_yaml(path)
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path.name}: карта — словарь, получено {type(raw).__name__}")
    name = str(raw.get("map") or "")
    where = f"{path.name}:{name or '<без имени>'}"
    if not name:
        raise ScenarioError(f"{where}: у карты нет имени (`map`)")
    _reject_unknown(raw, MAP_KEYS, where)
    for required in ("why", "start", "states", "transitions"):
        if not raw.get(required):
            raise ScenarioError(f"{where}: нет `{required}`")

    states = {}
    for state_name, body in (raw["states"] or {}).items():
        _reject_unknown(body or {}, STATE_KEYS, f"{where}.states.{state_name}")
        if not (body or {}).get("check"):
            raise ScenarioError(
                f"{where}.states.{state_name}: состояние без `check` — это ярлык, а не состояние: "
                "предикат обязан проверяться на живом сервере")
        states[state_name] = State(name=state_name, means=str(body.get("means") or ""),
                                   check=_steps(body["check"], vocab, f"{where}.states.{state_name}.check"),
                                   terminal=bool(body.get("terminal")))
    if raw["start"] not in states:
        raise ScenarioError(f"{where}: стартовое состояние {raw['start']!r} не объявлено")

    transitions = []
    for i, body in enumerate(raw["transitions"], 1):
        _reject_unknown(body or {}, TRANSITION_KEYS, f"{where}.transitions[{i}]")
        source = str(body.get("from") or "")
        if source not in states:
            raise ScenarioError(f"{where}.transitions[{i}]: состояние {source!r} не объявлено — обрыв в карте")
        if not body.get("via"):
            raise ScenarioError(f"{where}.transitions[{i}]: нет `via` — переход без вызовов не переход")
        if bool(body.get("to")) == bool(body.get("router")):
            raise ScenarioError(f"{where}.transitions[{i}]: переход объявляет либо `to` (один исход), "
                                "либо `router` (таблица вход→исход), но не оба и не ни одного")
        for key, target, overrides, facts in _routes_of(body, states, f"{where}.transitions[{i}]"):
            via = _substitute(body["via"], key)
            if overrides or facts:
                via = [dict(step) for step in via]
                via[0]["with"] = {**(via[0].get("with") or {}), **overrides}
                if facts:
                    via[0]["expect"] = {**(via[0].get("expect") or {}), "facts": facts}
            transitions.append(Transition(
                source=source, target=target,
                via=_steps(via, vocab, f"{where}.transitions[{i}].via"),
                carries=list(body.get("carries") or []), route=key))

    walk = raw.get("walk") or {}
    _reject_unknown(walk, WALK_KEYS, f"{where}.walk")
    return ScenarioMap(id=name, why=str(raw["why"]).strip(), context=dict(raw.get("context") or {}),
                       start=str(raw["start"]), states=states, transitions=transitions,
                       budget=int(walk.get("budget") or 40),
                       coverage=_coverage_level(str(walk.get("coverage") or "edges"), where))


# ═══ Разбор карты ДО прогона: что вообще из неё следует ═══

def analyse(smap: ScenarioMap) -> list[str]:
    """Недостижимые состояния, тупики и переходы, до которых не дойти. Возвращает замечания."""
    notes = []
    reachable = {smap.start}
    frontier = [smap.start]
    while frontier:
        current = frontier.pop()
        for edge in smap.transitions:
            if edge.source == current and edge.target not in reachable:
                reachable.add(edge.target)
                frontier.append(edge.target)
    for name, state in smap.states.items():
        if name not in reachable:
            notes.append(f"состояние {name!r} недостижимо из {smap.start!r} — переход к нему не объявлен")
        outgoing = [e for e in smap.transitions if e.source == name]
        if not outgoing and not state.terminal:
            notes.append(f"состояние {name!r} — тупик: выхода нет, а `terminal: true` не объявлен")
    for edge in smap.transitions:
        if edge.source not in reachable:
            notes.append(f"переход {edge.name} недостижим: до {edge.source!r} не дойти")
    return notes


def _pairs(smap: ScenarioMap) -> list[tuple[int, int]]:
    """Пары соседних переходов: `a` привёл в состояние, из которого делается `b`.

    Порядок вызовов — отдельный источник поведения: покрытие каждого перехода по разу его не
    видит, потому что каждый переход в нём встречается ровно в одном окружении.
    """
    return [(i, j) for i, a in enumerate(smap.transitions)
            for j, b in enumerate(smap.transitions) if a.target == b.source]


def plan(smap: ScenarioMap) -> list[list[int]]:
    """Пути обхода. `coverage: edges` — каждый переход по разу; `pairs` — каждая ПАРА соседних."""
    if smap.coverage == "pairs":
        return _plan_pairs(smap)
    uncovered = set(range(len(smap.transitions)))
    paths: list[list[int]] = []
    while uncovered:
        # Самый глубокий непокрытый: его префикс заодно покрывает ранние переходы,
        # иначе обход вырождается в лестницу [0], [0,1], [0,1,2] и бьётся о лимит частоты.
        target_edge = max(uncovered)
        prefix = _route_to(smap, smap.transitions[target_edge].source)
        if prefix is None:
            raise ScenarioError(f"карта {smap.id}: до {smap.transitions[target_edge].source!r} нет пути от старта")
        path = prefix + [target_edge]
        if sum(len(p) for p in paths) + len(path) > smap.budget:
            raise ScenarioError(f"карта {smap.id}: обход не влезает в бюджет {smap.budget} шагов — "
                                "сузь карту или подними бюджет осознанно")
        paths.append(path)
        uncovered -= set(path)
    return paths


def _plan_pairs(smap: ScenarioMap) -> list[list[int]]:
    """Путь на каждую непокрытую пару: префикс до её начала плюс сама пара."""
    uncovered = set(_pairs(smap))
    paths: list[list[int]] = []
    spent = 0
    for first, second in sorted(uncovered):
        if (first, second) in {(p[k], p[k + 1]) for p in paths for k in range(len(p) - 1)}:
            continue
        prefix = _route_to(smap, smap.transitions[first].source)
        if prefix is None:
            raise ScenarioError(f"карта {smap.id}: до {smap.transitions[first].source!r} нет пути от старта")
        path = prefix + [first, second]
        spent += len(path)
        if spent > smap.budget:
            raise ScenarioError(f"карта {smap.id}: обход пар не влезает в бюджет {smap.budget} шагов — "
                                "подними `walk.budget` осознанно или вернись к `coverage: edges`")
        paths.append(path)
    return paths


def _route_to(smap: ScenarioMap, state: str) -> list[int] | None:
    """Кратчайший путь от старта до состояния (в индексах переходов)."""
    if state == smap.start:
        return []
    seen, queue = {smap.start}, [(smap.start, [])]
    while queue:
        current, acc = queue.pop(0)
        for i, edge in enumerate(smap.transitions):
            if edge.source != current or edge.target in seen:
                continue
            if edge.target == state:
                return acc + [i]
            seen.add(edge.target)
            queue.append((edge.target, acc + [i]))
    return None


# ═══ Прогон ═══

class MapRunner:
    """Ходит по карте: каждый путь — свой контекст имён, каждое прибытие проверяется предикатом."""

    def __init__(self, routes: dict[str, list[str]] | None = None):
        self.routes = routes or {}
        self.runner = None

    def walk_path(self, smap: ScenarioMap, path: list[int], number: int, runner) -> list[Check]:
        """Один путь = один СВОЙ сервер: файрвол считает частоту по IP, и длинный обход банил бы сам себя
        (тот же вывод, что у роя: фазы разнесены по процессам, а не ослаблен порог)."""
        self.runner = runner
        context = {key: f"{value}p{number}" for key, value in smap.context.items()}
        trip = f"{smap.id}#путь{number}"
        checks = self._state(smap, smap.start, trip, context, "старт")
        current = smap.start
        for index in path:
            edge = smap.transitions[index]
            checks += self._edge(edge, trip, context)
            checks += self._state(smap, edge.target, trip, context, f"после {edge.name}")
            if edge.source != edge.target:
                checks.append(self._left_previous(smap, current, trip, context, edge))
            current = edge.target
        return checks

    def _run(self, steps, trip: str, context: dict) -> list[Check]:
        pseudo = Scenario(id=trip, why="", files={}, when=steps, then=[], source=Path(trip))
        results: dict = {"ctx": context}
        out: list[Check] = []
        for step in steps:
            out += self.runner._step(pseudo, step, results)
        return out

    def _state(self, smap: ScenarioMap, name: str, trip: str, context: dict, when: str) -> list[Check]:
        state = smap.states[name]
        failed = [c for c in self._run(state.check, trip, context) if not c.ok]
        return [Check(trip, f"{when}: состояние {name} ({state.means})", not failed,
                      "; ".join(f"{c.label} {c.detail}" for c in failed[:2]))]

    def _edge(self, edge: Transition, trip: str, context: dict) -> list[Check]:
        before = len(self.runner.trace)
        out = self._run(edge.via, trip, context)
        moved = self.runner.trace[before:]
        for route in edge.carries:
            selectors = self.routes.get(route)
            if selectors is None:
                out.append(Check(trip, f"{edge.name}: маршрут {route}", False,
                                 "такого маршрута нет в tests/routes/routes.yaml — объявление разошлось"))
                continue
            for selector in selectors:
                seen = [_observe(entry, selector) for entry in moved]
                out.append(Check(trip, f"{edge.name}: маршрут {route} несёт {selector}",
                                 any(v not in (None, "", [], {}, "_НЕТ_", False) for v in seen),
                                 f"на переходе значения нет ({seen})"))
        return out

    def _left_previous(self, smap: ScenarioMap, previous: str, trip: str,
                       context: dict, edge: Transition) -> Check:
        """Переход обязан менять наблюдаемое: иначе состояния — ярлыки, а карта — вымысел."""
        still = [c for c in self._run(smap.states[previous].check, trip, context) if not c.ok]
        return Check(trip, f"{edge.name}: наблюдаемое изменилось (вышли из {previous})", bool(still),
                     f"предикат {previous} держится и после перехода — состояния неразличимы")


def _observe(entry: dict, selector: str):
    if selector.startswith("data."):
        return dig(entry.get("data") or {}, selector[len("data."):])
    return entry.get(selector)
