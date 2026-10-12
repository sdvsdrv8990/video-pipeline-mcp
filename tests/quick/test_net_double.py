#!/usr/bin/env python3
"""tests/quick/test_net_double.py — двойник сети сценариев (род C): форма сценария, каждая форма ответа через настоящую
дверь загрузчика на живых политиках и приспособление в чистом процессе.

Истина — код и факты отказа двери (как их видит клиент) и журнал двойника: что дошло до «провайдера» и стало ли задачей.
Каждая форма — парой с законным: ответ → значение, сбой → свой код; растяжка ловит запрос мимо двери; без среды
приспособление не ставится, и старт падает с именем.
"""
import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx

from core.contracts import ContractError
from core.declaration import Declaration
from core.declaration_schemas import ONLINE_ANSWER, ONLINE_CALL, CallPolicy, Slot
from core.providers.download import answers
from tests.harness.net_double import JOURNAL, SEAMS, Double, parse_script

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


def attempt(call):
    """Исход вызова или отказ: оба — улика проверки; неожиданное исключение — тоже улика, а не обрыв набора."""
    try:
        return call(), None
    except ContractError as e:
        return None, e
    except Exception as e:  # поломка двойника или двери — улика проверки
        return None, ContractError("НЕ_ОТКАЗ", facts={"exception": f"{type(e).__name__}: {e}"})


PROVIDERS = Declaration(ROOT / "config" / "providers.yaml", ContractError, "провайдеров", "")
REPLICATE = "https://api.replicate.com/v1/predictions"
RUNNER = "http://127.0.0.1:8770/run"
NAMES = {"api.replicate.com": "151.101.1.1", "api.openai.com": "162.159.140.245"}


def route(host, *replies, method="POST", path=""):
    return {"host": host, "method": method, "path": path, "replies": list(replies)}


def call(*routes, names=NAMES, url=REPLICATE, slot=ONLINE_CALL, declared=REPLICATE):
    """Вызов POST дверью по политике слота на двойнике — (значение, отказ, журнал двойника)."""
    double = Double({"names": names, "routes": list(routes)})
    got, e = attempt(lambda: answers(PROVIDERS, slot, declared, transport=httpx.MockTransport(double.handle),
                                     resolve=double.resolve, sleep=lambda _s: None, proxies=dict).call(
        url, {"Authorization": "Bearer k"}, method="POST", payload={"json": {"image": "x"}}))
    return got, e, double.records


print("== 1. Форма сценария сети: законная проходит, каждая порча — отказ с путём ключа ==")
LEGAL = {"names": NAMES, "routes": [route("api.replicate.com", {"status": 200, "json": {"id": "t"}},
                                          {"status": 200, "body": "{}", "cut_after": 1}, {"error": "ReadTimeout"},
                                          {"status": 200, "size": 10})]}
check("законный сценарий проходит форму", parse_script(LEGAL) == LEGAL)
for case, spoiled, where in (
        ("лишний ключ сценария", {**LEGAL, "proxy": {}}, "network:"),
        ("имя не на IP", {**LEGAL, "names": {"api.replicate.com": "не-ip"}}, "network.names.api.replicate.com"),
        ("неизвестный сбой", {"routes": [route("h", {"error": "Boom"})]}, "network.routes[0].replies[0].error"),
        ("обрыв без тела", {"routes": [route("h", {"status": 200, "cut_after": 3})]}, "network.routes[0].replies[0].cut_after"),
        ("два вида тела", {"routes": [route("h", {"status": 200, "json": {}, "body": "x"})]}, "network.routes[0].replies[0]:"),
        ("код вне 100…599", {"routes": [route("h", {"status": 700})]}, "network.routes[0].replies[0].status"),
        ("маршрут без ответов", {"routes": [{"host": "h", "replies": []}]}, "network.routes[0]:")):
    try:
        parse_script(spoiled)
        said = "принято"
    except ValueError as e:
        said = str(e)
    check(f"{case} — отказ с путём «{where}»", said.startswith(where), said)

print("== 2. Формы ответа через настоящую дверь: код как у клиента, журнал — что дошло до провайдера ==")
got, e, log = call(route("api.replicate.com", {"status": 201, "json": {"id": "pred1"}}))
check("законный: ответ — JSON вызова; одна запись, POST — задача у провайдера",
      e is None and got == {"id": "pred1"} and [(r["method"], r["task"]) for r in log] == [("POST", True)], (got, e, log))
got, e, log = call(route("api.replicate.com", {"status": 500, "json": {"detail": "сбой"}}))
check("500 — PROVIDER_FAILED с хостом и кодом, задача у провайдера есть",
      e is not None and e.code == "PROVIDER_FAILED" and e.facts == {"host": "api.replicate.com", "status": 500}
      and log[-1]["task"], (e and (e.code, e.facts), log))
