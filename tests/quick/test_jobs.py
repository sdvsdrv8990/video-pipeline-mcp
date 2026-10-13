#!/usr/bin/env python3
"""tests/quick/test_jobs.py — узел заданий: id задания выдаёт и судит одна точка по декларации `jobs.yaml`.

Истина — форма из декларации прогона (`MCP_CONFIG` на копии деклараций), а не литерал теста: правка формы в декларации
меняет выдачу, битая декларация — отказ двери с путём ключа. Каждый род — парой с законным.
"""
import asyncio
import atexit
import json
import logging
import os
import shutil
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


def scratch(prefix: str) -> Path:
    """Временный каталог события; убирается при выходе процесса — после набора читателя у него нет."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


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

RES3 = Resources.model_validate({"default": "пробный",
                                 "classes": {"пробный": {"offload": True, "max_concurrent": 1, "retry_after_sec": 7}},
                                 "tools": {"долгий": "пробный"}, "work": {}})
eng3 = Engine(resources=RES3)
started3, gate3 = threading.Event(), threading.Event()


async def _long():
    started3.set()
    gate3.wait(5)
    return ToolResult(status="success", data={})


eng3.register("долгий", "проба отмены", {"type": "object", "properties": {}}, _long)


async def _abandoned_call() -> tuple[bool, bool]:
    """Вызов offload отменён посреди работы (так приходит обрыв клиента при отмене хендлера): место занято, пока поток не
    кончил; кончил — место свободно."""
    call3 = asyncio.create_task(eng3.call("долгий", {}))
    deadline = time.monotonic() + 5
    while not started3.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.001)
    call3.cancel()
    try:
        await call3
    except asyncio.CancelledError:
        pass
    early = eng3.slots.take("пробный")
    if early:
        eng3.slots.give("пробный")
    gate3.set()
    late, deadline = False, time.monotonic() + 5
    while not late and time.monotonic() < deadline:
        late = eng3.slots.take("пробный")
        await asyncio.sleep(0 if late else 0.01)
    if late:
        eng3.slots.give("пробный")
    return not early, late


held3, freed3 = asyncio.run(_abandoned_call())
check("вызов, снятый с цикла в поток, отменён посреди работы (обрыв клиента) — место занято, пока поток не кончил; "
      "кончил — место отдано: отмена ожидающего не пускает вторую работу поверх живой",
      held3 and freed3, (held3, freed3))

print("== 4. Узел: занято — pending; отказ работы — в задании; прерванное, простой; плохой id и битая — в элементе ==")
JOBS = yaml.safe_load((ROOT / "config" / "jobs.yaml").read_text(encoding="utf-8"))
now = [1000.0]
root = scratch("tasks_")
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
b_waited = (node.settle(b["task_id"], 0.2).get("task") or {}).get("status")
check("N+1 при пределе N=1 — второе задание pending и через 0,2 с (ждёт места), а не отказ и не работа",
      b.get("task", {}).get("status") == "pending" and b_waited == "pending", (b, b_waited))
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

# Обновлено в шаге 1 рендера: простой больше не вердикт сервера (слово владельца) — живое молчащее задание несёт факты
# хода, решает клиент. Прежде: «молчит дольше stale_after_sec — TASK_STALLED» и «простой — не приговор».
now_s, gate_s, beaten_s = [2000.0], threading.Event(), threading.Event()
node_s = Tasks(reng, root=scratch("tasks_s_"), clock=lambda: now_s[0])


def silent(beat):
    beat({"шаг": 1})                        # последний признак работы — в 2000 с эпохи
    beaten_s.set()
    gate_s.wait(5)
    return {"ok": "s"}


s_id = node_s.begin("проба", {"шаг": "молчит"}, "любой", silent)["task_id"]
beaten_s.wait(5)
now_s[0] += 7 * 86400
quiet_s = node_s.status([s_id])[0]["task"]
check("S1: живой исполнитель молчит неделю — processing с фактами хода (last_work_at прежний, idle_sec — неделя), "
      "ни failed, ни отказа по часам",
      quiet_s.get("status") == "processing" and quiet_s.get("error") is None
      and quiet_s.get("last_work_at") == "1970-01-01T00:33:20+00:00" and quiet_s.get("idle_sec") == 7 * 86400, quiet_s)
gate_s.set()
done_s = node_s.settle(s_id, 5).get("task") or {}
check("законный: молчавший дописал — completed с результатом, у конечного фактов хода нет",
      done_s.get("result") == {"ok": "s"} and done_s.get("last_work_at") is None and done_s.get("idle_sec") is None,
      done_s)

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
logs = scratch("task_node_")
env = {k: v for k, v in os.environ.items() if k not in ("VPM_FIXTURES", "MCP_LOGS", "MCP_CONFIG", "MCP_HOME")}
env.update(PYTHONPATH=str(ROOT / "tests" / "harness" / "fixtures"), VPM_FIXTURES="task_node",
           MCP_CONFIG=str(declarations_copy()), MCP_LOGS=str(logs), MCP_HOME=str(scratch("task_node_home_")))
done = subprocess.run([sys.executable, "-c", SEEDED], cwd=ROOT, env=env, capture_output=True, text=True)
try:
    seeded = json.loads(done.stdout.strip().splitlines()[-1])
except (IndexError, ValueError):
    seeded = {"crash": done.stderr[-400:]}
codes = {kind: ((entry.get("task") or {}).get("error") or entry.get("error") or {}).get("code")
         for kind, entry in seeded.items() if isinstance(entry, dict)}
quiet5 = (seeded.get("stalled") or {}).get("task") or {}
# Обновлено в шаге 1 рендера: молчун — processing с фактами хода, а не TASK_STALLED (простой больше не вердикт сервера).
check("засев: прерванное — TASK_INTERRUPTED (в task.error), битая — TASK_RECORD_INVALID (error), молчун — processing "
      "с последним признаком работы в эпохе 0 и idle_sec от неё, чужой — живой (его замок держит дочерний процесс)",
      codes == {"interrupted": "TASK_INTERRUPTED", "stalled": None, "broken": "TASK_RECORD_INVALID", "foreign": None}
      and seeded["broken"].get("task") is None and quiet5.get("status") == "processing"
      and ((seeded.get("foreign") or {}).get("task") or {}).get("status") in ("pending", "processing")
      and quiet5.get("last_work_at") == "1970-01-01T00:00:00+00:00" and quiet5.get("idle_sec", 0) > 10 ** 9,
      (codes, quiet5, done.stderr[-300:]))

print("== 6. Повтор того же вызова — то же задание (AD-21); конец работы — событием; ход без признака работы ==")
now6 = [5000.0]
node6 = Tasks(reng, root=scratch("tasks6_"), clock=lambda: now6[0])
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


# Обновлено в шаге 1 рендера: ход без признака работы не отодвигает last_work_at, idle_sec растёт; прежде — TASK_STALLED.
drip_id = node6.begin("проба", {"x": 7}, "любой", drip)["task_id"]
working6.wait(5)
began6 = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now6[0]))
now6[0] += 600
ticked.set()
dripped.wait(5)
now6[0] += 600                                      # от признака работы — 1200 с, от хода — 600
drip_seen = node6.status([drip_id])[0]["task"]
check("S2: ход без признака работы (working=False) — ход новый, last_work_at прежний, idle_sec растёт от признака, "
      "а не от хода",
      drip_seen.get("status") == "processing" and drip_seen.get("progress") == {"bytes": 10}
      and drip_seen.get("last_work_at") == began6 and drip_seen.get("idle_sec") == 1200, (began6, drip_seen))
go_on.set()
resumed.wait(5)
drip_back = node6.status([drip_id])[0]["task"]
check("признак работы вернулся — processing, ход новый, idle_sec с нуля",
      drip_back.get("status") == "processing" and drip_back.get("progress") == {"bytes": 20}
      and drip_back.get("idle_sec") == 0, drip_back)
finish.set()
node6.settle(drip_id, 5)

wide = child(declarations_copy({"jobs.yaml": {"dedupe_window_sec": JOBS["retain_sec"] + 1}}))
check("окно повтора длиннее срока хранения — DECLARATION_INVALID с путём ключа: запись не дожила бы до конца окна",
      wide.get("code") == "DECLARATION_INVALID" and "dedupe_window_sec" in wide.get("facts", {}).get("details", ""), wide)
edge = child(declarations_copy({"jobs.yaml": {"dedupe_window_sec": JOBS["retain_sec"]}}))
check("законный: окно равно сроку хранения — декларация проходит", edge.get("own") is True, edge)

print("== 7. Работа ждёт чужое — до снятия (S3); список по инструменту; место класса work — у задания ==")
# Обновлено в шаге 1 рендера: суда тишины у узла нет — «правило простоя» и «ждёт дольше срока — TASK_STALLED» сняты
# (слово владельца: простой — не вердикт сервера); ждущая работа кончается снятием.
now7 = [9000.0]
root7 = scratch("tasks7_")
node7 = Tasks(reng, root=root7, clock=lambda: now7[0])
waiting7 = threading.Event()


class _Said(logging.Handler):
    """Строки журнала узла — улика отказанной поздней записи."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


