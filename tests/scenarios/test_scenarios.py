#!/usr/bin/env python3
"""tests/scenarios/test_scenarios.py — прогон объявленных сценариев против живого сервера.

Один файл сценариев = один сервер: область общая внутри файла (как у клиента), между файлами —
своя, и частота вызовов не копится через весь набор до бана по IP.
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml

from tests.harness import live_server
from tests.harness.scenario import Journal, Runner, ScenarioError, Vocabulary, load, scenario_files
from tests.harness.scenario_map import MapRunner, analyse, load_map, plan
from tests.scenarios.steps import STEPS

HERE = Path(__file__).parent
JOURNAL_DIR = Path(__file__).resolve().parents[1] / ".journal"


def main() -> int:
    files = scenario_files(HERE)
    maps = sorted(HERE.glob("*.map.yaml"))
    if not files and not maps:
        print("Нет ни одного файла сценариев — пустой прогон зелёным не считается.")
        return 1

    vocab = Vocabulary()
    ran: set[str] = set()
    journal = Journal(JOURNAL_DIR / f"scenarios-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    total, fails = 0, []
    try:
        for path in files:
            try:
                scenarios = load(path, vocab)
            except ScenarioError as exc:
                print(f"\n══ {path.name}: ОБЪЯВЛЕНИЕ НЕ ЗАГРУЖЕНО ══\n  {exc}")
                fails.append(f"{path.name}: {exc}")
                continue
            # Отбор — список через запятую: точечный прогон задетой правкой части
            # (`blast_radius --affected` печатает готовую строку).
            only = [part for part in os.environ.get("VPM_SCENARIO", "").split(",") if part]
            scenarios = [s for s in scenarios if any(part in s.id for part in only)] if only else scenarios
            if not scenarios:
                continue
            print(f"\n══ {path.name}: сценариев {len(scenarios)} ══")
            with live_server() as srv:
                srv.rpc.timeout = 180.0        # предел КЛИЕНТА: под coverage дерево с книгами идёт дольше
                runner = Runner(srv, journal, STEPS)
                for scenario in scenarios:
                    ran.add(scenario.id)
                    print(f"\n— {scenario.id}: {scenario.why}")
                    for check in runner.run(scenario):
                        total += 1
                        # Метка всегда с именем сценария: по ней сравниваются ДВА прогона
                        # (diff поведения), а шаговые метки в разных сценариях совпадают.
                        if check.ok:
                            print(f"  ✓ {check.scenario} · {check.label}")
                        else:
                            print(f"  ✗ {check.scenario} · {check.label}  → {check.detail}")
                            fails.append(f"{check.scenario} · {check.label} → {check.detail}")
        only = [part for part in os.environ.get("VPM_SCENARIO", "").split(",") if part]
        for path in maps:
            # Отбор применяется и к картам: иначе «точечный прогон» тянул всю карту целиком.
            if only and not any(part.split("#")[0] in path.stem for part in only):
                continue
            total, fails = _walk_map(path, vocab, journal, total, fails, ran)
    except BaseException:
        # Авария (сервер не поднялся, порт занят, харнесс упал) — это НЕ зелёный прогон.
        # Улика, объявляющая успех тому, чего не было, хуже отсутствия улики.
        journal.verdict(ok=False, total=total, failed=len(fails), scenarios=sorted(ran), crashed=True)
        journal.close()
        raise
    journal.verdict(ok=not fails, total=total, failed=len(fails), scenarios=sorted(ran))
    journal.close()

    print(f"\n{'=' * 50}\nРЕЗУЛЬТАТ: {total - len(fails)}/{total} прошло · журнал: {journal.path}")
    if fails:
        print("ПРОВАЛЫ:")
        for line in fails:
            print(f"  - {line}")
        return 1
    print("ВСЁ ЗЕЛЁНОЕ ✅")
    return 0


def _routes() -> dict[str, list[str]]:
    """Селекторы маршрутов: переход карты объявляет, ЧТО обязан донести, а список полей — у маршрута."""
    declared = ROOT / "tests" / "routes" / "routes.yaml"
    if not declared.exists():
        return {}
    return {r["route"]: list(r["proof"]["observe"])
            for r in yaml.safe_load(declared.read_text(encoding="utf-8"))
            if r.get("proof", {}).get("arrives")}


def _walk_map(path: Path, vocab: Vocabulary, journal: Journal, total: int, fails: list, ran: set):
    """Карта: сначала РАЗБОР (что из неё следует), потом обход, покрывающий каждый переход."""
    try:
        smap = load_map(path, vocab)
    except ScenarioError as exc:
        print(f"\n══ {path.name}: КАРТА НЕ ЗАГРУЖЕНА ══\n  {exc}")
        return total + 1, fails + [f"{path.name}: {exc}"]

    print(f"\n══ карта {smap.id}: {smap.why} ══")
    notes = analyse(smap)
    print(f"  состояний {len(smap.states)}, переходов {len(smap.transitions)}")
    for note in notes:
        print(f"  ⚠ {note}")
    paths = plan(smap)
    print(f"  обход покрывает каждый переход: путей {len(paths)}, шагов {sum(len(p) for p in paths)}")
    for i, route in enumerate(paths, 1):
        print(f"    путь{i}: " + " → ".join([smap.start] + [smap.transitions[e].target for e in route]))
    total += 1
    if notes:
        fails.append(f"{smap.id}: карта не сходится — {notes[0]}")

    walker = MapRunner(_routes())
    for number, route in enumerate(paths, 1):
        with live_server() as srv:            # свой сервер на путь: см. walk_path
            srv.rpc.timeout = 180.0
            ran.add(smap.id)                  # в улику попадает пройденное, а не запланированное
            for check in walker.walk_path(smap, route, number, Runner(srv, journal, STEPS)):
                total += 1
                if check.ok:
                    print(f"  ✓ {check.scenario} · {check.label}")
                else:
                    print(f"  ✗ {check.scenario} · {check.label}  → {check.detail}")
                    fails.append(f"{check.scenario} · {check.label} → {check.detail}")
    return total, fails


def test_scenarios_suite():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