got, e, log = call(route("api.replicate.com", {"error": "ReadTimeout"}))
check("ответ не пришёл — PROVIDER_TIMEOUT; запрос ушёл — задача у провайдера могла создаться",
      e is not None and e.code == "PROVIDER_TIMEOUT" and len(log) == 1 and log[0]["task"], (e and e.code, log))
got, e, log = call(route("api.replicate.com", {"error": "ConnectError"}))
check("соединение не открылось — код unreached раздела, задачи у провайдера нет",
      e is not None and e.code == "PROVIDER_FAILED" and e.facts.get("status") == "ConnectError"
      and not any(r["task"] for r in log), (e and (e.code, e.facts), log))
got, e, log = call(route("api.replicate.com", {"status": 200, "body": "{\"id\": \"pred1\"}", "cut_after": 4}))
check("тело оборвано после отправки POST — код outcome_unknown вызова с принятым и заявленным, задача у провайдера есть",
      e is not None and e.code == "PROVIDER_OUTCOME_UNKNOWN"
      and e.facts == {"host": "api.replicate.com", "got": 4, "expected": 15} and log[-1]["task"],
      (e and (e.code, e.facts), log))
limit = PROVIDERS.section(*ONLINE_CALL.section, schema=CallPolicy).max_bytes
got, e, log = call(route("api.replicate.com", {"status": 200, "size": limit + 1}))
check("тело больше max_bytes раздела вызова — DOWNLOAD_TOO_LARGE с ключом раздела",
      e is not None and e.code == "DOWNLOAD_TOO_LARGE" and e.facts.get("key") == "config/providers.yaml → online.call.max_bytes",
      (e and (e.code, e.facts)))
got, e, log = call(route("api.replicate.com", {"status": 201, "json": {}}), names={"api.replicate.com": "10.0.0.5"})
check("имя провайдера ведёт во внутреннюю сеть — DOWNLOAD_FORBIDDEN, до двойника ни запроса",
      e is not None and e.code == "DOWNLOAD_FORBIDDEN" and e.facts == {"host": "api.replicate.com", "address": "10.0.0.5"}
      and not log, (e and (e.code, e.facts), log))
got, e, log = call(route("api.replicate.com", {"status": 201, "json": {}}), names={})
check("имени нет в names — отказ «не ушёл» (gaierror), до двойника ни запроса",
      e is not None and e.code == "PROVIDER_FAILED" and e.facts.get("status") == "gaierror" and not log,
      (e and (e.code, e.facts), log))
got, e, log = call(route("api.openai.com", {"status": 200, "json": {}}))
check("запрос без маршрута — свой код двойника в фактах и запись «маршрута нет», а не молчаливый успех",
      e is not None and e.facts.get("status") == 599 and log and log[0]["route"] is None, (e and (e.code, e.facts), log))
got, e, log = call(route("api.replicate.com", {"status": 201, "json": {"id": "t"}}, method="GET"))
check("маршрут того же хоста на другой метод — POST его не получает: «маршрута нет»",
      e is not None and e.facts.get("status") == 599 and log and log[0]["route"] is None, (e and (e.code, e.facts), log))
got, e, log = call(route("api.replicate.com", {"status": 201, "json": {"id": "t"}}, path="/v1/models"))
check("маршрут того же хоста и метода на другой путь — запрос не к нему: «маршрута нет»",
      e is not None and e.facts.get("status") == 599 and log and log[0]["route"] is None, (e and (e.code, e.facts), log))

runner_slot = Slot(ONLINE_CALL.file, ("online", "http", "Local_runner", "call"), CallPolicy)
got, e, log = call(route("127.0.0.1", {"error": "ConnectError"}), url=RUNNER, slot=runner_slot, declared=RUNNER)
check("раннер на петле: порт молчит — RUNNER_NOT_RUNNING из блока call раннера",
      e is not None and e.code == "RUNNER_NOT_RUNNING", (e and (e.code, e.facts), log))
got, e, log = call(route("127.0.0.1", {"status": 200, "json": {"files": []}}), url=RUNNER, slot=runner_slot, declared=RUNNER)
check("законный: раннер ответил — значение; литерал петли разрешён двойником без names",
      e is None and got == {"files": []} and log[0]["host"] == "127.0.0.1", (got, e, log))

double = Double({"names": NAMES, "routes": [route("api.openai.com", {"status": 503, "headers": {"Retry-After": "0"}},
                                                  {"status": 200, "json": {"data": []}}, method="GET")]})
got, e = attempt(lambda: answers(PROVIDERS, ONLINE_ANSWER, "https://api.openai.com/v1/models",
                                 transport=httpx.MockTransport(double.handle), resolve=double.resolve,
                                 sleep=lambda _s: None, proxies=dict).answer("https://api.openai.com/v1/models"))
check("очередь ответов: 503, затем 200 — повтор двери получил второй ответ; журнал называет номер ответа",
      e is None and got == {"data": []} and [r["reply"] for r in double.records] == [0, 1], (got, e, double.records))