said7 = _Said()
logging.getLogger("core.jobs").addHandler(said7)
logging.getLogger("core.jobs").setLevel(logging.INFO)


heard7: list[bool] = []


def awaiting(pulse):
    """Работа ждёт чужое до снятия, как установка у чужого замка модели (cancel_check), и снятия не слушается: её итог
    после снятия — поздняя запись."""
    waiting7.set()
    heard7.append(pulse.cancelled.wait(5))
    return {"поздно": 1}


w7 = node7.begin("ожидание", {"x": 1}, "любой", awaiting)["task_id"]
waiting7.wait(5)
calm = node7.status([w7])[0]["task"]
check("законный: ждёт — processing", calm.get("status") == "processing", calm)
now7[0] += 7 * 86400
week7 = node7.settle(w7, 0.2).get("task") or {}
check("ждёт неделю — processing с idle_sec недели: узел не кончает работу по часам",
      week7.get("status") == "processing" and week7.get("idle_sec") == 7 * 86400, week7)
task7 = node7.cancel([w7])[0].get("task") or {}
check("S3: снятие молчащего — cancelled с причиной TASK_CANCELLED (id, инструмент), результата нет",
      task7.get("status") == "cancelled" and (task7.get("error") or {}).get("code") == "TASK_CANCELLED"
      and task7["error"].get("facts") == {"task_id": w7, "tool": "ожидание"} and task7.get("result") is None, task7)
