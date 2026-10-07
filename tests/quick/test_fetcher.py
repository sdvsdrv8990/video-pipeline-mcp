#!/usr/bin/env python3
"""tests/quick/test_fetcher.py — один загрузчик части (`core/providers/download.py Fetcher`) на подставном транспорте.

Истина — байты на диске и журнал транспорта: какие запросы ушли, куда (IP), под каким именем (Host, SNI), с какими
заголовками. Каждый род — парой с законным: обрыв → докачка, 200 на Range → с нуля, чужой адрес → туда ни запроса.
Отказы — кодом с фактами и через путь доставки (`Reactions.get_error`). Вызов загрузчика не роняет набор: исход и
отказ судит проверка.
"""
import hashlib
import shutil
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
import yaml

from core.contracts import ContractError
from core.declaration import Declaration
from core.declaration_schemas import FetchPolicy, RequestPolicy
from core.paths import REMNANT, Files
from core.providers.download import Fetcher, file_digest
from core.reactions import Reactions

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


BODY = bytes(range(256)) * 40
BODY2 = bytes(reversed(range(256))) * 40
SHA, SHA2 = hashlib.sha256(BODY).hexdigest(), hashlib.sha256(BODY2).hexdigest()
RESOLVE = "https://huggingface.co/org/m/resolve/c10e/model.onnx"
CDN = "us.aws.cdn.hf.co"
NAMES = {"huggingface.co": ["18.1.1.1"], CDN: ["13.2.2.2"], "evil.example": ["66.6.6.6"],
         "inner.example": ["93.1.1.1", "10.0.0.7"], "cdn.provider.example": ["52.3.3.3"]}
# Политика весов из шага 0 (хосты — точные, факт HEAD); коды потребителя — данные его декларации.
WEIGHTS = {"schemes": ["https"], "hosts": ["huggingface.co", CDN], "block_private": True, "max_bytes": 0,
           "timeout_sec": 30, "redirects": 5, "attempts": 1, "backoff_sec": 1, "backoff_max_sec": 60,
           "retry_after_max_sec": 120, "jitter": 0, "speed_floor_bytes_per_sec": 0, "speed_window_sec": 30,
           "retry_on": [429, 500, 502, 503, 504],
           "codes": {"failed": "MODEL_SOURCE_FAILED", "corrupt": "MODEL_CORRUPT"}}
RESULTS = {**WEIGHTS, "hosts": [], "max_bytes": 6000,
           "codes": {"failed": "PROVIDER_FAILED", "corrupt": "MODEL_CORRUPT"}}


