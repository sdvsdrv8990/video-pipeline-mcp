#!/usr/bin/env python3
"""tests/quick/test_snapshot_race_queue.py — писатели одного снимка таблицы (две очереди и учёт расхода) не теряют записей.

Причина рождается в потоках: две `json_execute_queue` одной таблицы на двери движка и списание расхода (`usage.charge`)
читают, правят и пишут один `read.json` — каждый в своём потоке, как их исполняет сервер (offload: свой поток и свой
цикл на вызов). Шов держит писателя у настоящей двери записи (`_atomic_write_json` для `read.json`), пока к ней не
придут все: так чтение каждого гарантированно прошло до чужой записи, и потеря видна, если замка снимка нет. Следствие —
на двери: двери ответили «применено» и «списано», а в снимке после всех — обе строки очереди и расход.
"""
import asyncio
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import core.state.state_manager as _state_module
from core.engine import Engine
from core.ids import IDGenerator
from core.providers.usage import UsageLedger
from core.reactions import Reactions
from core.state import StateManager
from tests.harness.inprocess import register_basic_tools

_checks = 0
_fails: list[str] = []
TABLE, SHEET, ROW = "ch/канал", "Провайдеры", "p1"
HOLD = 2.0          # срок шва у двери записи: при замке снимка писатели приходят по одному, и шов отпускает их сроком


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def stand() -> tuple[StateManager, Engine, UsageLedger]:
    ws = Path(tempfile.mkdtemp(prefix="snapshot_race_")) / "workspace"
    ws.mkdir(parents=True)
    state = StateManager(ws)
    state.write_snapshot(TABLE, {SHEET: {"schema": {}, "rows": {ROW: {"current_usage": 0}}}})
    engine = Engine(reactions=Reactions(ROOT / "config" / "server_reactions.yaml"), state_manager=state)
    register_basic_tools(engine, IDGenerator(), state)
    return state, engine, UsageLedger(ROOT / "config" / "providers.yaml", state)


def append(row_id: str) -> dict:
    return {"action": "append", "sheet": SHEET, "row_id": row_id, "data": {"имя": row_id}}


def run_queue(engine: Engine, said: dict, key: str) -> None:
    result = asyncio.run(engine.call("json_execute_queue", {"table": TABLE}))
    said[key] = (result.data or {}).get("applied") if result.status == "success" else result.error.code


def landed(state: StateManager) -> dict:
    rows = (state.read_snapshot(TABLE) or {}).get(SHEET, {}).get("rows", {})
    return {"a1": "a1" in rows, "a2": "a2" in rows, "расход": rows.get(ROW, {}).get("current_usage")}


print("== Законный: писатели по одному — все записи на месте ==")
state, engine, ledger = stand()
said: dict = {}
state.push_to_queue(TABLE, append("a1"))
run_queue(engine, said, "A")
state.push_to_queue(TABLE, append("a2"))
run_queue(engine, said, "B")
said["C"] = ledger.charge(TABLE, SHEET, ROW, 3)["after"]
check("последовательно: обе строки очереди и расход в снимке",
      landed(state) == {"a1": True, "a2": True, "расход": 3}, (said, landed(state)))

print("== Гонка: две json_execute_queue одной таблицы и списание расхода в трёх потоках ==")
state, engine, ledger = stand()
said = {}
arrived: list[str] = []
first, everyone = threading.Event(), threading.Event()
real_write = _state_module._atomic_write_json


def held(path: Path, data) -> None:
    if path.name == "read.json":
        arrived.append(threading.current_thread().name)
        first.set()
        if len(arrived) >= 3:
            everyone.set()
        everyone.wait(HOLD)
    real_write(path, data)


_state_module._atomic_write_json = held
try:
    state.push_to_queue(TABLE, append("a1"))
    writers = [threading.Thread(target=run_queue, args=(engine, said, "A"), name="очередь A")]
    writers[0].start()
    first.wait(30)                          # A забрал a1 из очереди и дошёл до записи
    state.push_to_queue(TABLE, append("a2"))
    writers.append(threading.Thread(target=run_queue, args=(engine, said, "B"), name="очередь B"))
    writers.append(threading.Thread(target=lambda: said.__setitem__("C", ledger.charge(TABLE, SHEET, ROW, 3)["after"]),
                                    name="списание"))
    for writer in writers[1:]:
        writer.start()
    for writer in writers:
        writer.join(60)
finally:
    _state_module._atomic_write_json = real_write
after = landed(state)
check("двери ответили: обе очереди применили по строке, расход списан",
      said.get("A") == 1 and said.get("B") == 1 and said.get("C") == 3, said)
check("после всех писателей в снимке обе строки очереди и расход — ни одна запись не потеряна",
      after == {"a1": True, "a2": True, "расход": 3}, f"{after}; порядок у двери записи: {arrived}")

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