after7 = node7.settle(w7, 5).get("task") or {}
check("сигнал дошёл до работы (её ожидание кончилось снятием, а не сроком), её поздний итог — отказ узла с фактом в "
      "журнале: cancelled необратим",
      heard7 == [True] and after7.get("status") == "cancelled" and after7.get("result") is None
      and any(w7 in line and "итог работы после cancelled отказано" in line for line in said7.lines),
      (heard7, after7, said7.lines))
cut7 = node7.begin("ожидание", {"x": 9}, "любой", lambda pulse: (pulse.cancelled.wait(5), {"ok": 1})[1])["task_id"]
node7.cancel([cut7])
again7 = node7.begin("ожидание", {"x": 9}, "любой", lambda pulse: {"заново": 1})["task_id"]
node7.settle(again7, 5)
check("повтор вызова в окне повтора после снятия — новое задание, а не снятое (рецепт TASK_CANCELLED — повторить вызов)",
      again7 != cut7 and (node7.status([again7])[0].get("task") or {}).get("result") == {"заново": 1}, (cut7, again7))
done7 = node7.begin("другой", {"x": 2}, "любой", lambda pulse: {"ok": 1})["task_id"]
node7.settle(done7, 5)
cancel_set = node7.cancel([w7, done7, issue()])
check("набор «снятое, кончившееся, несуществующее» — три элемента-отказа, вызов не падает: TASK_FINISHED с конечным "
      "статусом (повторное снятие — с cancelled), TASK_NOT_FOUND",
      [(e.get("error") or {}).get("code") for e in cancel_set] == ["TASK_FINISHED", "TASK_FINISHED", "TASK_NOT_FOUND"]
      and [((e.get("error") or {}).get("facts") or {}).get("status") for e in cancel_set[:2]]
      == ["cancelled", "completed"], cancel_set)
