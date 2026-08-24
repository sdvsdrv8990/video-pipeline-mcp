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
from tests.harness.scenario import Runner, ScenarioError, Vocabulary, dig, load
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
    return raw


def anchors_exist(routes: list[dict]) -> None:
    """Рубеж и то, что выбирает маршрут, — реальные файлы: переименовали слой → маршрут порван."""
    print("\n== Рубежи маршрутов существуют на диске ==")
    for route in routes:
        for target in dict.fromkeys([route["decided_by"]] + [h["at"] for h in route["hops"]]):
            if "/" not in target:
                continue                       # рубеж-описание («конверт MCP», «консоль сервера»)
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


def main() -> int:
    routes = load_routes()
    print(f"Маршрутов объявлено: {len(routes)}")
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
                   f"ни на одном шаге {scenario_id} значения нет ({[v for v in seen if _filled(v)] or 'пусто везде'})")
            else:
                ok(not arrived,
                   f"{route['route']}: {selector} НЕ доезжает — разрыв {proof['finding']} подтверждён",
                   f"значение появилось ({[v for v in seen if _filled(v)]}) — разрыв закрыт, обнови "
                   f"{proof['finding']} в docs/roadmap/02_findings.md и переверни `arrives`")

    served = {str(r["proof"]["scenario"]).partition("#")[2] for r in routes}
    all_ids = {s.id for path in sorted(SCENARIO_DIR.glob("*.yaml")) for s in load(path, vocab)}
    idle = sorted(all_ids - served)
    print(f"\nСценариев всего {len(all_ids)}, опорой маршрута служат {len(served)}; "
          f"остальные проверяют контракт, а не поток: {idle}")

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
