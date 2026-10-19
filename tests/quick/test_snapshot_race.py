#!/usr/bin/env python3
"""tests/quick/test_snapshot_race.py — писатели одного снимка таблицы не теряют записей друг друга.

Причина рождается в потоках: списание расхода (`usage.charge`) из двух вызовов `media_generate` идёт в потоках пула
(offload), и оба читают, правят и пишут один `read.json`. Шов держит писателя у настоящей двери записи
(`_atomic_write_json` для `read.json`), пока к ней не придут все или не выйдет срок шва: без замка снимка чтение каждого
прошло до чужой записи, и одна прибавка пропадает; под замком (`StateManager.edit_snapshot`) писатели идут по очереди.
Следствие — на двери: оба списания ответили «было → стало», а в снимке после обоих — сумма прибавок.
"""
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import core.state.state_manager as _state_module
from core.providers.usage import UsageLedger
from core.state import StateManager

_checks = 0
_fails: list[str] = []
TABLE, SHEET, ROW = "ch/канал", "Провайдеры", "p1"
HOLD = 2.0          # срок шва у двери записи: под замком писатели приходят по одному, и шов отпускает их сроком


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def stand() -> tuple[StateManager, UsageLedger]:
    ws = Path(tempfile.mkdtemp(prefix="snapshot_race_")) / "workspace"
    ws.mkdir(parents=True)
    state = StateManager(ws)
    state.write_snapshot(TABLE, {SHEET: {"schema": {}, "rows": {ROW: {"current_usage": 0}}}})
    return state, UsageLedger(ROOT / "config" / "providers.yaml", state)


def usage(state: StateManager) -> object:
    return (state.read_snapshot(TABLE) or {}).get(SHEET, {}).get("rows", {}).get(ROW, {}).get("current_usage")


print("== Законный: списания по одному — сумма прибавок ==")
state, ledger = stand()
ledger.charge(TABLE, SHEET, ROW, 3)
ledger.charge(TABLE, SHEET, ROW, 4)
check("последовательно: расход 3 + 4 = 7", usage(state) == 7, usage(state))

print("== Гонка: два списания одной строки в двух потоках ==")
state, ledger = stand()
said: dict = {}
arrived: list[str] = []
everyone = threading.Event()
real_write = _state_module._atomic_write_json


def held(path: Path, data) -> None:
    if path.name == "read.json":
        arrived.append(threading.current_thread().name)
        if len(arrived) >= 2:
            everyone.set()
        everyone.wait(HOLD)
    real_write(path, data)


_state_module._atomic_write_json = held
try:
    writers = [threading.Thread(target=lambda key=key, amount=amount: said.__setitem__(
        key, ledger.charge(TABLE, SHEET, ROW, amount)), name=key) for key, amount in (("списание 3", 3), ("списание 4", 4))]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(60)
finally:
    _state_module._atomic_write_json = real_write
check("двери ответили: оба списания вернули «было → стало»",
      sorted(report["amount"] for report in said.values()) == [3, 4], said)
check("после обоих списаний в снимке сумма прибавок — ни одна не потеряна",
      usage(state) == 7, f"расход {usage(state)}; ответы {[(k, r['before'], r['after']) for k, r in said.items()]}; "
                         f"порядок у двери записи: {arrived}")

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