check("снятие кончившегося — статус прежний, а не тихий успех",
      (node7.status([done7])[0].get("task") or {}).get("status") == "completed")
broken7 = issue()
(root7 / JOBS["store"] / f"{broken7}.json").write_text("{битая запись", encoding="utf-8")
by_tool = {entry["task_id"] for entry in node7.status(tool="ожидание")}
check("список по инструменту — его задания и битые записи (их инструмент не прочесть), чужого инструмента — нет",
      by_tool == {w7, cut7, again7, broken7}, sorted(by_tool))
check("законный: без инструмента — все записи", len(node7.status()) == 5, len(node7.status()))
class Hiding:
    """Дверь файлов, чей обход не видит одно имя — так прячет имя записи `os.replace` на btrfs; остальное — прежней."""

    def __init__(self, files, hidden: str):
        self._files, self._hidden = files, hidden

    def walk(self, path=""):
        return [entry for entry in self._files.walk(path) if Path(entry.path).name != self._hidden]

    def __getattr__(self, name):
        return getattr(self._files, name)


gate_hidden = threading.Event()
hidden7 = node7.begin("скрытая", {"x": 3}, "любой", held(gate_hidden, {"ok": 1}))["task_id"]
files7 = node7.files
node7.files = Hiding(files7, f"{hidden7}.json")
twin7 = node7.begin("скрытая", {"x": 3}, "любой", held(threading.Event(), {"ok": 2}))["task_id"]
listed7 = {entry["task_id"] for entry in node7.status(tool="скрытая")}
node7.files = files7
gate_hidden.set()
node7.settle(hidden7, 5)
check("обход каталога не увидел имя записи живого задания (так прячет его os.replace на btrfs) — задание найдено по "
      "замку записи: повтор — тот же id (AD-21), список его называет", twin7 == hidden7 and hidden7 in listed7,
      (hidden7, twin7, sorted(listed7)))

RES7 = Resources.model_validate({
    "default": "пробный", "classes": {"пробный": {"offload": True, "max_concurrent": 1, "retry_after_sec": 7}},
    "tools": {"занятой": "пробный"}, "work": {"загрузка": {"max_concurrent": 1}}})
eng7 = Engine(reactions=Reactions(ROOT / "config" / "server_reactions.yaml"), resources=RES7)
eng7.register("занятой", "проба счёта", {"type": "object", "properties": {}}, _quick)
node7b = Tasks(eng7, root=scratch("tasks7b_"))
began7, gate7 = threading.Event(), threading.Event()


def loading(pulse):
    began7.set()
    gate7.wait(5)
    return {"ok": "загрузка"}


one7 = node7b.begin("установка", {"m": 1}, "загрузка", loading)["task_id"]
began7.wait(5)
two7 = node7b.begin("установка", {"m": 2}, "загрузка", lambda pulse: {"ok": "вторая"})
waited7 = (node7b.settle(two7["task_id"], 0.2).get("task") or {}).get("status")
check("класс work с одним местом — вторая работа того же класса ждёт места (pending и через 0,2 с, хотя её работа "
      "мгновенна): место ждёт задание, а не вызов", waited7 == "pending", waited7)
check("место работы не занимает места вызовов: вызов своего класса идёт, пока загрузка держит своё",
      asyncio.run(eng7.call("занятой", {})).status == "success")
gate7.set()
check("первая отдала место — вторая доделана",
      (node7b.settle(one7, 5).get("task") or {}).get("status") == "completed"
      and (node7b.settle(two7["task_id"], 5).get("task") or {}).get("result") == {"ok": "вторая"}, two7["task_id"])

print("== 8. Снятие ждущего места; ожидание смены статуса (task_status wait); исполнитель другого процесса ==")
root8 = scratch("tasks8_")
node8 = Tasks(eng7, root=root8)
began8, tick8, ticked8, gate8, gate9 = (threading.Event() for _ in range(5))
ran8: list[str] = []


def holding(pulse):
    began8.set()
    tick8.wait(5)
    pulse({"шаг": 1})                       # ход — не смена статуса
    ticked8.set()
    gate8.wait(5)
    return {"ok": "держатель"}


def queued8(pulse):
    ran8.append("снятая")
    return {"ok": "снятая"}


