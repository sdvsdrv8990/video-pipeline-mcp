"""Приспособление «узел заданий» (сценарии TASK_*): в хранилище узла сервера сценария засеяны три задания настоящими
путями узла, id каждого — в журнале приспособления `MCP_LOGS/task_node.jsonl` (одна строка {interrupted, stalled,
broken}):
- interrupted — задание начал дочерний процесс и умер: запись незаконченная, замок свободен (TASK_INTERRUPTED);
- stalled — живой поток этого процесса держит замок, признак работы старше `stale_after_sec` (TASK_STALLED);
- broken — запись задания испорчена (TASK_RECORD_INVALID).
Засев кончается исходом или отказом приспособления: бесконечного ожидания в нём нет.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))      # корень цели: core

from core.engine.engine import Engine
from core.jobs import Tasks, issue

_DIED = ("import os, threading\nfrom core.engine.engine import Engine\nfrom core.jobs import Tasks\n"
         "entry = Tasks(Engine()).begin('сценарий', {'засев': 'прерванное'}, 'любой',\n"
         "                              lambda beat: threading.Event().wait())\n"
         "print(entry['task_id'], flush=True)\nos._exit(0)\n")


def _died() -> str:
    """Задание процесса, который умер посреди работы: ядро сняло его замок, запись осталась незаконченной."""
    done = subprocess.run([sys.executable, "-c", _DIED], cwd=ROOT, capture_output=True, text=True, check=True)
    return done.stdout.split()[-1]


def _silent() -> str:
    """Живой исполнитель, чей последний признак работы старше любого предела простоя: часы засева — эпоха 0."""
    node = Tasks(Engine(), clock=lambda: 0.0)
    task_id = node.begin("сценарий", {"засев": "молчун"}, "любой", lambda beat: threading.Event().wait())["task_id"]
    entry = node.status([task_id])[0]
    # Место в свежем движке есть сразу: pending кончается работой или смертью исполнителя (замок свободен — прерван).
    while (entry.get("task") or {}).get("status") == "pending":
        time.sleep(0.01)
        entry = node.status([task_id])[0]
    if (entry.get("task") or {}).get("status") != "processing":
        raise RuntimeError(f"молчун не дошёл до работы: {entry}")
    return task_id


def _broken() -> str:
    """Задание с выданным id, чья запись на настоящем пути узла испорчена: исполнителя и ожидания нет."""
    task_id = issue()
    Tasks(Engine()).record_file(task_id).write_text("{испорченная запись", encoding="utf-8")
    return task_id


_unset = [name for name in ("MCP_HOME", "MCP_LOGS") if not os.environ.get(name)]
if _unset:
    raise SystemExit(f"task_node: не задано {', '.join(_unset)} — засев лёг бы в дом инстанса по умолчанию (живое дерево "
                     "проекта); приспособление ставят только со своим домом и журналом: сервер сценария или набор")
journal = Path(os.environ["MCP_LOGS"]) / "task_node.jsonl"
journal.parent.mkdir(parents=True, exist_ok=True)
with journal.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"interrupted": _died(), "stalled": _silent(), "broken": _broken()}) + "\n")
