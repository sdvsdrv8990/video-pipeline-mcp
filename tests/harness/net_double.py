"""tests/harness/net_double.py — двойник сети провайдеров для сценариев (род C): сценарий сети — данные, ответ — форма.

Подменяет только швы двери загрузчика (`transport`, `resolve` у классов, которые ходят в сеть): политика, суд адреса,
повтор и перевод отказа остаются боевыми. Сценарий сети: `names` (имя → IP; литерал IP разрешается сам) и `routes` —
правило `{host, method, path}` с очередью ответов `replies`, последний повторяется. Формы ответа:
- `{status, headers?, json? | body?}` — ответ;
- `{error: ConnectError | ConnectTimeout | ReadTimeout | ReadError | RemoteProtocolError}` — сбой сети;
- `{status, body, cut_after}` — тело оборвано после `cut_after` байт, заявлена полная длина;
- `{status, size}` — тело на `size` байт.
Журнал — строка JSON на запрос. POST, дошедший до двойника, — задача на стороне провайдера (`task`), даже если ответ
оборван: события «у провайдера ровно одна задача» (AD-20, AD-21) читают его. Запрос мимо двери — растяжка.
"""
import importlib
import ipaddress
import json
import socket
import threading
from pathlib import Path

import httpx

ERRORS = ("ConnectError", "ConnectTimeout", "ReadTimeout", "ReadError", "RemoteProtocolError")
UNSENT = ("ConnectError", "ConnectTimeout")        # запрос не ушёл: задачи у провайдера нет
REPLY_KEYS = {"status", "headers", "json", "body", "cut_after", "size", "error"}
ROUTE_KEYS = {"host", "method", "path", "replies"}
UNROUTED = 599                                     # ответа на маршрут нет — код вне любых retry_on сценария
JOURNAL = "net_double.jsonl"                       # журнал двойника в каталоге журналов сервера (MCP_LOGS)
# Классы со швами двери: любая сеть сервера сценария идёт через двойник.
SEAMS = ("core.providers.img.http_image:HttpImageAPI", "core.providers.catalog:OnlineCatalog",
         "core.providers.catalog:ModelCatalog", "core.runner.supervisor:RunnerSupervisor",
         "core.transport.tunnel:CloudflaredTunnel")
# Растяжки httpx: функции верхнего уровня и настоящий транспорт любого клиента, построенного мимо швов.
BYPASS = ("request", "stream", "get", "post", "HTTPTransport.handle_request", "AsyncHTTPTransport.handle_async_request")


def parse_script(data: object) -> dict:
    """Сценарий сети, проверенный по форме; негодное место — ValueError с путём ключа."""
    if not isinstance(data, dict) or set(data) - {"names", "routes"}:
        raise ValueError(f"network: ждали {{names, routes}}, пришло {sorted(data) if isinstance(data, dict) else type(data).__name__}")
    names = data.get("names") or {}
    if not isinstance(names, dict) or not all(isinstance(v, str) for v in names.values()):
        raise ValueError("network.names: имя → IP строкой")
    for name, ip in names.items():
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise ValueError(f"network.names.{name}: «{ip}» не IP") from None
    routes = data.get("routes") or []
    if not isinstance(routes, list):
        raise ValueError("network.routes: список правил")
    for i, route in enumerate(routes):
        where = f"network.routes[{i}]"
        if not isinstance(route, dict) or set(route) - ROUTE_KEYS or "host" not in route or not route.get("replies"):
            raise ValueError(f"{where}: ждали {{host, method?, path?, replies}}")
        for j, reply in enumerate(route["replies"]):
            _reply_form(reply, f"{where}.replies[{j}]")
    return {"names": dict(names), "routes": routes}


def _reply_form(reply: object, where: str) -> None:
    if not isinstance(reply, dict) or not reply or set(reply) - REPLY_KEYS:
        raise ValueError(f"{where}: ключи — {sorted(REPLY_KEYS)}")
    if "error" in reply:
        if set(reply) != {"error"} or reply["error"] not in ERRORS:
            raise ValueError(f"{where}.error: одно из {list(ERRORS)} и без других ключей")
        return
    if not isinstance(reply.get("status"), int) or not 100 <= reply["status"] <= 599:
        raise ValueError(f"{where}.status: код 100…599")
    if "cut_after" in reply and not isinstance(reply.get("body"), str):
        raise ValueError(f"{where}.cut_after: только с body")
    if sum(key in reply for key in ("json", "body", "size")) > 1:
        raise ValueError(f"{where}: одно из json, body, size")