def waited(ids: list[str]) -> tuple[threading.Thread, dict]:
    """Ожидание смены статуса в своём потоке — как вызов task_status(wait=true)."""
    seen: dict = {}
    thread = threading.Thread(target=lambda: seen.update(entries=asyncio.run(node8.waiting(ids))))
    thread.start()
    return thread, seen


def said8(seen: dict) -> list:
    return [(e.get("task") or {}).get("status") for e in seen.get("entries", [])]


hold8 = node8.begin("держит", {"m": 1}, "загрузка", holding)["task_id"]
began8.wait(5)
pend8 = node8.begin("ждёт", {"m": 2}, "загрузка", queued8)["task_id"]
on_pend, seen_pend = waited([pend8])
on_pend.join(0.2)
check("S7: ожидание на pending не отвечает, пока статус прежний", on_pend.is_alive(), seen_pend)
pend_task = node8.cancel([pend8])[0].get("task") or {}
on_pend.join(5)
check("снятие pending — cancelled; ожидание ответило на смену статуса — cancelled",
      pend_task.get("status") == "cancelled" and said8(seen_pend) == ["cancelled"], (pend_task, seen_pend))
node8.settle(pend8, 5)
check("снятое в очереди ушло из неё, не взяв места: работа не начиналась, очередь класса пуста",
      ran8 == [] and not eng7.slots._queue["загрузка"], (ran8, list(eng7.slots._queue["загрузка"])))
pend9 = node8.begin("ждёт", {"m": 3}, "загрузка", held(gate9, {"ok": "третья"}))["task_id"]
on_hold, seen_hold = waited([hold8])
on_next, seen_next = waited([pend9])
tick8.set()
ticked8.wait(5)
on_hold.join(0.2)
check("ход работы (тик) сменой статуса не считается — ожидание не ответило", on_hold.is_alive(), seen_hold)
gate8.set()
on_hold.join(5)
on_next.join(5)
check("конец работы — ожидание ответило completed; ждавшее место — processing (первая смена, а не конец)",
      said8(seen_hold) == ["completed"] and said8(seen_next) == ["processing"], (seen_hold, seen_next))
gate9.set()
node8.settle(pend9, 5)
started = time.monotonic()
idle8 = asyncio.run(node8.waiting([hold8, issue()]))
check("законный: живых исполнителей этого процесса среди ждущих нет — ответ сразу, без ожидания",
      [(e.get("task") or {}).get("status") or e["error"]["code"] for e in idle8] == ["completed", "TASK_NOT_FOUND"]
      and time.monotonic() - started < 1, idle8)

gate_own8 = threading.Event()
own8 = node8.begin("своё", {"m": 10}, "любой", held(gate_own8, {"ok": "своё"}))["task_id"]
node8.settle(own8, 0.2)
on_own, seen_own = waited([own8])
node8.settle(node8.begin("чужое", {"m": 11}, "любой", lambda pulse: {"ok": "чужое"})["task_id"], 5)
on_own.join(0.2)
check("смена статуса другого задания ожиданию своего не отвечает: ждёт смены именно своего", on_own.is_alive(),
      seen_own)
gate_own8.set()
on_own.join(5)
check("своё кончилось — ответ completed", said8(seen_own) == ["completed"], seen_own)

from core import jobs as _jobs


async def _abandon(task_id: str) -> tuple[int, int, bool]:
    """Ожидающий, чей вызов отменён (так приходит обрыв клиента при отмене хендлера), — слушатели узла до и после."""
    before = len(_jobs._HEARD)
    waiter = asyncio.create_task(node8.waiting([task_id]))
    while len(_jobs._HEARD) == before and not waiter.done():   # слушатель встал — ожидание началось
        await asyncio.sleep(0)
    waiter.cancel()
    try:
        await waiter
    except asyncio.CancelledError:
        pass
    return before, len(_jobs._HEARD), waiter.cancelled()


gate_ab8 = threading.Event()
left8 = node8.begin("брошенное", {"m": 12}, "любой", held(gate_ab8, {"ok": 1}))["task_id"]
node8.settle(left8, 0.2)
heard_before, heard_after, was_cancelled = asyncio.run(_abandon(left8))
gate_ab8.set()
node8.settle(left8, 5)
check("ожидание без потока и места: отменённый ожидающий (обрыв клиента) снимает слушателя — у узла не остаётся "
      "ни слушателя, ни ждущего потока", was_cancelled and heard_after == heard_before,
      (heard_before, heard_after, was_cancelled))