print("== 3. Приспособление в чистом процессе: швы стоят, обход двери — растяжка, без среды — отказ старта ==")
work = Path(tempfile.mkdtemp(prefix="net_double_"))
(work / "net.json").write_text(json.dumps(LEGAL, ensure_ascii=False), encoding="utf-8")
PROBE = ("import importlib, sys\nimport httpx\nfor spec in sys.argv[1:]:\n    module, _, name = spec.partition(':')\n"
         "    owner = getattr(importlib.import_module(module), name)\n"
         "    print(spec, type(owner.transport).__name__, type(getattr(owner.resolve, '__self__', None)).__name__)\n"
         "try:\n    httpx.get('https://api.replicate.com/v1/models')\nexcept RuntimeError as e:\n    print('РАСТЯЖКА', e)\n"
         "try:\n    httpx.Client().get('https://api.replicate.com/v1/models')\nexcept RuntimeError as e:\n"
         "    print('РАСТЯЖКА ТРАНСПОРТА', e)\n")


def child(**env):
    base = {k: v for k, v in os.environ.items() if k not in ("NET_DOUBLE", "MCP_LOGS", "VPM_FIXTURES")}
    base.update(PYTHONPATH=str(ROOT / "tests" / "harness" / "fixtures"), VPM_FIXTURES="net_double", **env)
    return subprocess.run([sys.executable, "-c", PROBE, *sorted(code_seams)], cwd=ROOT, env=base,
                          capture_output=True, text=True)


def seams_in_code() -> set[str]:
    """Классы ядра со швом двери — атрибут `transport` в теле класса: оракул полноты `SEAMS` по исходнику."""
    found = set()
    for path in sorted((ROOT / "core").rglob("*.py")):
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and any(
                    isinstance(target, ast.Name) and target.id == "transport"
                    for item in node.body if isinstance(item, (ast.Assign, ast.AnnAssign))
                    for target in (item.targets if isinstance(item, ast.Assign) else [item.target])):
                found.add(f"{module}:{node.name}")
    return found


code_seams = seams_in_code()
check(f"швы двойника — все классы ядра со швом двери ({len(code_seams)} по исходнику): сеть мимо двойника не останется",
      code_seams and set(SEAMS) == code_seams, (sorted(code_seams - set(SEAMS)), sorted(set(SEAMS) - code_seams)))
done = child(NET_DOUBLE=str(work / "net.json"), MCP_LOGS=str(work))
journal = [json.loads(line) for line in (work / JOURNAL).read_text(encoding="utf-8").splitlines()] \
    if (work / JOURNAL).exists() else []
check("приспособление поставлено: у каждого класса со швом — транспорт и разрешение имён двойника; httpx.get и клиент "
      "на настоящем транспорте мимо двери — растяжка с записью в журнале, наружу ни байта",
      done.returncode == 0 and all(f"{spec} MockTransport Double" in done.stdout for spec in code_seams)
      and "РАСТЯЖКА ТРАНСПОРТА" in done.stdout
      and {r.get("bypass") for r in journal} >= {"httpx.get", "httpx.HTTPTransport.handle_request"},
      (done.returncode, done.stdout[-600:], done.stderr[-300:], journal))
check("первая запись журнала — объявление установки: все швы и растяжки стоят до первого запроса (по ней сценарий "
      "судит, что двойник поставлен, до засева ключа)",
      journal and set(journal[0].get("installed", ())) == code_seams and "HTTPTransport.handle_request" in journal[0].get(
          "tripwires", ()) and journal[0].get("task") is False, journal[:1])
done = child(MCP_LOGS=str(work))
check("нет NET_DOUBLE — отказ старта с именем приспособления, процесс не идёт дальше",
      done.returncode != 0 and "ПРИСПОСОБЛЕНИЕ НЕ ПОСТАВЛЕНО: net_double" in done.stderr and "MockTransport" not in done.stdout,
      (done.returncode, done.stderr[:300]))
(work / "bad.json").write_text(json.dumps({"routes": [{"host": "h", "replies": [{"error": "Boom"}]}]}), encoding="utf-8")
done = child(NET_DOUBLE=str(work / "bad.json"), MCP_LOGS=str(work))
check("битый сценарий сети — отказ старта с путём ключа, а не сеть без двойника",
      done.returncode != 0 and "network.routes[0].replies[0].error" in done.stderr, (done.returncode, done.stderr[:300]))
logs = work / "server" / "logs"
done = child(NET_DOUBLE=str(work / "net.json"), MCP_LOGS=str(logs))
journal = [json.loads(line) for line in (logs / JOURNAL).read_text(encoding="utf-8").splitlines()] \
    if (logs / JOURNAL).exists() else []
check("каталога журналов сервера ещё нет к первому запросу — двойник пишет журнал, а не падает на записи",
      done.returncode == 0 and "РАСТЯЖКА" in done.stdout and any(r.get("bypass") == "httpx.get" for r in journal),
      (done.returncode, done.stdout[-300:], done.stderr[-300:], journal))

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