class Double:
    """Сеть сценария: разрешение имён и ответы по маршрутам; журнал — JSONL (если назван)."""

    def __init__(self, script: dict, journal: Path | None = None):
        self.script = parse_script(script)
        self.journal = journal
        if journal:
            journal.parent.mkdir(parents=True, exist_ok=True)     # сервер создаёт каталог журналов позже первого запроса
        self._lock = threading.Lock()
        self._served = [0] * len(self.script["routes"])
        self.records: list[dict] = []

    def resolve(self, host, port, *args, **kwargs):
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            if host not in self.script["names"]:
                raise socket.gaierror(f"двойник сети: имени {host} нет в names") from None
            ip = ipaddress.ip_address(self.script["names"][host])
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (str(ip), port))]

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = (request.headers.get("host") or request.url.host).rsplit(":", 1)[0].strip("[]")
        path, method = request.url.path, request.method
        index = next((i for i, r in enumerate(self.script["routes"])
                      if r["host"] == host and r.get("method", method).upper() == method
                      and path.startswith(r.get("path", ""))), None)
        record = {"method": method, "host": host, "path": path, "route": index,
                  "idem": next((v for k, v in request.headers.items() if "idempotency" in k.lower()), None)}
        if index is None:
            self._write({**record, "outcome": f"маршрута нет — {UNROUTED}", "task": False})
            return httpx.Response(UNROUTED, text="двойник сети: маршрута нет")
        with self._lock:
            replies = self.script["routes"][index]["replies"]
            reply = replies[min(self._served[index], len(replies) - 1)]
            record["reply"] = min(self._served[index], len(replies) - 1)
            self._served[index] += 1
        if "error" in reply:
            self._write({**record, "outcome": reply["error"], "task": method == "POST" and reply["error"] not in UNSENT})
            raise getattr(httpx, reply["error"])(f"двойник сети: {reply['error']}", request=request)
        self._write({**record, "outcome": self._outcome(reply), "task": method == "POST"})
        return self._response(reply)

    @staticmethod
    def _outcome(reply: dict) -> str:
        if "cut_after" in reply:
            return f"ответ {reply['status']}, обрыв после {reply['cut_after']} байт"
        return f"ответ {reply['status']}"

    @staticmethod
    def _response(reply: dict) -> httpx.Response:
        headers = dict(reply.get("headers") or {})
        if "json" in reply:
            return httpx.Response(reply["status"], headers=headers, json=reply["json"])
        if "size" in reply:
            return httpx.Response(reply["status"], headers=headers, content=b" " * int(reply["size"]))
        body = str(reply.get("body") or "").encode()
        if "cut_after" not in reply:
            return httpx.Response(reply["status"], headers=headers, content=body)
        cut = int(reply["cut_after"])

        def chunks():
            yield body[:cut]
            raise httpx.ReadError("двойник сети: обрыв тела")
        return httpx.Response(reply["status"], headers={**headers, "content-length": str(len(body))}, content=chunks())

    def bypass(self, name: str):
        """Растяжка на функцию httpx: запрос мимо двери пишется в журнал и отказывает."""
        def tripwire(*args, **kwargs):
            self._write({"bypass": f"httpx.{name}", "args": [str(a)[:120] for a in args[:2]], "task": False})
            raise RuntimeError(f"двойник сети: запрос мимо двери загрузчика (httpx.{name})")
        return tripwire

    def _write(self, record: dict) -> None:
        with self._lock:
            self.records.append(record)
            if self.journal:
                with self.journal.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def install(script_path: Path, journal: Path) -> Double:
    """Поставить двойник в процесс: швы `transport` и `resolve` классов `SEAMS`, растяжка на функции и настоящий
    транспорт httpx `BYPASS` — запрос мимо швов отказывает в процессе, а не уходит в сеть. Первая запись журнала —
    `installed`: по ней сценарий судит, что двойник стоит, до своего первого шага."""
    double = Double(json.loads(Path(script_path).read_text(encoding="utf-8")), journal)
    installed, tripwires = [], []                      # запись — что цикл поставил, а не что объявлено
    for spec in SEAMS:
        module, _, name = spec.partition(":")
        owner = getattr(importlib.import_module(module), name)
        owner.transport = httpx.MockTransport(double.handle)
        owner.resolve = staticmethod(double.resolve)
        installed.append(spec)
    for name in BYPASS:
        holder, _, attr = name.rpartition(".")
        setattr(getattr(httpx, holder) if holder else httpx, attr, double.bypass(name))
        tripwires.append(name)
    double._write({"installed": installed, "tripwires": tripwires, "task": False})
    return double