from aiohttp import ClientSession, ClientTimeout, web


async def _dropped(cancel_on_drop: bool, task_id: str) -> tuple[int, int]:
    """Клиент оборвал ожидание по HTTP (настоящий aiohttp на петле): слушатели узла до запроса и после обрыва."""
    async def handler(request):
        return web.json_response(await node8.waiting([task_id]))

    app = web.Application()
    app.router.add_post("/", handler)
    runner = web.AppRunner(app, handler_cancellation=cancel_on_drop, shutdown_timeout=0.5)   # контроль висит — не ждать 60 с
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    before = len(_jobs._HEARD)
    try:
        async with ClientSession(timeout=ClientTimeout(total=0.3)) as client:
            await client.post(f"http://127.0.0.1:{runner.addresses[0][1]}/")
    except asyncio.TimeoutError:
        pass
    deadline = time.monotonic() + 3
    while len(_jobs._HEARD) > before and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    after = len(_jobs._HEARD)
    await runner.cleanup()
    return before, after


gate_drop8 = threading.Event()
drop8 = node8.begin("оборванное", {"m": 13}, "любой", held(gate_drop8, {"ok": 1}))["task_id"]
node8.settle(drop8, 0.2)
cut_before, cut_after = asyncio.run(_dropped(True, drop8))
kept_before, kept_after = asyncio.run(_dropped(False, drop8))
gate_drop8.set()
node8.settle(drop8, 5)
check("клиент оборвал task_status(wait) по HTTP — с отменой хендлера (server.py) у узла не остаётся слушателя; без неё "
      "слушатель висит до смены статуса (контроль: событие различает флаг)",
      cut_after == cut_before and kept_after == kept_before + 1, (cut_before, cut_after, kept_before, kept_after))

from core.observability.trail import Trail


async def _dropped_traced(task_id: str, trail_dir: Path, stop: bool = False) -> list[dict]:
    """Вызов движка по HTTP отменён до ответа — обрывом клиента или остановкой сервера (`stop`: метка остановки, затем
    cleanup, как server.py): что легло в след (живой оракул брошенного ожидания)."""
    traced = Engine(trail=Trail(trail_dir, keep_files=1, max_bytes=0, record_args=False))

    async def awaiting_call():
        return ToolResult(status="success", data={"tasks": await node8.waiting([task_id])})

    traced.register("ждать", "ожидание смены статуса", {"type": "object", "properties": {}}, awaiting_call)

    async def handler(request):
        return web.json_response((await traced.call("ждать", {})).model_dump())

    app = web.Application()
    app.router.add_post("/", handler)
    runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=0.5)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}/"
    if stop:
        async with ClientSession(timeout=ClientTimeout(total=10)) as client:
            asked = asyncio.ensure_future(client.post(url))
            heard, deadline = len(_jobs._HEARD), time.monotonic() + 3
            while len(_jobs._HEARD) == heard and time.monotonic() < deadline:   # ожидание встало
                await asyncio.sleep(0.01)
            traced.stopping = True
            await runner.cleanup()
            try:
                await asyncio.wait_for(asked, 3)
            except Exception:                   # сервер закрыл соединение — ответа клиенту нет, это и меряется
                pass
    else:
        try:
            async with ClientSession(timeout=ClientTimeout(total=0.3)) as client:
                await client.post(url)
        except asyncio.TimeoutError:
            pass
    records: list[dict] = []
    deadline = time.monotonic() + 3
    while not records and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
        records = [json.loads(line) for path in trail_dir.glob("trail-*.jsonl")
                   for line in path.read_text(encoding="utf-8").splitlines()]
    if not stop:
        await runner.cleanup()
    return records


