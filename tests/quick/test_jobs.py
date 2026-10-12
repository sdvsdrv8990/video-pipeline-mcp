#!/usr/bin/env python3
"""tests/quick/test_jobs.py — узел заданий: id задания выдаёт и судит одна точка по декларации `jobs.yaml`.

Истина — форма из декларации прогона (`MCP_CONFIG` на копии деклараций), а не литерал теста: правка формы в декларации
меняет выдачу, битая декларация — отказ двери с путём ключа. Каждый род — парой с законным.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml

from core.contracts import ContractError, ToolResult
from core.declaration_schemas import Resources
from core.engine.engine import Engine
from core.jobs import Tasks, issue, issued
from core.reactions import Reactions
from tests.harness.live_server import declarations_copy

_checks = 0
_fails: list[str] = []


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


PROBE = ("import json\nfrom core.contracts import ContractError\nfrom core.jobs import issue, issued\n"
         "try:\n    one = issue()\n    print(json.dumps({'id': one, 'own': issued(one), 'foreign': issued('z' * len(one))}))\n"
         "except ContractError as e:\n    print(json.dumps({'code': e.code, 'facts': e.facts}))\n")


def child(config: Path) -> dict:
    """Узел в чистом процессе на каталоге деклараций прогона — исход выдачи или отказ двери."""
    done = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env={**os.environ, "MCP_CONFIG": str(config)},
                          capture_output=True, text=True)
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return {"crash": done.stderr[-400:]}


print("== 1. Выдача и суд формы: законный id — свой; чужая длина, чужой знак, не строка — не свои ==")
one = issue()
check(f"законный: выданный id своей формы и узнаётся судом ({one})", isinstance(one, str) and issued(one), one)
many = [issue() for _ in range(2000)]
check("2000 выдач — ни одного повтора: id случаен, а не счётчик", len(set(many)) == len(many), len(set(many)))
for case, foreign in (("длиннее", one + "0"), ("короче", one[:-1]), ("чужой знак", one[:-1] + "Z"),
                      ("путь наружу", "../" + one[3:]), ("не строка", 12), ("пусто", None)):
    check(f"{case} — не id узла", issued(foreign) is False, foreign)

print("== 2. Форма — из декларации прогона, а не литерал: правка формы меняет выдачу; битая — отказ двери ==")
shaped = child(declarations_copy({"jobs.yaml": {"id.alphabet": "ab", "id.length": 8}}))
check("алфавит «ab», длина 8 в декларации — id из 8 знаков a/b, свой; id из чужого знака — не свой",
      len(shaped.get("id", "")) == 8 and set(shaped["id"]) <= {"a", "b"} and shaped.get("own") is True
      and shaped.get("foreign") is False, shaped)
broken = child(declarations_copy({"jobs.yaml": {"id.length": 0}}))
check("длина 0 — DECLARATION_INVALID с путём ключа, а не id из пустоты",
      broken.get("code") == "DECLARATION_INVALID" and broken.get("facts", {}).get("file") == "jobs.yaml"
      and "id.length" in broken.get("facts", {}).get("details", ""), broken)
missing = declarations_copy()
(missing / "jobs.yaml").unlink()
gone = child(missing)
check("нет jobs.yaml — TEMPLATE_NOT_FOUND, а не форма по умолчанию в коде", gone.get("code") == "TEMPLATE_NOT_FOUND", gone)

print("== 3. Места — один счёт движка и заданий (CP-05): занятое заданием место видит вызов; ждущих не обгоняют ==")
RES = Resources.model_validate({"default": "пробный",
                                "classes": {"пробный": {"offload": True, "max_concurrent": 1, "retry_after_sec": 7}},
                                "tools": {"занятой": "пробный"}, "work": {}})


async def _quick():
    return ToolResult(status="success", data={})


eng = Engine(resources=RES)
eng.register("занятой", "проба счёта", {"type": "object", "properties": {}}, _quick)
call = lambda: asyncio.run(eng.call("занятой", {}))
check("законный: место свободно — вызов идёт", call().status == "success")
eng.slots.wait("пробный")                               # место взяло задание узла
busy = call()
check("место держит задание — вызов движка RESOURCE_BUSY: счёт один, а не у каждого свой",
      busy.status == "error" and busy.error.code == "RESOURCE_BUSY", busy.error and busy.error.code)
order: list[str] = []
queued = threading.Thread(target=lambda: (eng.slots.wait("пробный"), order.append("задание")))
queued.start()
deadline = time.monotonic() + 5
while not eng.slots._queue["пробный"] and time.monotonic() < deadline:   # ждущий встал в очередь класса
    time.sleep(0.001)
eng.slots.give("пробный")                               # первое задание отдало место
queued.join(5)
check("место ушло ждущему заданию, а не вызову: вызов не обгоняет очередь",
      order == ["задание"] and call().status == "error", order)
eng.slots.give("пробный")
check("ждущих нет, место свободно — вызов идёт", call().status == "success")
try:
    eng.slots.give("пробный")
    over = "принято"
except RuntimeError as e:
    over = str(e)
check("отдать невзятое место — ошибка кода, а не лишнее место", "без взятия" in over, over)

print("== 4. Узел: занято — pending; отказ работы — в задании; прерванное, простой; плохой id и битая — в элементе ==")
JOBS = yaml.safe_load((ROOT / "config" / "jobs.yaml").read_text(encoding="utf-8"))
now = [1000.0]
root = Path(tempfile.mkdtemp(prefix="tasks_"))
store = root / JOBS["store"]
reng = Engine(reactions=Reactions(ROOT / "config" / "server_reactions.yaml"), resources=RES)
reng.register("занятой", "проба счёта", {"type": "object", "properties": {}}, _quick)
node = Tasks(reng, root=root, clock=lambda: now[0])


def settle(task_id: str, wanted: set[str]) -> dict:
    """Статус задания, как только он среди ожидаемых (исполнитель — фоновый поток), иначе последний увиденный."""
    deadline = time.monotonic() + 5
    entry = node.status([task_id])[0]
    while (entry.get("task") or {}).get("status") not in wanted and time.monotonic() < deadline:
        time.sleep(0.005)
        entry = node.status([task_id])[0]
    return entry.get("task") or entry


def held(gate: threading.Event, result: dict):
    def work(beat):
        beat({"шаг": 1})
        gate.wait(5)
        return result
    return work


gate_a, gate_b = threading.Event(), threading.Event()
a_id = node.begin("проба", {"x": 1, "api_key": "секрет-канарейка"}, "пробный", held(gate_a, {"ok": "a"}))["task_id"]
a = settle(a_id, {"processing"})
check("законный: задание взяло место — processing; секрет параметров замаскирован",
      a.get("status") == "processing" and "секрет-канарейка" not in json.dumps(a, ensure_ascii=False), a)
b = node.begin("проба", {"шаг": "второе"}, "пробный", held(gate_b, {"ok": "b"}))
check("N+1 при пределе N=1 — второе задание pending, а не отказ", b.get("task", {}).get("status") == "pending", b)
held_call = asyncio.run(reng.call("занятой", {}))
check("место держит задание узла — вызов движка того же класса RESOURCE_BUSY: счёт один",
      held_call.status == "error" and held_call.error.code == "RESOURCE_BUSY", held_call.error and held_call.error.code)
gate_a.set()
check("первое отдало место — исход completed с результатом", settle(a_id, {"completed"}).get("result") == {"ok": "a"})
check("ждущее получило место — processing", settle(b["task_id"], {"processing"}).get("status") == "processing")
gate_b.set()
check("второе — completed", settle(b["task_id"], {"completed"}).get("result") == {"ok": "b"})


def refusing(beat):
    raise ContractError("PROVIDER_FAILED", facts={"host": "api.provider.example", "status": 500})


def crashing(beat):
    raise RuntimeError("путь /srv/секрет")


failed = settle(node.begin("проба", {"шаг": "отказ"}, "пробный", refusing)["task_id"], {"failed"})
check("отказ работы — задание failed с отказом реестра (код, класс), место отпущено",
      (failed.get("error") or {}).get("code") == "PROVIDER_FAILED" and failed["error"].get("reaction_class")
      and asyncio.run(reng.call("занятой", {})).status == "success", failed)
crashed = settle(node.begin("проба", {"шаг": "падение"}, "пробный", crashing)["task_id"], {"failed"})
check("падение работы — INTERNAL_ERROR с номером события, без текста исключения",
      (crashed.get("error") or {}).get("code") == "INTERNAL_ERROR" and "событие" in crashed["error"].get("message", "")
      and "/srv/секрет" not in json.dumps(crashed, ensure_ascii=False), crashed)

orphan = issue()
store.mkdir(parents=True, exist_ok=True)
GRAPH = {"parent": issue(), "children": [issue()], "inputs": [{"id": "вход", "sha256": "0" * 64}],
         "outputs": [{"id": "выход", "sha256": "f" * 64}]}
(store / f"{orphan}.json").write_text(json.dumps({
    "tool": "проба", "klass": "пробный", "created": 1.0, "heartbeat": 1.0, "finished": None,
    "task": {"task_id": orphan, "status": "processing", "params": {}}, **GRAPH}), encoding="utf-8")
lost = settle(orphan, {"failed"})
check("незаконченное без исполнителя (замок свободен) — сразу TASK_INTERRUPTED с id и инструментом",
      (lost.get("error") or {}).get("code") == "TASK_INTERRUPTED"
      and lost["error"].get("facts") == {"task_id": orphan, "tool": "проба"}, lost)
rewritten = json.loads((store / f"{orphan}.json").read_text(encoding="utf-8"))
check("прерванное записано: следующее чтение — тот же исход, а не снова processing",
      rewritten["task"]["status"] == "failed")
check("граф записи (родитель, дети, входы и выходы с хешами) переживает перезапись узлом",
      {key: rewritten.get(key) for key in GRAPH} == GRAPH, rewritten)

gate_s = threading.Event()
s_id = node.begin("проба", {"шаг": "простой"}, "пробный", held(gate_s, {"ok": "s"}))["task_id"]
settle(s_id, {"processing"})
now[0] += JOBS["stale_after_sec"] + 1
stalled = node.status([s_id])[0]["task"]
check("живой исполнитель молчит дольше stale_after_sec — TASK_STALLED с секундами (в ответе)",
      (stalled.get("error") or {}).get("code") == "TASK_STALLED"
      and stalled["error"]["facts"].get("seconds") == JOBS["stale_after_sec"] + 1, stalled)
gate_s.set()
check("простой — не приговор: исполнитель дописал — completed", settle(s_id, {"completed"}).get("result") == {"ok": "s"})

broken = issue()
(store / f"{broken}.json").write_text("{битая запись канарейка", encoding="utf-8")
foreign, absent = "../" + broken[3:], issue()
try:
    mixed = node.status([a_id, absent, broken, foreign])
except ContractError as e:                  # один плохой id отказал всему набору: своих ответов у элементов нет
    mixed = [{"отказ всего вызова": e.code}] * 4
check("набор «живой, несуществующий, битый, чужой» — четыре элемента, вызов не падает",
      [e.get("task_id") for e in mixed] == [a_id, absent, broken, foreign], mixed)
check("у каждого элемента ровно одно из task и error",
      all((e.get("task") is None) != (e.get("error") is None) for e in mixed), mixed)
check("несуществующий и чужой по форме id — error TASK_NOT_FOUND в своём элементе",
      [(e.get("error") or {}).get("code") for e in mixed[1::2]] == ["TASK_NOT_FOUND", "TASK_NOT_FOUND"], mixed)
check("битая запись — error TASK_RECORD_INVALID с путём от корня, без содержимого, а не «task failed»",
      mixed[2].get("task") is None and (mixed[2].get("error") or {}).get("code") == "TASK_RECORD_INVALID"
      and mixed[2]["error"]["facts"] == {"path": f"{JOBS['store']}/{broken}.json"}
      and "канарейка" not in json.dumps(mixed[2], ensure_ascii=False), mixed[2])
now[0] += JOBS["retain_sec"] + 1
listed = {e["task_id"] for e in node.status()}
check("список после retain_sec: конченые убраны, битая запись осталась уликой",
      a_id not in listed and broken in listed and (store / f"{broken}.json").exists(), sorted(listed))

print("== 5. Приспособление task_node (сценарии TASK_*): засев настоящими путями узла даёт три кода в элементах ==")
SEEDED = ("import json, os\nfrom pathlib import Path\nfrom core.declaration import declarations_root\n"
          "from core.engine.engine import Engine\nfrom core.jobs import Tasks\nfrom core.reactions import Reactions\n"
          "ids = json.loads((Path(os.environ['MCP_LOGS']) / 'task_node.jsonl').read_text().splitlines()[-1])\n"
          "node = Tasks(Engine(reactions=Reactions(declarations_root() / 'server_reactions.yaml')))\n"
          "print(json.dumps({k: node.status([v])[0] for k, v in ids.items()}, ensure_ascii=False))\n")
logs = Path(tempfile.mkdtemp(prefix="task_node_"))
env = {k: v for k, v in os.environ.items() if k not in ("VPM_FIXTURES", "MCP_LOGS", "MCP_CONFIG")}
env.update(PYTHONPATH=str(ROOT / "tests" / "harness" / "fixtures"), VPM_FIXTURES="task_node",
           MCP_CONFIG=str(declarations_copy()), MCP_LOGS=str(logs))
done = subprocess.run([sys.executable, "-c", SEEDED], cwd=ROOT, env=env, capture_output=True, text=True)
try:
    seeded = json.loads(done.stdout.strip().splitlines()[-1])
except (IndexError, ValueError):
    seeded = {"crash": done.stderr[-400:]}
codes = {kind: ((entry.get("task") or {}).get("error") or entry.get("error") or {}).get("code")
         for kind, entry in seeded.items() if isinstance(entry, dict)}
check("засев: прерванное — TASK_INTERRUPTED, молчун — TASK_STALLED (в task.error), битая — TASK_RECORD_INVALID (error)",
      codes == {"interrupted": "TASK_INTERRUPTED", "stalled": "TASK_STALLED", "broken": "TASK_RECORD_INVALID"}
      and seeded["broken"].get("task") is None, (codes, done.stderr[-300:]))

print("== 6. Повтор того же вызова — то же задание (AD-21); конец работы — событием; ход без признака работы ==")
now6 = [5000.0]
node6 = Tasks(reng, root=Path(tempfile.mkdtemp(prefix="tasks6_")), clock=lambda: now6[0])
runs: list[str] = []


def counted(name: str, gate: threading.Event | None = None):
    def work(beat):
        runs.append(name)
        if gate is not None:
            gate.wait(5)
        return {"сделано": name}
    return work


def call6(secret: str) -> dict:
    return {"x": 1, "api_key": secret}


gate6 = threading.Event()
first = node6.begin("проба", call6("ключ-один"), "любой", counted("первый", gate6))["task_id"]
again = node6.begin("проба", call6("ключ-два"), "любой", counted("повтор"))["task_id"]
gate6.set()
done6 = node6.settle(first, 5).get("task") or {}
check("повтор, пока идёт, — тот же id и одна работа; другой ключ доступа — тот же вызов",
      again == first and done6.get("status") == "completed" and runs == ["первый"], (first, again, runs))
check("повтор после конца в окне — тот же id готового задания, работа не повторена",
      node6.begin("проба", call6("ключ-три"), "любой", counted("после"))["task_id"] == first and runs == ["первый"],
      runs)
fresh = node6.begin("проба", call6("ключ-один"), "любой", counted("заново"), fresh=True)["task_id"]
node6.settle(fresh, 5)
check("fresh — новое задание и вторая работа, слово «заново» в записи",
      fresh != first and runs == ["первый", "заново"]
      and json.loads(node6.record_file(fresh).read_text(encoding="utf-8"))["fresh"] is True, (fresh, runs))
other = node6.begin("проба", {"x": 2}, "любой", counted("другой"))["task_id"]
node6.settle(other, 5)
check("другой аргумент — другое задание", other not in (first, fresh) and runs[-1] == "другой", runs)
keyed = node6.begin("проба", {"x": 3, "шум": 1}, "любой", counted("ключ"), key={"x": 3})["task_id"]
node6.settle(keyed, 5)
check("вызов судится ключом вызова, а не параметрами записи: тот же ключ при других параметрах — то же задание",
      node6.begin("проба", {"x": 3, "шум": 2}, "любой", counted("лишний"), key={"x": 3})["task_id"] == keyed
      and "лишний" not in runs, runs)
now6[0] += JOBS["dedupe_window_sec"] + 1
late = node6.begin("проба", call6("ключ-один"), "любой", counted("вне окна"))["task_id"]
node6.settle(late, 5)
check("вне окна повтора — новое задание", late not in (first, fresh) and runs[-1] == "вне окна", runs)


def failing(beat):
    runs.append("отказ")
    raise ContractError("PROVIDER_FAILED", facts={"host": "api.provider.example", "status": 500})


lost6 = node6.begin("проба", {"x": 4}, "любой", failing)["task_id"]
node6.settle(lost6, 5)
retry6 = node6.begin("проба", {"x": 4}, "любой", counted("после отказа"))["task_id"]
node6.settle(retry6, 5)
check("кончившееся отказом в окне — заводится заново: повтор после отказа — это попытка, а не дубль",
      retry6 != lost6 and runs[-2:] == ["отказ", "после отказа"], (lost6, retry6, runs))

started = time.monotonic()
quick = node6.settle(node6.begin("проба", {"x": 5}, "любой", counted("быстрый"))["task_id"], 5)
quick_sec = time.monotonic() - started
check("settle: работа кончилась — элемент сразу по событию, а не по сроку ожидания",
      (quick.get("task") or {}).get("status") == "completed" and quick_sec < 1, f"{quick_sec:.3f} с")
gate_slow = threading.Event()
slow_id = node6.begin("проба", {"x": 6}, "любой", counted("медленный", gate_slow))["task_id"]
started = time.monotonic()
slow = node6.settle(slow_id, 0.2)
slow_sec = time.monotonic() - started
check("settle: работа идёт — ждёт не дольше срока и отдаёт processing",
      (slow.get("task") or {}).get("status") == "processing" and 0.2 <= slow_sec < 0.7, f"{slow_sec:.3f} с")
gate_slow.set()
check("тот же settle после конца работы — completed с результатом",
      (node6.settle(slow_id, 5).get("task") or {}).get("result") == {"сделано": "медленный"})
started = time.monotonic()
strange = node6.settle(issue(), 5)
check("settle по id без исполнителя в этом процессе — элемент сразу: TASK_NOT_FOUND без ожидания",
      (strange.get("error") or {}).get("code") == "TASK_NOT_FOUND" and time.monotonic() - started < 1, strange)

working6, ticked, dripped, resumed, finish = (threading.Event() for _ in range(5))
go_on = threading.Event()


def drip(beat):
    working6.set()                          # processing записан: последний признак работы — сейчас
    ticked.wait(5)
    beat({"bytes": 10}, working=False)      # на полсрока позже — ход есть, признака работы нет
    dripped.set()
    go_on.wait(5)
    beat({"bytes": 20})
    resumed.set()
    finish.wait(5)
    return {"bytes": 20}


drip_id = node6.begin("проба", {"x": 7}, "любой", drip)["task_id"]
half = JOBS["stale_after_sec"] // 2
working6.wait(5)
now6[0] += half
ticked.set()
dripped.wait(5)
now6[0] += JOBS["stale_after_sec"] - half + 1        # от признака работы — stale_after_sec + 1, от хода — меньше
drip_seen = node6.status([drip_id])[0]["task"]
check("ход без признака работы (working=False) пишет ход, но простой не отодвигает — TASK_STALLED с ходом",
      (drip_seen.get("error") or {}).get("code") == "TASK_STALLED" and drip_seen.get("progress") == {"bytes": 10},
      drip_seen)
go_on.set()
resumed.wait(5)
drip_back = node6.status([drip_id])[0]["task"]
check("признак работы вернулся — снова processing, ход новый",
      drip_back.get("status") == "processing" and drip_back.get("progress") == {"bytes": 20}, drip_back)
finish.set()
node6.settle(drip_id, 5)

wide = child(declarations_copy({"jobs.yaml": {"dedupe_window_sec": JOBS["retain_sec"] + 1}}))
check("окно повтора длиннее срока хранения — DECLARATION_INVALID с путём ключа: запись не дожила бы до конца окна",
      wide.get("code") == "DECLARATION_INVALID" and "dedupe_window_sec" in wide.get("facts", {}).get("details", ""), wide)
edge = child(declarations_copy({"jobs.yaml": {"dedupe_window_sec": JOBS["retain_sec"]}}))
check("законный: окно равно сроку хранения — декларация проходит", edge.get("own") is True, edge)

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
