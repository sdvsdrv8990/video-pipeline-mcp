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

from tests.harness import live_server
from tests.harness.scenario import Journal, Runner, ScenarioError, Vocabulary, load
from tests.scenarios.steps import STEPS

HERE = Path(__file__).parent
JOURNAL_DIR = Path(__file__).resolve().parents[1] / ".journal"


def main() -> int:
    files = sorted(HERE.glob("*.yaml"))
    if not files:
        print("Нет ни одного файла сценариев — пустой прогон зелёным не считается.")
        return 1

    vocab = Vocabulary()
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
            only = os.environ.get("VPM_SCENARIO", "")
            scenarios = [s for s in scenarios if only in s.id] if only else scenarios
            if not scenarios:
                continue
            print(f"\n══ {path.name}: сценариев {len(scenarios)} ══")
            with live_server() as srv:
                srv.rpc.timeout = 180.0        # предел КЛИЕНТА: под coverage дерево с книгами идёт дольше
                runner = Runner(srv, journal, STEPS)
                for scenario in scenarios:
                    print(f"\n— {scenario.id}: {scenario.why}")
                    for check in runner.run(scenario):
                        total += 1
                        if check.ok:
                            print(f"  ✓ {check.label}")
                        else:
                            print(f"  ✗ {check.label}  → {check.detail}")
                            fails.append(f"{check.scenario} · {check.label} → {check.detail}")
    finally:
        journal.close()

    print(f"\n{'=' * 50}\nРЕЗУЛЬТАТ: {total - len(fails)}/{total} прошло · журнал: {journal.path}")
    if fails:
        print("ПРОВАЛЫ:")
        for line in fails:
            print(f"  - {line}")
        return 1
    print("ВСЁ ЗЕЛЁНОЕ ✅")
    return 0


def test_scenarios_suite():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