gate_trace8 = threading.Event()
trace8 = node8.begin("след обрыва", {"m": 14}, "любой", held(gate_trace8, {"ok": 1}))["task_id"]
node8.settle(trace8, 0.2)
traced8 = asyncio.run(_dropped_traced(trace8, scratch("trail8_")))
gate_trace8.set()
node8.settle(trace8, 5)
check("обрыв клиента на ожидании — в следе запись отмены (улика, не шаг): инструмент, сколько шёл, кто отменил, без "
      "параметров вызова",
      len(traced8) == 1 and traced8[0].get("level") == "cancel" and traced8[0].get("tool") == "ждать"
      and traced8[0].get("ok") is False and traced8[0].get("args") == {}
      and traced8[0].get("data", {}).get("elapsed_sec", 0) >= 0.2 and traced8[0]["data"].get("by") == "обрыв клиента",
      traced8)
gate_stop8 = threading.Event()
stop8 = node8.begin("след остановки", {"m": 15}, "любой", held(gate_stop8, {"ok": 1}))["task_id"]
node8.settle(stop8, 0.2)
stopped8 = asyncio.run(_dropped_traced(stop8, scratch("trail8s_"), stop=True))
gate_stop8.set()
node8.settle(stop8, 5)
check("остановка сервера посреди ожидания (метка до cleanup) — в следе запись отмены с причиной «остановка сервера», "
      "а не обрыв клиента",
      [(r.get("level"), r.get("data", {}).get("by")) for r in stopped8] == [("cancel", "остановка сервера")], stopped8)

sys.path.insert(0, str(ROOT / "scripts" / "guards"))
import reproduce

try:
    _picked = reproduce.pick(traced8 + stopped8, None, None)
except SystemExit as said:
    _picked = str(said)
try:
    reproduce.as_steps(traced8)
    _stepped = "шаг собран"
except SystemExit as said:
    _stepped = str(said)
check("воспроизводитель не выдаёт отмену за отказ: pick её пропускает (воспроизводить нечего), as_steps отказывает с "
      "причиной, а не expect UNKNOWN_ERROR",
      "воспроизводить нечего" in str(_picked) and "не воспроизводится" in _stepped, (_picked, _stepped))

bare_gate = threading.Event()
bare_node = Tasks(Engine(), root=root8)          # исполнитель на движке без реестра — как приспособление сценария
bare_id = bare_node.begin("голый", {"m": 9}, "любой", held(bare_gate, {"ok": 1}))["task_id"]
bare_task = node8.cancel([bare_id])[0].get("task") or {}
bare_gate.set()
bare_node.settle(bare_id, 5)
check("причину снятия переводит узел, принявший вызов, а не узел исполнителя: исполнитель на движке без реестра — "
      "TASK_CANCELLED с классом и текстом реестра",
      (bare_task.get("error") or {}).get("reaction_class") == "ai_recoverable"
      and bare_id in (bare_task.get("error") or {}).get("message", ""), bare_task)

FOREIGN = ("import sys, threading\nfrom pathlib import Path\nfrom core.engine.engine import Engine\n"
           "from core.jobs import Tasks\nnode, stop = Tasks(Engine(), root=Path(sys.argv[1])), threading.Event()\n"
           "entry = node.begin('чужое', {'x': 1}, 'любой', lambda beat: (stop.wait(), {'ok': 1})[1])\n"
           "print(entry['task_id'], flush=True)\nsys.stdin.read()\nstop.set()\nnode.settle(entry['task_id'], 5)\n")
clean = {k: v for k, v in os.environ.items() if k != "VPM_FIXTURES"}
other = subprocess.Popen([sys.executable, "-c", FOREIGN, str(root8)], cwd=ROOT, env=clean, text=True,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
foreign_id = (other.stdout.readline() if other.stdout else "").strip()
foreign = node8.cancel([foreign_id])[0]
check("снятие задания, чей исполнитель в другом процессе (держит замок записи), — TASK_FOREIGN; задание идёт дальше",
      (foreign.get("error") or {}).get("code") == "TASK_FOREIGN"
      and (node8.status([foreign_id])[0].get("task") or {}).get("status") in ("pending", "processing"), foreign)
stdout8, stderr8 = other.communicate(timeout=10)
check("конец трубы — конец чужой работы: completed, процесс вышел сам",
      other.returncode == 0 and (node8.status([foreign_id])[0].get("task") or {}).get("status") == "completed",
      stderr8[-300:])

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