class World:
    """Подставной интернет: разрешение имён, журнал запросов, HF resolve → подписанная ссылка CDN → тело.

    ETag CDN — его собственный (xet-хеш), не sha256 состава: так у настоящего моста (факт шага 0).
    """

    def __init__(self):
        self.body, self.etag, self.modified, self.signature = BODY, '"xet-72ce7bc6"', "", "a"
        self.log: list[dict] = []
        self.resolved: list[str] = []
        self.cut: int | None = None              # обрыв соединения после стольких байт тела — на одну попытку
        self.short: int | None = None            # тело короче без обрыва (Content-Length честно меньше)
        self.honor_range, self.shift = True, 0
        self.statuses: list[tuple[int, dict]] = []   # ответы CDN до нормального (перегрузка)
        self.location = ""                        # куда ведёт resolve; пусто — на CDN с текущей подписью
        self.coding: tuple[str, bytes] | None = None  # CDN шлёт сжатое тело (кодировка, байты) вопреки запросу
        self.pace: list[float] = []               # секунд источника перед каждым куском; кончились — повтор последнего
        self.now = 0.0                            # часы мира: их читает шов `clock` загрузчика
        self.linked = ""                          # X-Linked-Size на переходе resolve — длина файла за CDN (как у HF)
        self.lengthless = False                   # CDN не называет Content-Length

    def resolve(self, host, port, *args, **kwargs):
        self.resolved.append(host)
        if host not in NAMES:
            raise socket.gaierror(f"нет имени {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in NAMES[host]]

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", "")
        self.log.append({"ip": request.url.host, "host": host, "sni": request.extensions.get("sni_hostname"),
                         "path": request.url.path, "query": request.url.query.decode(),
                         "range": request.headers.get("range"), "if_range": request.headers.get("if-range"),
                         "auth": request.headers.get("authorization"),
                         "accept_encoding": request.headers.get("accept-encoding"), "method": request.method})
        if host == "huggingface.co" and "/resolve/" in request.url.path:
            moved = {"location": self.location or f"https://{CDN}/xet-bridge-us/72ce?Signature={self.signature}"}
            return httpx.Response(302, headers={**moved, **({"x-linked-size": self.linked} if self.linked else {})})
        if host == CDN and request.url.params.get("Signature") != self.signature:
            return httpx.Response(403)                # подпись истекла
        if self.statuses:
            status, headers = self.statuses.pop(0)
            return httpx.Response(status, headers=headers)
        if self.coding:
            coding, wire = self.coding
            return httpx.Response(200, headers={"content-encoding": coding, "content-length": str(len(wire))},
                                  content=iter([wire]))    # потоком, как из сети: байты httpx распаковал бы сразу
        body, start = self.body[:self.short] if self.short else self.body, 0
        asked = request.headers.get("range", "")
        if asked and self.honor_range and request.headers.get("if-range") in {self.etag, self.modified} - {""}:
            start = int(asked.split("=")[1].rstrip("-"))
        headers = {k: v for k, v in (("etag", self.etag), ("last-modified", self.modified)) if v}
        if start:
            headers["content-range"] = f"bytes {start + self.shift}-{len(body) - 1}/{len(body)}"
        rest = body[start:]
        if not self.lengthless:
            headers["content-length"] = str(len(rest))
        cut, self.cut = self.cut, None
        stream = self._stream(rest, cut)
        return httpx.Response(206 if start else 200, headers=headers,
                              content=self._paced(stream) if self.pace else stream)

    @staticmethod
    def _stream(rest: bytes, cut: int | None):
        for i in range(0, len(rest), 1024):
            if cut is not None and i >= cut:
                raise httpx.ReadError("соединение оборвано")
            yield rest[i:i + 1024]

    def _paced(self, stream):
        for n, chunk in enumerate(stream):
            self.now += self.pace[min(n, len(self.pace) - 1)]
            yield chunk


def fetcher(world: World, policy: dict = WEIGHTS, proxies: dict | None = None, **over) -> tuple[Fetcher, list]:
    """Загрузчик на подставном мире; прокси — только названные тестом, не из среды прогона (TS-09)."""
    pauses: list[float] = []
    return Fetcher(FetchPolicy.model_validate({**policy, **over}), transport=httpx.MockTransport(world.handle),
                   resolve=world.resolve,
                   sleep=pauses.append, proxies=lambda: dict(proxies or {})), pauses


def attempt(call) -> tuple[dict, ContractError | None]:
    """Исход вызова или его отказ: оба — улика проверки; неожиданное исключение — тоже улика, а не обрыв набора."""
    try:
        return call(), None
    except ContractError as e:
        return {}, e
    except Exception as e:  # мутант или поломка: проверка назовёт это, а не трасса
        return {}, ContractError("НЕ_ОТКАЗ", facts={"exception": f"{type(e).__name__}: {e}"})


REACTIONS = Reactions(ROOT / "config" / "server_reactions.yaml")
work = Path(tempfile.mkdtemp(prefix="fetcher_"))
files = Files(work)
PART = "org/m/model.onnx"


def fresh() -> None:
    shutil.rmtree(work / "org", ignore_errors=True)


def remnant_of(owner: str = "inst1") -> Path:
    return files.remnant(PART, owner)


def remnant_size(owner: str = "inst1") -> int | None:
    part = remnant_of(owner)
    return part.stat().st_size if part.exists() else None


def on_disk() -> bytes | None:
    target = work / PART
    return target.read_bytes() if target.exists() else None


def pull(world: World, policy: dict = WEIGHTS, **kwargs) -> tuple[dict, ContractError | None]:
    over = {k: kwargs.pop(k) for k in list(kwargs) if k in policy}
    return attempt(lambda: fetcher(world, policy, **over)[0].fetch(RESOLVE, files, PART, "inst1", **kwargs))


def interrupted(world: World, at: int = 4096, **kwargs) -> None:
    """Первая попытка обрывается на `at` байтах тела — остаток с заметкой остаётся на диске."""
    world.cut = at
    pull(world, **kwargs)


try:
    print("== 1. Законный путь: resolve → CDN, адрес судится и закрепляется, ключ — только исходному хосту ==")
    fresh()
    w = World()
    got, e = pull(w, size=len(BODY), digest=SHA, headers={"Authorization": "Bearer t"})
    check("файл лёг целиком и сошёлся с отпечатком", on_disk() == BODY and got.get("bytes") == len(BODY), e and e.facts)
    left = sorted(p.name for p in (work / PART).parent.iterdir() if REMNANT in p.name) if on_disk() else ["—"]
    check("ни остатка, ни заметки", not left, left)
    check("соединение — на IP, который судили; Host и SNI — исходное имя",
          [(x["ip"], x["host"], x["sni"]) for x in w.log]
          == [("18.1.1.1", "huggingface.co", "huggingface.co"), ("13.2.2.2", CDN, CDN)], w.log)
    check("каждое имя разрешено ровно раз — второго разрешения нет", w.resolved == ["huggingface.co", CDN], w.resolved)
    check("ключ ушёл исходному хосту и не ушёл CDN", [x["auth"] for x in w.log] == ["Bearer t", None], w.log)
    check("исход называет хост, а не подписанный адрес", got.get("host") == CDN and got.get("resumed_from") == 0, got)

    print("== 2. Обрыв → остаток с заметкой → докачка Range + If-Range валидатором CDN (MF-03, MF-04) ==")
    fresh()
    w = World()
    w.cut = 4096
    _, e = pull(w, size=len(BODY), digest=SHA)
    check("обрыв — DOWNLOAD_INCOMPLETE с хостом и байтами",
          e is not None and e.code == "DOWNLOAD_INCOMPLETE"
          and e.facts == {"host": CDN, "got": 4096, "expected": len(BODY)}, e and (e.code, e.facts))
    check("остаток своего владельца — ровно 4096 байт, файла под именем нет",
          remnant_size() == 4096 and on_disk() is None, remnant_size())
    check("заметка остатка — ETag того сервера, что отдавал, и его хост",
          files.noted(PART, "inst1") == {"etag": w.etag, "host": CDN}, files.noted(PART, "inst1"))
    got, e = pull(w, size=len(BODY), digest=SHA)
    cdn = [x for x in w.log if x["host"] == CDN][-1]
    check("докачка: Range от длины остатка и If-Range — ETag CDN, а не sha256 состава",
          cdn["range"] == "bytes=4096-" and cdn["if_range"] == w.etag != SHA, cdn)
    check("206 дописан к остатку — файл совпал с источником",
          on_disk() == BODY and got.get("resumed_from") == 4096 and got.get("restarted") is False,
          f"{got} {e and (e.code, e.facts)}")
    check("resolve не получил Range: валидатор одного сервера другому ничего не говорит",
          [x["range"] for x in w.log if x["host"] == "huggingface.co"] == [None, None], w.log)

    print("== 3. Сервер не принял Range (200) или источник сменил файл → остаток сброшен, начато заново ==")
    for case, change, body, sha in (
            ("200 вместо 206", lambda w: setattr(w, "honor_range", False), BODY, SHA),
            ("источник сменил файл (ETag другой)",
             lambda w: (setattr(w, "body", BODY2), setattr(w, "etag", '"xet-new"')), BODY2, SHA2)):
        fresh()
        w = World()
        interrupted(w, size=len(BODY), digest=sha)
        change(w)
        got, e = pull(w, size=len(BODY), digest=sha)
        check(f"{case}: файл — новые байты целиком, исход «начато заново»",
              on_disk() == body and got.get("restarted") is True and got.get("resumed_from") == 4096,
              f"{got} {e and (e.code, e.facts)}")

    print("== 4. Content-Range не с N → отказ, остаток не тронут ==")
    fresh()
    w = World()
    interrupted(w, size=len(BODY))
    w.shift = -4096
    _, e = pull(w, size=len(BODY))
    check("DOWNLOAD_RANGE_INVALID с хостом, ожидаемым и пришедшим",
          e is not None and e.code == "DOWNLOAD_RANGE_INVALID"
          and e.facts == {"host": CDN, "expected": 4096, "got": f"bytes 0-{len(BODY) - 1}/{len(BODY)}"},
          e and (e.code, e.facts))
    check("остаток цел: 4096 байт", remnant_size() == 4096, remnant_size())

    print("== 5. Длина и отпечаток — до того, как файл встанет под имя (MF-05, MF-06) ==")
    for case, size, digest, facts in (
            ("длиннее состава", len(BODY) - 100, "", {"part": PART, "expected": len(BODY) - 100, "got": len(BODY)}),
            ("отпечаток разошёлся", len(BODY), "0" * 64, {"part": PART, "expected": "0" * 64, "got": SHA})):
        fresh()
        _, e = pull(World(), size=size, digest=digest)
        check(f"{case}: код потребителя с фактами, остаток и заметка убраны, под именем ничего",
              e is not None and e.code == "MODEL_CORRUPT" and e.facts == facts and remnant_size() is None
              and files.noted(PART, "inst1") is None and on_disk() is None, e and (e.code, e.facts))
    fresh()
    w = World()
    w.short = 5000
    _, e = pull(w, size=len(BODY))
    check("короче состава без обрыва — DOWNLOAD_INCOMPLETE, остаток жив (докачается)",
          e is not None and e.code == "DOWNLOAD_INCOMPLETE" and remnant_size() == 5000, e and (e.code, e.facts))

    print("== 6. Адрес судится на каждом переходе: туда, куда нельзя, — ни разрешения имени, ни запроса (MF-15) ==")
    for case, location, policy, host, facts in (
            ("чужой хост", "https://evil.example/x", WEIGHTS, "evil.example",
             {"host": "evil.example", "allowed": f"huggingface.co, {CDN}"}),
            ("адрес без схемы на чужой хост", "//evil.example/x", WEIGHTS, "evil.example",
             {"host": "evil.example", "allowed": f"huggingface.co, {CDN}"}),
            ("https → http", f"http://{CDN}/x", WEIGHTS, CDN, {"host": CDN, "allowed": "https"}),
            ("объявленный хост как имя пользователя", "https://huggingface.co@evil.example/x", WEIGHTS,
             "evil.example", {"host": "evil.example", "allowed": f"huggingface.co, {CDN}"}),
            ("имя ведёт и во внутреннюю сеть", "https://inner.example/x", RESULTS, "inner.example",
             {"host": "inner.example", "address": "10.0.0.7"})):
        fresh()
        w = World()
        w.location = location
        _, e = pull(w, policy)
        reached = [x for x in w.log if x["host"] == host or x["ip"] in NAMES.get(host, [])]
        check(f"{case}: DOWNLOAD_FORBIDDEN, к адресу запроса нет",
              e is not None and e.code == "DOWNLOAD_FORBIDDEN" and e.facts == facts and not reached,
              f"{e and (e.code, e.facts)} запросы туда: {reached}")
    fresh()
    w = World()
    w.location = "/org/m/raw/c10e/model.onnx"
    got, e = pull(w)
    check("законный: относительный переход — тот же хост, судится и проходит",
          got.get("host") == "huggingface.co" and on_disk() == BODY, f"{e and (e.code, e.facts)} {w.log}")

    print("== 7. Попытка — от исходного адреса: подпись CDN истекла между попытками → новая ссылка, докачка ==")
    fresh()
    w = World()
    w.cut = 4096
    f, pauses = fetcher(w, attempts=2)
    real = w.handle

    def expire_after_cut(request):
        if request.headers.get("host") == "huggingface.co" and len(w.log) >= 2:
            w.signature = "b"
        return real(request)

    f.transport = httpx.MockTransport(expire_after_cut)
    got, e = attempt(lambda: f.fetch(RESOLVE, files, PART, "inst1", size=len(BODY), digest=SHA))
    cdn = [x for x in w.log if x["host"] == CDN]
    check("вторая попытка снова спросила resolve и пошла по новой подписи с Range",
          [x["host"] for x in w.log].count("huggingface.co") == 2
          and cdn[-1]["query"] == "Signature=b" and cdn[-1]["range"] == "bytes=4096-", w.log)
    check("файл совпал, пауза между попытками одна", on_disk() == BODY and len(pauses) == 1,
          f"{pauses} {e and (e.code, e.facts)}")

    print("== 8. Перегрузка: Retry-After сервера — пауза, потом успех (M5) ==")
    fresh()
    w = World()
    w.statuses = [(503, {"Retry-After": "7"})]
    f, pauses = fetcher(w, attempts=2)
    got, e = attempt(lambda: f.fetch(RESOLVE, files, PART, "inst1", size=len(BODY)))
    check("пауза — названная сервером, файл лёг", pauses == [7.0] and on_disk() == BODY,
          f"{pauses} {e and (e.code, e.facts)}")
    fresh()
    w = World()
    w.statuses = [(404, {})]
    _, e = pull(w, attempts=3)
    check("404 не повторяется — код потребителя (у весов MODEL_SOURCE_FAILED) с хостом и статусом",
          e is not None and e.code == "MODEL_SOURCE_FAILED" and e.facts == {"host": CDN, "status": 404}
          and len([x for x in w.log if x["host"] == CDN]) == 1, e and (e.code, e.facts))

    print("== 9. Остаток: чужой не тронут, целый — без сети, без валидатора — с нуля ==")
    fresh()
    other = remnant_of("other")
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_bytes(b"x" * 100)
    pull(World(), size=len(BODY))
    check("чужой остаток (другой владелец) цел", other.exists() and other.read_bytes() == b"x" * 100)
    fresh()
    w = World()
    interrupted(w, size=len(BODY))
    if remnant_of().exists():
        remnant_of().write_bytes(BODY)
    w.log.clear()
    got, e = pull(w, size=len(BODY), digest=SHA)
    check("остаток уже целый — ни одного запроса, суд отпечатком, файл встал",
          not w.log and on_disk() == BODY, f"{w.log} {e and (e.code, e.facts)}")
    for case, forged in (("ссылка на месте остатка без заметки", False), ("ссылка и поддельная заметка", True)):
        fresh()
        victim = work / "victim.bin"
        victim.write_bytes("чужое".encode())
        remnant_of().parent.mkdir(parents=True, exist_ok=True)
        remnant_of().symlink_to(victim)
        w = World()
        if forged:
            files.note(PART, "inst1", {"etag": w.etag, "host": CDN})
        got, e = pull(w, size=len(BODY), digest=SHA)
        check(f"{case}: чужой файл цел, ссылка снята (FS-14)",
              victim.read_bytes() == "чужое".encode() and not remnant_of().is_symlink(), victim.read_bytes()[:40])
        check(f"{case}: {'отказ PATH_ESCAPE с путём остатка' if forged else 'загрузка с нуля, файл лёг'}",
              (e is not None and e.code == "PATH_ESCAPE" and e.facts == {"path": f"{PART}.inst1.part"})
              if forged else on_disk() == BODY, f"{got} {e and (e.code, e.facts)}")
    modified = "Wed, 21 Oct 2025 07:28:00 GMT"
    for case, etag, last, note, resumed in (
            ("без ETag и Last-Modified", "", "", None, 0),
            ("слабый ETag (W/)", 'W/"weak"', "", None, 0),
            ("только Last-Modified", "", modified, {"last_modified": modified, "host": CDN}, 4096)):
        fresh()
        w = World()
        w.etag, w.modified = etag, last
        interrupted(w, size=len(BODY))
        check(f"{case}: заметка остатка {note}", files.noted(PART, "inst1") == note, files.noted(PART, "inst1"))
        got, e = pull(w, size=len(BODY))
        sent = [x for x in w.log if x["host"] == CDN][-1]
        check(f"{case}: {'докачка с If-Range' if resumed else 'докачки вслепую нет — Range не послан'}, файл целиком",
              got.get("resumed_from") == resumed and on_disk() == BODY
              and (sent["if_range"] == last if resumed else sent["range"] is None), f"{got} {sent}")

    print("== 10. Результат поставщика: предел байт по ходу потока ==")
    fresh()
    w = World()
    w.location = "https://cdn.provider.example/out.png"
    _, e = pull(w, RESULTS)
    check("сверх предела — DOWNLOAD_TOO_LARGE {limit_mb}, остаток убран",
          e is not None and e.code == "DOWNLOAD_TOO_LARGE" and e.facts == {"limit_mb": 0.006}
          and remnant_size() is None, e and (e.code, e.facts))

    print("== 12. Прокси среды: через него — по имени, без разрешения и закрепления; суд хоста тот же ==")
    fresh()
    w = World()
    proxied = {"https": "http://proxy.local:3128", "no": CDN}
    got, e = attempt(lambda: fetcher(w, proxies=proxied)[0].fetch(RESOLVE, files, PART, "inst1", size=len(BODY)))
    check("через прокси — по имени (имя разрешает прокси); хост из NO_PROXY — напрямую, на судимый IP",
          [(x["ip"], x["host"]) for x in w.log] == [("huggingface.co", "huggingface.co"), ("13.2.2.2", CDN)]
          and w.resolved == [CDN] and on_disk() == BODY, f"{w.log} {w.resolved} {e and (e.code, e.facts)}")
    fresh()
    w = World()
    w.location = "https://evil.example/x"
    _, e = attempt(lambda: fetcher(w, proxies={"https": "http://proxy.local:3128"})[0].fetch(
        RESOLVE, files, PART, "inst1"))
    check("через прокси суд хоста тот же: чужой хост — DOWNLOAD_FORBIDDEN, к нему ни запроса",
          e is not None and e.code == "DOWNLOAD_FORBIDDEN" and not [x for x in w.log if x["host"] == "evil.example"],
          f"{e and (e.code, e.facts)} {w.log}")

    print("== 13. Политика — объект схемы двери: битая — отказ двери при чтении, тело без своей политики — ошибка кода ==")
    decl = work / "fetch.yaml"
    decl.write_text(yaml.safe_dump({"fetch": {k: v for k, v in WEIGHTS.items() if k not in ("codes", "redirects")}}),
                    encoding="utf-8")
    e = attempt(lambda: Declaration(decl, ContractError, "загрузки", "").section("fetch", schema=FetchPolicy))[1]
    check("в разделе нет ключей — отказ двери DECLARATION_INVALID, ключи названы путём от корня файла",
          e is not None and e.code == "DECLARATION_INVALID" and "fetch.codes" in e.facts.get("details", "")
          and "fetch.redirects" in e.facts.get("details", ""), e and (e.code, e.facts))
    check("законный: полная политика строит загрузчик",
          attempt(lambda: Fetcher(FetchPolicy.model_validate(WEIGHTS)))[1] is None)
    fresh()
    w = World()
    body_only = set(FetchPolicy.model_fields) - set(RequestPolicy.model_fields)
    head = RequestPolicy.model_validate({k: v for k, v in WEIGHTS.items() if k not in body_only}
                                        | {"codes": {"failed": "MODEL_SOURCE_FAILED"}})
    f = Fetcher(head, transport=httpx.MockTransport(w.handle), resolve=w.resolve, sleep=lambda _s: None, proxies=dict)
    reply = httpx.Response(200, headers={"content-type": "image/png"}, content=BODY,
                           request=httpx.Request("POST", "https://cdn.provider.example/r"))
    refused = [attempt(lambda: f.fetch(RESOLVE, files, PART, "inst1", size=len(BODY), digest=SHA))[1],
               attempt(lambda: f.receive(reply, files, PART, "inst1", accept=["image/png"]))[1]]
    check("политика без тела: приём тела (fetch, receive) — ошибка кода с именем схемы до первого запроса",
          all(e is not None and e.code == "НЕ_ОТКАЗ" and e.facts["exception"].startswith("TypeError")
              and "FetchPolicy" in e.facts["exception"] for e in refused)
          and not w.log and not w.resolved and on_disk() is None and remnant_size() is None,
          f"{[e and (e.code, e.facts) for e in refused]} {w.log}")
    check("законный: та же политика без тела отвечает длиной по HEAD",
          attempt(lambda: f.length(RESOLVE, linked="X-Linked-Size"))[0] == (len(BODY), f"HEAD {CDN}: Content-Length"))

    print("== 14. Отметки работы: переход, кусок, начало паузы, блок сверки — простой не ловит законную тишину ==")
    fresh()
    w = World()
    w.statuses = [(503, {"Retry-After": "7"})]
    events: list[str] = []
    f = Fetcher(FetchPolicy.model_validate({**WEIGHTS, "attempts": 2}), transport=httpx.MockTransport(w.handle),
                resolve=w.resolve,
                sleep=lambda _s: events.append("sleep"), proxies=dict, mark=lambda: events.append("mark"))
    got, e = attempt(lambda: f.fetch(RESOLVE, files, PART, "inst1", size=len(BODY), digest=SHA))
    sleeps = [i for i, x in enumerate(events) if x == "sleep"]
    check("перед паузой повтора — отметка: тишина паузы отсчитывается от неё",
          sleeps == [3] and events[2] == "mark" and on_disk() == BODY, f"{events[:8]} {e and (e.code, e.facts)}")
    expected = len(w.log) + len(sleeps) + len(BODY) // 1024 + 1
    check("отметка на каждом переходе, принятом куске и блоке сверки — счёт сходится",
          events.count("mark") == expected, f"{events.count('mark')} ≠ {expected} ({len(w.log)} переходов)")
    big = work / "big.bin"
    big.write_bytes(b"z" * (3 * 2**20 + 1))
    blocks: list[int] = []
    check("сверка отпечатка отмечает каждый МиБ прочитанного и считает тот же отпечаток",
          file_digest(big, "sha256", lambda: blocks.append(1)) == hashlib.sha256(big.read_bytes()).hexdigest()
          and len(blocks) == 4, len(blocks))
    check("наибольшая тишина между отметками — max(2 × timeout_sec, backoff_max_sec × (1 + jitter))",
          Fetcher(FetchPolicy.model_validate(WEIGHTS)).silence_sec() == 60.0
          and Fetcher(FetchPolicy.model_validate({**WEIGHTS, "timeout_sec": 90, "backoff_max_sec": 200,
                                                  "jitter": 0.5})).silence_sec() == 300.0,
          (Fetcher(FetchPolicy.model_validate(WEIGHTS)).silence_sec()))

    print("== 11. Путь доставки: у каждого кода загрузчика — текст реестра с фактами ==")
    for code, facts, want in (
            ("DOWNLOAD_FORBIDDEN", {"host": "evil.example", "allowed": CDN}, "evil.example"),
            ("DOWNLOAD_RANGE_INVALID", {"host": CDN, "expected": 4096, "got": "bytes 0-9/10"}, "4096"),
            ("DOWNLOAD_INCOMPLETE", {"host": CDN, "got": 4096, "expected": 10240}, "4096"),
            ("DOWNLOAD_TOO_LARGE", {"limit_mb": 100.0}, "100"),
            ("MODEL_SOURCE_FAILED", {"host": CDN, "status": 404}, "404")):
        detail = REACTIONS.get_error(code, "", facts=facts)
        check(f"{code}: код в реестре, факты в тексте ({want})",
              detail.code == code and want in detail.message and detail.recovery, detail.message)

    print("== 12. Файл — без распаковки (NI-04): identity в запросе, сжатое вопреки ему — отказ до записи ==")
    import gzip

    world = World()
    fresh()
    _, e = pull(world, size=len(BODY))
    check("каждый запрос к источнику ушёл с Accept-Encoding: identity, целое тело встало как есть",
          e is None and on_disk() == BODY and all(r["accept_encoding"] == "identity" for r in world.log),
          str([r["accept_encoding"] for r in world.log]))
    packed = gzip.compress(BODY)
    for label, wire, size in (
            ("целое сжатое", packed, len(BODY)),
            ("сжатое обрезано вдвое, длины в составе нет", packed[:len(packed) // 2], None),
            ("битое сжатое", packed[:10] + bytes(b ^ 0xFF for b in packed[10:40]) + packed[40:], len(BODY))):
        world = World()
        world.coding = ("gzip", wire)
        fresh()
        _, e = pull(world, size=size)
        check(f"{label} вопреки identity — отказ кодом до записи: под именем и в остатке пусто",
              e is not None and e.code == "MODEL_SOURCE_FAILED" and e.facts.get("status") == "Content-Encoding gzip"
              and on_disk() is None and remnant_size() is None, f"{e and e.code} {e and e.facts}")

    print("== 15. Мера работы тела (NI-01): капля ниже предела за окно — отказ с тратой попытки, на ходу — отметки ==")

    def paced(world: World, **over) -> tuple[Fetcher, list[str]]:
        """Загрузчик с пределом 1024 байт/с; часы — мира, их двигает транспорт перед каждым куском."""
        events: list[str] = []
        return Fetcher(FetchPolicy.model_validate({**WEIGHTS, "speed_floor_bytes_per_sec": 1024, **over}),
                       transport=httpx.MockTransport(world.handle),
                       resolve=world.resolve, sleep=lambda _s: events.append("sleep"), proxies=dict,
                       mark=lambda: events.append("mark"), clock=lambda: world.now), events

    fresh()
    w = World()
    w.pace = [5.0]                                  # 1024 байт за 5 с — 204.8 байт/с
    f, events = paced(w, speed_window_sec=10, attempts=2)
    _, e = attempt(lambda: f.fetch(RESOLVE, files, PART, "inst1", size=len(BODY)))
    check("капля ниже предела за окно — DOWNLOAD_TOO_SLOW с принятым за окно, окном и пределом",
          e is not None and e.code == "DOWNLOAD_TOO_SLOW"
          and e.facts == {"host": CDN, "got": 2048, "window_sec": 10, "floor": 1024}, e and (e.code, e.facts))
    check("медленный источник тратит попытку: обе дошли до тела, между ними пауза, остаток растёт для докачки",
          len([x for x in w.log if x["host"] == CDN]) == 2 and events.count("sleep") == 1
          and remnant_size() == 4096 and on_disk() is None, f"{w.log} {events.count('sleep')} {remnant_size()}")
    check("капля отметок работы не даёт: отметки — только переходы и пауза повтора",
          events.count("mark") == len(w.log) + events.count("sleep"),
          f"{events.count('mark')} ≠ {len(w.log)} + {events.count('sleep')}")
    detail = REACTIONS.get_error("DOWNLOAD_TOO_SLOW", "", facts=e.facts if e else {})
    check("DOWNLOAD_TOO_SLOW: код в реестре, хост и числа в тексте, есть рецепт",
          detail.code == "DOWNLOAD_TOO_SLOW" and all(s in detail.message for s in (CDN, "2048", "10", "1024"))
          and detail.recovery, detail.message)

    fresh()
    w = World()
    w.pace = [0.5]                                  # 2048 байт/с, окно 2 с закрывается дважды
    f, events = paced(w, speed_window_sec=2)
    _, e = attempt(lambda: f.fetch(RESOLVE, files, PART, "inst1", size=len(BODY), digest=SHA))
    check("законный: не медленнее предела — окна закрываются выше него, файл лёг целиком",
          e is None and on_disk() == BODY, e and (e.code, e.facts))
    check("на ходу отметка на каждом куске, как при пределе 0 (M4): переходы + куски + блок сверки",
          events.count("mark") == len(w.log) + len(BODY) // 1024 + 1, f"{events.count('mark')} ({len(w.log)} переходов)")

    fresh()
    w = World()
    w.pace = [1.0]                                  # ровно предел: 1024 байт за 1 с, окно 10 с — L·T байт
    f, events = paced(w, speed_window_sec=10)
    _, e = attempt(lambda: f.fetch(RESOLVE, files, PART, "inst1", size=len(BODY), digest=SHA))
    check("ровно на пределе — L·T байт за окно T: приём идёт, файл лёг целиком, отметка на каждом куске",
          e is None and on_disk() == BODY and events.count("mark") == len(w.log) + len(BODY) // 1024 + 1,
          f"{e and (e.code, e.facts)} {events.count('mark')}")

    fresh()
    w = World()
    w.pace = [0.125] * 6 + [5.0]                    # быстрое начало, потом капля
    f, events = paced(w, speed_window_sec=5)
    _, e = attempt(lambda: f.fetch(RESOLVE, files, PART, "inst1", size=len(BODY)))
    check("капля после быстрого начала — отказ в первом медленном окне: окно меряет своё, а не среднее с начала тела",
          e is not None and e.code == "DOWNLOAD_TOO_SLOW" and e.facts.get("got") == 1024 and remnant_size() == 8192,
          f"{e and (e.code, e.facts)} {remnant_size()}")

    fresh()
    w = World()
    w.pace = [5.0]
    f, events = paced(w, speed_floor_bytes_per_sec=0, speed_window_sec=10)
    _, e = attempt(lambda: f.fetch(RESOLVE, files, PART, "inst1", size=len(BODY), digest=SHA))
    check("предел 0 — та же капля принимается целиком, отметка на каждом куске (M4 как был)",
          e is None and on_disk() == BODY and events.count("mark") == len(w.log) + len(BODY) // 1024 + 1,
          f"{e and (e.code, e.facts)} {events.count('mark')}")

    clock = [0.0]

    def drip():
        for i in range(0, len(BODY), 1024):
            clock[0] += 5.0
            yield BODY[i:i + 1024]

    reply = httpx.Response(200, headers={"content-type": "image/png"}, content=drip(),
                           request=httpx.Request("POST", "https://cdn.provider.example/r"))
    f = Fetcher(FetchPolicy.model_validate({**RESULTS, "speed_floor_bytes_per_sec": 1024, "speed_window_sec": 10}),
                clock=lambda: clock[0])
    fresh()
    e = attempt(lambda: f.receive(reply, files, PART, "inst1", accept=["image/png"]))[1]
    check("результат каплей — отказ без повтора и без остатка: под именем и в остатке пусто",
          e is not None and e.code == "DOWNLOAD_TOO_SLOW" and on_disk() is None and remnant_size() is None,
          f"{e and (e.code, e.facts)} {remnant_size()}")

    print("== 16. Длина по HEAD без тела (MS-04): заголовок перехода, иначе Content-Length не перехода, иначе None ==")

    def head(world: World, **over):
        """Длина части тем же загрузчиком: (исход length, отказ, паузы); заголовок длины — как у HF."""
        f, pauses = fetcher(world, **over)
        got, e = attempt(lambda: f.length(RESOLVE, {"Authorization": "Bearer t"}, linked="X-Linked-Size"))
        return got, e, pauses

    w = World()
    w.linked = "123456789"
    got, e, _ = head(w)
    check("HF назвал длину на переходе — она и есть; на CDN запроса нет, только HEAD, ключ — исходному хосту",
          got == (123456789, "HEAD huggingface.co: X-Linked-Size")
          and [(x["host"], x["method"], x["auth"]) for x in w.log] == [("huggingface.co", "HEAD", "Bearer t")]
          and w.resolved == ["huggingface.co"], f"{got} {e and (e.code, e.facts)} {w.log}")
    w = World()
    got, e, _ = head(w)
    check("законный: перехода с длиной нет — Content-Length конечного ответа, переход судится и разрешается",
          got == (len(BODY), f"HEAD {CDN}: Content-Length")
          and [(x["ip"], x["method"], x["auth"]) for x in w.log] == [("18.1.1.1", "HEAD", "Bearer t"),
                                                                       ("13.2.2.2", "HEAD", None)],
          f"{got} {e and (e.code, e.facts)} {w.log}")
    from core.providers.download import Unreached

    w = World()
    w.lengthless = True
    got, e, _ = head(w)
    check("длины нет ни в заголовке перехода, ни у конечного ответа — None с тем, что спрошено, а не 0; хост достигнут",
          e is None and got == (None, f"HEAD {CDN}: нет X-Linked-Size и Content-Length")
          and not isinstance(got[1], Unreached), f"{got} {type(got[1]).__name__} {e and e.facts}")
    w = World()
    w.statuses = [(503, {"Retry-After": "7"})]
    got, e, pauses = head(w, attempts=3)
    check("перегрузка на HEAD — повтор общим циклом двери, как у fetch: пауза — срок сервера, вторая попытка дала длину",
          e is None and got == (len(BODY), f"HEAD {CDN}: Content-Length") and pauses == [7.0] and len(w.log) == 4,
          f"{got} {pauses} {len(w.log)}")
    w = World()
    w.statuses = [(503, {})] * 3
    got, e, pauses = head(w, attempts=3)
    check("перегрузка на всех попытках — None с кодом ответа и меткой Unreached: хост не достигнут, а не отказ",
          e is None and got == (None, f"HEAD {CDN}: 503") and isinstance(got[1], Unreached)
          and pauses == [1.0, 2.0] and len(w.log) == 6, f"{got} {type(got[1]).__name__} {pauses} {len(w.log)}")
    w = World()

    def offline(request):
        raise httpx.ConnectError("нет связи")

    f, _ = fetcher(w)
    f.transport = httpx.MockTransport(offline)
    got, e = attempt(lambda: f.length(RESOLVE, linked="X-Linked-Size"))
    check("сеть не ответила — None с родом обрыва и меткой Unreached, а не исключение",
          e is None and got == (None, "HEAD huggingface.co: ConnectError") and isinstance(got[1], Unreached),
          f"{got} {type(got[1]).__name__} {e and (e.code, e.facts)}")
    for case, location, code in (("чужой хост на переходе", "https://evil.example/x", "DOWNLOAD_FORBIDDEN"),
                                 ("https → http на переходе", f"http://{CDN}/x", "DOWNLOAD_FORBIDDEN")):
        w = World()
        w.location = location
        got, e, _ = head(w)
        check(f"{case}: отказ суда, а не «длина неизвестна»; туда ни запроса",
              e is not None and e.code == code and len(w.log) == 1, f"{got} {e and (e.code, e.facts)} {w.log}")
    w = World()
    w.statuses = [(404, {})]
    got, e, _ = head(w)
    check("источник ответил 404 — отказ источника кодом потребителя, а не «длина неизвестна»",
          e is not None and e.code == "MODEL_SOURCE_FAILED" and e.facts == {"host": CDN, "status": 404},
          f"{got} {e and (e.code, e.facts)}")
finally:
    shutil.rmtree(work, ignore_errors=True)

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
