#!/usr/bin/env python3
"""tests/routes/test_routes.py — маршрутная таблица: доехало ли значение туда, куда объявлено.

Покрытие по строкам говорит «строка исполнилась», но не «значение доехало до клиента»: класс
реакции терялся на границе конверта при зелёном наборе. Здесь маршрут объявлен, а вердикт
берётся из СЛЕДА прогона сценария-опоры — что реально пришло на последнем рубеже.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml

from tests.harness import live_server
from tests.harness.scenario import (Runner, ScenarioError, Vocabulary, dig, known_findings, load,
                                    scenario_files)
from tests.scenarios.steps import STEPS

ROUTES = Path(__file__).with_name("routes.yaml")
SCENARIO_DIR = ROOT / "tests" / "scenarios"

ROUTE_KEYS = {"route", "what", "means", "decided_by", "hops", "proof"}
HOP_KEYS = {"at"}
PROOF_KEYS = {"scenario", "observe", "arrives", "finding"}

_checks, _fails = 0, []


def ok(cond, label, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  → {detail}")
        _fails.append(f"{label} → {detail}")


def _reject(got: dict, allowed: set, where: str):
    extra = set(got) - allowed
    if extra:
        raise ScenarioError(f"{where}: неизвестные ключи {sorted(extra)}")


def load_routes() -> list[dict]:
    """Объявление маршрутов. Кривое падает здесь — до сервера и до первого вызова."""
    raw = yaml.safe_load(ROUTES.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ScenarioError("routes.yaml: ожидается непустой список маршрутов")
    for item in raw:
        name = str((item or {}).get("route") or "")
        if not name:
            raise ScenarioError("routes.yaml: у маршрута нет имени")
        _reject(item, ROUTE_KEYS, f"routes.yaml:{name}")
        for required in ("what", "means", "decided_by", "hops", "proof"):
            if not item.get(required):
                raise ScenarioError(f"routes.yaml:{name}: нет `{required}` — маршрут без него не проверяем")
        for hop in item["hops"]:
            _reject(hop, HOP_KEYS, f"routes.yaml:{name}.hops")
        proof = item["proof"]
        _reject(proof, PROOF_KEYS, f"routes.yaml:{name}.proof")
        if "arrives" not in proof or not proof.get("observe"):
            raise ScenarioError(f"routes.yaml:{name}.proof: нужны `observe` и `arrives`")
        if not proof["arrives"] and not proof.get("finding"):
            raise ScenarioError(
                f"routes.yaml:{name}.proof: разрыв без адреса находки — «не доезжает» обязано ссылаться на F#")
        known = known_findings()
        if proof.get("finding") and known and str(proof["finding"]) not in known:
            raise ScenarioError(f"routes.yaml:{name}.proof: находки {proof['finding']} нет в реестре — "
                                "адрес разрыва ведёт в пустоту")
    return raw


def anchors_exist(routes: list[dict]) -> None:
    """Рубеж и то, что выбирает маршрут, — реальные файлы: переименовали слой → маршрут порван."""
    print("\n== Рубежи маршрутов существуют на диске ==")
    for route in routes:
        for target in dict.fromkeys([route["decided_by"]] + [h["at"] for h in route["hops"]]):
            # Рубеж-описание («конверт MCP», «консоль сервера») пути не имеет; всё остальное —
            # путь, и он обязан существовать. Проверять по косой черте нельзя: `server.py` её
            # не содержит и молча выпадал бы из проверки.
            if " " in target or target.endswith(("MCP content", "structuredContent")):
                continue
            ok((ROOT / target).exists(), f"{route['route']}: рубеж {target}", "файла нет — объявление отстало")


def observe(entry: dict, selector: str):
    """Значение селектора в записи следа: поле, путь внутри data или образец по консоли."""
    if selector.startswith("data."):
        return dig(entry.get("data") or {}, selector[len("data."):])
    if selector.startswith("console:"):
        text = "\n".join(entry.get("console") or [])
        found = re.search(selector[len("console:"):], text, re.I)
        return found.group(0) if found else ""
    return entry.get(selector)


def _filled(value) -> bool:
    return value not in (None, "", [], {}, "_НЕТ_", False)


DECLARES = {
    "code": lambda e: bool(e.code),
    "reaction_class": lambda e: bool(e.reaction_class),
    "recovery": lambda e: e.recovery is not None,
    "facts": lambda e: bool(e.facts),
}


def declares(expect, selector: str) -> bool:
    """Объявляет ли ожидание сценария ту же наблюдаемую, что и маршрут."""
    if expect is None:
        return False
    if selector.startswith("data."):
        # Совпадать обязан ПУТЬ, а не сам факт объявления данных: «в сценарии есть какой-то data»
        # засчитывало кандидатом почти каждый сценарий, и список переставал что-либо значить.
        path = selector[len("data."):]
        return any(key == path or key.startswith(path.split(".")[0] + ".") or path.startswith(key + ".")
                   for key in list(expect.data) + list(expect.data_contains))
    if selector.startswith("console:"):
        return bool(expect.console)
    return DECLARES.get(selector, lambda _: False)(expect)


def explain(route: dict, steps: list[dict], selector: str) -> str:
    """ГДЕ цепочка ещё цела. Без этого отказ звучит «значения нет нигде», и чинить идут в конец
    потока, хотя оборвалось в середине: соседнее значение, доехавшее на том же шаге, показывает,
    что конверт дошёл и потеряно ровно объявленное поле."""
    prefix = _parent(selector)
    while prefix:
        where = [e["step"] for e in steps if _filled(observe(e, prefix))]
        if where:
            return (f"цепочка цела до `{prefix}` (шаг {where[0]}), обрывается на "
                    f"`{selector[len(prefix) + 1:]}`")
        prefix = _parent(prefix)
    siblings = {other: [e["step"] for e in steps if _filled(observe(e, other))]
                for other in route["proof"]["observe"] if other != selector}
    arrived = {name: at for name, at in siblings.items() if at}
    if arrived:
        return (f"на шаге {min(min(v) for v in arrived.values())} доехали {', '.join(sorted(arrived))} — "
                f"конверт дошёл, потеряно ровно `{selector}`: ищи между тем, кто его кладёт, и концом")
    first = route["hops"][0]["at"] if route["hops"] else route["decided_by"]
    return (f"ни одно объявленное значение не доехало ни на одном шаге — обрыв РАНЬШЕ первого "
            f"рубежа `{first}`, а не в конце потока")


def _parent(selector: str) -> str | None:
    """`data.results.0.trust` → `data.results.0`; у верхнеуровневого и у `console:` родителя нет."""
    if selector.startswith("console:"):
        return None
    head, sep, _ = selector.rpartition(".")
    return head if sep else None


def self_check() -> None:
    """Локализация обрыва проверяется подставленным следом: живой прогон даёт только ЗЕЛЁНЫЙ путь,
    а сообщение об обрыве читают именно тогда, когда всё сломано — и проверить его тогда уже нечем."""
    print("\n== Локализация обрыва и карта опор (самопроверка) ==")
    route = {"route": "r", "decided_by": "config/x.yaml", "hops": [{"at": "core/a.py"}],
             "proof": {"observe": ["code", "reaction_class"]}}
    partial = [{"step": 3, "code": "X", "reaction_class": "", "data": {}}]
    ok("потеряно ровно `reaction_class`" in explain(route, partial, "reaction_class"),
       "обрыв назван полем, а не «значения нет нигде»")
    ok("шаге 3" in explain(route, partial, "reaction_class"), "назван шаг, на котором цепочка была цела")
    nothing = [{"step": 1, "code": "", "reaction_class": "", "data": {}}]
    ok("РАНЬШЕ первого рубежа `core/a.py`" in explain(route, nothing, "reaction_class"),
       "пустой след указывает на начало потока, а не на конец")
    deep = {"route": "d", "decided_by": "d", "hops": [], "proof": {"observe": ["data.a.b.c"]}}
    ok("цела до `data.a` " in explain(deep, [{"step": 2, "data": {"a": {"x": 1}}}], "data.a.b.c"),
       "у вложенного значения назван последний целый уровень")

    class _Exp:
        code = ""; reaction_class = ""; recovery = None; facts = []; console = ""
        data = {"content.trust": "x"}; data_contains = {}
    ok(declares(_Exp(), "data.content.trust"), "путь данных совпал — сценарий годится в опоры")
    ok(not declares(_Exp(), "data.results.0.trust"), "чужой путь данных кандидатом не делает")
    ok(not declares(None, "code"), "шаг без ожиданий кандидатом не считается")


def main() -> int:
    routes = load_routes()
    print(f"Маршрутов объявлено: {len(routes)}")
    self_check()
    anchors_exist(routes)

    by_file: dict[str, set] = {}
    for route in routes:
        source, _, scenario_id = str(route["proof"]["scenario"]).partition("#")
        by_file.setdefault(source, set()).add(scenario_id)

    vocab = Vocabulary()
    traces: dict[str, list[dict]] = {}
    broken_proofs: dict[str, str] = {}
    for source, wanted in sorted(by_file.items()):
        path = SCENARIO_DIR / source
        if not path.exists():
            for name in wanted:
                broken_proofs[name] = f"файла сценариев {source} нет"
            continue
        available = {s.id: s for s in load(path, vocab)}
        missing = wanted - set(available)
        for name in missing:
            broken_proofs[name] = f"сценария {name} нет в {source} — опора маршрута исчезла"
        run = [available[n] for n in sorted(wanted & set(available))]
        if not run:
            continue
        print(f"\n== Прогон опор из {source}: {[s.id for s in run]} ==")
        with live_server() as srv:
            srv.rpc.timeout = 180.0
            runner = Runner(srv, steps=STEPS)
            for scenario in run:
                failed = [c for c in runner.run(scenario) if not c.ok]
                if failed:
                    broken_proofs[scenario.id] = f"опора не отработала: {failed[0].label} → {failed[0].detail[:120]}"
            for entry in runner.trace:
                traces.setdefault(entry["scenario"], []).append(entry)

    print("\n== Значение доезжает туда, куда объявлено ==")
    for route in routes:
        proof = route["proof"]
        _, _, scenario_id = str(proof["scenario"]).partition("#")
        if scenario_id in broken_proofs:
            ok(False, f"{route['route']}: опора {scenario_id}", broken_proofs[scenario_id])
            continue
        steps = traces.get(scenario_id) or []
        for selector in proof["observe"]:
            seen = [observe(entry, selector) for entry in steps]
            arrived = any(_filled(value) for value in seen)
            if proof["arrives"]:
                ok(arrived, f"{route['route']}: {selector} доезжает",
                   explain(route, steps, selector))
            else:
                # Отсутствие значения доказывает разрыв только там, где значение вообще может
                # появиться: у опечатки в селекторе следа не бывает НИКОГДА, и «разрыв подтверждён»
                # зеленело бы вечно — в том числе после починки разрыва.
                parent = _parent(selector)
                observable = parent is None or any(_filled(observe(e, parent)) for e in steps)
                if not observable:
                    ok(False, f"{route['route']}: {selector} наблюдаем",
                       f"ни на одном шаге {scenario_id} нет даже родителя `{parent}` — "
                       "это опечатка в объявлении, а не подтверждённый разрыв")
                    continue
                ok(not arrived,
                   f"{route['route']}: {selector} НЕ доезжает — разрыв {proof['finding']} подтверждён",
                   f"значение появилось ({[v for v in seen if _filled(v)]}) — разрыв закрыт, обнови "
                   f"{proof['finding']} в docs/roadmap/02_findings.md и переверни `arrives`")

    served = {str(r["proof"]["scenario"]).partition("#")[2] for r in routes}
    every = [s for path in scenario_files(SCENARIO_DIR) for s in load(path, vocab)]
    all_ids = {s.id for s in every}
    idle = sorted(all_ids - served)
    print(f"\nСценариев всего {len(all_ids)}, опорой маршрута служат {len(served)}; "
          f"остальные проверяют контракт, а не поток: {idle}")

    print("\n== Что ЕЩЁ может держать маршрут (по объявлению, без прогона) ==")
    for route in routes:
        support = str(route["proof"]["scenario"]).partition("#")[2]
        wanted = [s for s in route["proof"]["observe"]]
        able = sorted(s.id for s in every if s.id != support
                      and all(any(declares(step.expect, sel) for step in s.when + s.then)
                              for sel in wanted))
        if able:
            print(f"  {route['route']}: опора {support}; те же значения объявляют ещё {len(able)} — "
                  f"{', '.join(able[:6])}{' …' if len(able) > 6 else ''}")
        else:
            print(f"  ⚠ {route['route']}: опора {support} ЕДИНСТВЕННАЯ — сценария, объявляющего "
                  f"{', '.join(wanted)}, больше нет; упадёт она — поток перестанет проверяться вовсе")

    print(f"\n{'=' * 50}\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
    if _fails:
        print("ПРОВАЛЫ:")
        for line in _fails:
            print(f"  - {line}")
        return 1
    print("ВСЁ ЗЕЛЁНОЕ ✅")
    return 0


def test_routes_suite():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
