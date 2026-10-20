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
import traceback
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
        # Заголовки — байтами, как с провода: не-ASCII валидатор сервера httpx декодирует, а не отвергает.
        return httpx.Response(206 if start else 200, headers=[(k, v.encode()) for k, v in headers.items()],
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
            ("только Last-Modified", "", modified, {"last_modified": modified, "host": CDN}, 4096),
            ("ETag не ASCII — библиотеки запроса его обратно не понесут", '"xet-é"', "", None, 0),
            ("ETag не ASCII и Last-Modified", '"xet-é"', modified, {"last_modified": modified, "host": CDN}, 4096),
            ("Last-Modified не ASCII", "", f"{modified} é", None, 0)):
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
    fresh()
    w = World()
    interrupted(w, size=len(BODY))
    files.note(PART, "inst1", {"etag": '"xet-é"', "host": CDN})
    got, e = pull(w, size=len(BODY), digest=SHA)
    sent = [x for x in w.log if x["host"] == CDN][-1]
    check("заметка с валидатором, которого библиотеки не понесут (записана прежде суда), — остаток сброшен, файл с нуля, "
          "а не отказ при каждой докачке",
          e is None and on_disk() == BODY and got.get("resumed_from") == 0 and sent["range"] is None,
          (e and (e.code, e.facts), got, sent))

    print("== 10. Результат поставщика: предел байт по ходу потока ==")
    fresh()
    w = World()
    w.location = "https://cdn.provider.example/out.png"
    _, e = pull(w, RESULTS)
    check("сверх предела — DOWNLOAD_TOO_LARGE {limit_mb}, остаток убран (загрузчик без слота — ключ «max_bytes»)",
          e is not None and e.code == "DOWNLOAD_TOO_LARGE" and e.facts == {"limit_mb": 0.006, "key": "max_bytes"}
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
    e = attempt(lambda: Declaration(decl, ContractError, "загрузки").section("fetch", schema=FetchPolicy))[1]
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
    # Смена поведения (дверь, срок сервера): пауза повтора бывает и сроком сервера до retry_after_max_sec — тишина его
    # покрывает; прежде формула знала только свою паузу, и простой установки судился бы по ней.
    silences = [Fetcher(FetchPolicy.model_validate({**WEIGHTS, **over})).silence_sec() for over in (
        {}, {"timeout_sec": 90, "backoff_max_sec": 200, "jitter": 0.5},
        {"timeout_sec": 90, "backoff_max_sec": 10, "retry_after_max_sec": 45})]
    check("наибольшая тишина между отметками — max(2 × timeout_sec, backoff_max_sec × (1 + jitter), "
          "retry_after_max_sec): срок сервера, своя пауза, соединение — каждый в своём случае",
          silences == [120.0, 300.0, 180.0], silences)

    print("== 11. Путь доставки: у каждого кода загрузчика — текст реестра с фактами ==")
    for code, facts, want in (
            ("DOWNLOAD_FORBIDDEN", {"host": "evil.example", "allowed": CDN}, "evil.example"),
            ("DOWNLOAD_RANGE_INVALID", {"host": CDN, "expected": 4096, "got": "bytes 0-9/10"}, "4096"),
            ("DOWNLOAD_INCOMPLETE", {"host": CDN, "got": 4096, "expected": 10240}, "4096"),
            ("DOWNLOAD_TOO_LARGE", {"limit_mb": 100.0, "key": "config/media_tasks.yaml → download.fetch.max_bytes"},
             "100"),
            ("MODEL_SOURCE_FAILED", {"host": CDN, "status": 404}, "404")):
        detail = REACTIONS.get_error(code, "", facts=facts)
        check(f"{code}: код в реестре, факты в тексте ({want})",
              detail.code == code and want in detail.message and detail.recovery, detail.message)
    detail = REACTIONS.get_error("DOWNLOAD_TOO_LARGE", "", facts={"limit_mb": 1.0,
                                                                  "key": "config/providers.yaml → online.answer.max_bytes"})
    check("рецепт DOWNLOAD_TOO_LARGE называет ключ предела из фактов отказа — один источник, не перечень в тексте",
          "config/providers.yaml → online.answer.max_bytes" in detail.recovery.reason
          and "download.answer" not in detail.recovery.reason, detail.recovery.reason)

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

    print("== 17. Ответ API в память (NI-09, NI-06, NI-07): та же дверь, предел до разбора, отказ — код или перевод ==")
    import gzip
    import json

    from core.declaration_schemas import SLOTS, AddressedPolicy, AnswerPolicy, ProviderUrl, Slot
    from core.providers.download import answers

    NAMES["api.provider.example"] = ["52.4.4.4"]
    API, DECLARED = "https://api.provider.example/v1/models", "https://api.provider.example/v1/{model}"
    # Форма политики — как online.answer и download.answer: без хоста (его ставит читатель), предел > 0, без переходов,
    # срок обращения; часы событий §17 — настоящие, срок с запасом: его меряет §18 подставными часами.
    ANSWER = {"schemes": ["https"], "block_private": True, "max_bytes": 4096, "timeout_sec": 30, "deadline_sec": 600,
              "redirects": 0,
              "attempts": 3, "backoff_sec": 0.5, "backoff_max_sec": 8, "retry_after_max_sec": 120, "jitter": 0,
              "speed_floor_bytes_per_sec": 0, "speed_window_sec": 30, "retry_on": [408, 409, 429, 500, 502, 503, 504],
              "codes": {"failed": "PROVIDER_FAILED", "corrupt": "DOWNLOAD_INCOMPLETE"}}
    SLOT = Slot("answer.yaml", ("answer",), AnswerPolicy)
    LIST = {"data": [{"id": "модель-1"}, {"id": "модель-2"}]}

    class Api:
        """Подставной API провайдера: ответы по очереди (код, тело, заголовки[, обрыв после байт]) — последний
        повторяется; тело идёт кусками по 1024, `pulled` — сколько кусков дверь вытянула; журнал — куда и с чем."""

        def __init__(self, *replies):
            self.replies, self.log, self.pulled = list(replies), [], 0

        def handle(self, request: httpx.Request) -> httpx.Response:
            self.log.append({"ip": request.url.host, "host": request.headers.get("host"),
                             "sni": request.extensions.get("sni_hostname"), "path": request.url.path,
                             "auth": request.headers.get("authorization"),
                             "accept_encoding": request.headers.get("accept-encoding")})
            status, body, headers, *cut = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
            return httpx.Response(status, headers=headers, content=self._chunks(body, cut[0] if cut else None))

        def _chunks(self, body: bytes, cut: int | None):
            for i in range(0, len(body), 1024):
                if cut is not None and i >= cut:
                    raise httpx.ReadError("соединение оборвано")
                self.pulled += 1
                yield body[i:i + 1024]

    def ask(api: Api, refused=None, url: str = API, declared: str = DECLARED, **over):
        """Ответ двери на подставном API — (значение, отказ, паузы, разрешённые имена). Загрузчик собран читателем
        `answers` из декларации, хост — из объявленного адреса `declared`; ключ — в заголовке потребителя."""
        decl = work / "answer.yaml"
        decl.write_text(yaml.safe_dump({"answer": {**ANSWER, **over}}), encoding="utf-8")
        w, pauses = World(), []
        got, e = attempt(lambda: answers(Declaration(decl, ContractError, "ответа"), SLOT, declared,
                                         transport=httpx.MockTransport(api.handle), resolve=w.resolve,
                                         sleep=pauses.append, proxies=dict).answer(
            url, {"Authorization": "Bearer t"}, refused=refused))
        return got, e, pauses, w.resolved

    api = Api((200, json.dumps(LIST, ensure_ascii=False).encode(), {"content-type": "application/json"}))
    got, e, _, resolved = ask(api)
    check("законный: JSON целиком; соединение на судимый IP, имя в Host и SNI, ключ исходному хосту, без распаковки",
          e is None and got == LIST and resolved == ["api.provider.example"]
          and [(x["ip"], x["host"], x["sni"], x["auth"], x["accept_encoding"]) for x in api.log]
          == [("52.4.4.4", "api.provider.example", "api.provider.example", "Bearer t", "identity")],
          f"{got} {e and (e.code, e.facts)} {api.log}")
    got, e, _, _ = ask(Api((200, json.dumps(LIST).encode("utf-16"), {})))
    check("законный: JSON разобран из байтов — UTF-16 с меткой, как разрешает RFC 8259, без угадывания кодировки",
          e is None and got == LIST, f"{got} {e and (e.code, e.facts)}")

    api = Api((200, b"[" + b"1," * 30000 + b"1]", {}))     # 60 КБ — годный JSON, если дочитать
    got, e, _, _ = ask(api)
    check("тело сверх max_bytes — DOWNLOAD_TOO_LARGE {limit_mb} до разбора: годный, но длинный JSON не принят, "
          "кусков вытянуто не больше предела",
          e is not None and e.code == "DOWNLOAD_TOO_LARGE" and api.pulled <= 5
          and e.facts == {"limit_mb": 0.004, "key": "config/answer.yaml → answer.max_bytes"},
          f"{e and (e.code, e.facts)} кусков {api.pulled}")
    api = Api((200, gzip.compress(json.dumps(LIST).encode()), {"content-encoding": "gzip"}))
    got, e, _, _ = ask(api)
    check("сжатое вопреки identity — отказ кодом политики до приёма: ни куска не вытянуто",
          e is not None and e.code == "PROVIDER_FAILED"
          and e.facts == {"host": "api.provider.example", "status": "Content-Encoding gzip"} and api.pulled == 0,
          f"{e and (e.code, e.facts)} кусков {api.pulled}")
    got, e, _, _ = ask(Api((200, b"<html>200 OK</html>", {"content-type": "text/html"})))
    check("200 не JSON — отказ кодом политики с причиной, а не трасса разбора",
          e is not None and e.code == "PROVIDER_FAILED"
          and e.facts == {"host": "api.provider.example", "status": "ответ не JSON"}, e and (e.code, e.facts))

    api = Api((404, b'{"detail": "no"}', {}))
    got, e, _, _ = ask(api)
    check("404 без переводчика — код политики с хостом и статусом, без повтора",
          e is not None and e.code == "PROVIDER_FAILED" and e.facts == {"host": "api.provider.example", "status": 404}
          and len(api.log) == 1, f"{e and (e.code, e.facts)} {len(api.log)}")
    told: list = []

    def translated(response):
        told.append((response.status_code, response.json()))
        return ContractError("AUTH_FAILED", facts={"status": response.status_code})

    api = Api((401, b'{"detail": "bad key"}', {}))
    got, e, _, _ = ask(api, refused=translated)
    check("401 с переводчиком потребителя — его отказ как есть; переводчик видит код и тело, прочитанное дверью",
          e is not None and e.code == "AUTH_FAILED" and told == [(401, {"detail": "bad key"})] and len(api.log) == 1,
          f"{e and (e.code, e.facts)} {told}")
    told.clear()
    got, e, _, _ = ask(Api((401, b"x" * 9000, {})), refused=translated)
    check("тело отказа сверх предела — DOWNLOAD_TOO_LARGE двери, переводчик не зван: отказ двери не переписывается",
          e is not None and e.code == "DOWNLOAD_TOO_LARGE" and not told, f"{e and (e.code, e.facts)} {told}")

    api = Api((503, b"", {"Retry-After": "7"}), (200, json.dumps(LIST).encode(), {}))
    got, e, pauses, _ = ask(api)
    check("перегрузка — повтор общим циклом двери: пауза — срок сервера, вторая попытка дала ответ",
          e is None and got == LIST and pauses == [7.0] and len(api.log) == 2, f"{got} {pauses} {e and e.facts}")
    told.clear()
    api = Api((503, b'{"detail": "busy"}', {}))
    got, e, pauses, _ = ask(api, refused=translated)
    check("перегрузка на всех попытках — перевод потребителя последнего ответа; паузы растут вдвое от backoff_sec",
          e is not None and e.code == "AUTH_FAILED" and e.facts == {"status": 503} and pauses == [0.5, 1.0]
          and len(api.log) == 3, f"{e and (e.code, e.facts)} {pauses} {len(api.log)}")
    api = Api((200, json.dumps(LIST).encode() * 60, {}, 1024), (200, json.dumps(LIST).encode(), {}))
    got, e, _, _ = ask(api)
    check("обрыв посреди тела — повтор: остатка у ответа нет, вторая попытка с начала",
          e is None and got == LIST and len(api.log) == 2, f"{got} {e and (e.code, e.facts)}")
    got, e, _, _ = ask(Api((200, json.dumps(LIST).encode() * 60, {}, 1024)), attempts=1)
    check("обрыв на последней попытке — код политики с принятым, не DOWNLOAD_INCOMPLETE «на диске»",
          e is not None and e.code == "PROVIDER_FAILED"
          and e.facts == {"host": "api.provider.example", "status": "ответ оборван на 1024 байт"}, e and (e.code, e.facts))

    api = Api((302, b"", {"location": "https://api.provider.example/v2/models"}), (200, json.dumps(LIST).encode(), {}))
    got, e, _, _ = ask(api)
    check("переход при redirects 0 (как в декларациях) — отказ, по переходу ни запроса, даже на тот же хост",
          e is not None and e.code == "PROVIDER_FAILED"
          and e.facts == {"host": "api.provider.example", "status": "переходов больше 0"} and len(api.log) == 1,
          f"{e and (e.code, e.facts)} {api.log}")
    api = Api((302, b"", {"location": "https://evil.example/x"}), (200, json.dumps(LIST).encode(), {}))
    got, e, _, resolved = ask(api, redirects=1)
    check("переход на необъявленный хост (политика события — redirects 1) — DOWNLOAD_FORBIDDEN: хост — только "
          "из объявленного адреса; к чужому ни разрешения имени, ни запроса",
          e is not None and e.code == "DOWNLOAD_FORBIDDEN"
          and e.facts == {"host": "evil.example", "allowed": "api.provider.example"}
          and len(api.log) == 1 and "evil.example" not in resolved, f"{e and (e.code, e.facts)} {api.log} {resolved}")
    api = Api((302, b"", {"location": "/v2/models"}), (200, json.dumps(LIST).encode(), {}))
    got, e, _, _ = ask(api, redirects=1)
    check("законный: переход на тот же объявленный хост при redirects 1 — ответ; ключ — исходному хосту оба раза",
          e is None and got == LIST and [(x["path"], x["auth"]) for x in api.log]
          == [("/v1/models", "Bearer t"), ("/v2/models", "Bearer t")], f"{e and (e.code, e.facts)} {api.log}")
    api = Api((200, json.dumps(LIST).encode(), {}))
    got, e, _, _ = ask(api, url="https://inner.example/v1", declared="https://inner.example/{model}")
    check("имя провайдера ведёт и во внутреннюю сеть — DOWNLOAD_FORBIDDEN с адресом, запроса нет",
          e is not None and e.code == "DOWNLOAD_FORBIDDEN"
          and e.facts == {"host": "inner.example", "address": "10.0.0.7"} and not api.log, f"{e and (e.code, e.facts)}")

    api = Api((200, json.dumps(LIST).encode(), {}))
    contract = [attempt(lambda: Fetcher(FetchPolicy.model_validate(policy), transport=httpx.MockTransport(api.handle),
                                        resolve=World().resolve, proxies=dict).answer(API))[1]
                for policy in ({**RESULTS, "hosts": []}, {**RESULTS, "hosts": ["api.provider.example"], "max_bytes": 0})]
    check("политика без хоста или без предела — ошибка кода до запроса: ответ в память только с объявленного хоста",
          all(e is not None and e.code == "НЕ_ОТКАЗ" and e.facts["exception"].startswith("TypeError") for e in contract)
          and not api.log, f"{[e and e.facts for e in contract]} {api.log}")

    def decl_refusal(body: dict, *path, kind=None):
        """Отказ двери деклараций на разделе `body` (схемой AnswerPolicy или типом `kind`) — детали или «принято»."""
        decl = work / "answer.yaml"
        decl.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
        door = Declaration(decl, ContractError, "ответа")
        e = attempt(lambda: door.need(*path, kind=kind) if kind else door.section(*path, schema=AnswerPolicy))[1]
        return e.facts.get("details", "") if e is not None and e.code == "DECLARATION_INVALID" else "принято"

    check("предел ответа 0 — отказ схемы с путём ключа (NI-06: у приёмника в память предел обязателен)",
          decl_refusal({"answer": {**ANSWER, "max_bytes": 0}}, "answer") == "answer.max_bytes (greater_than)")
    check("срока обращения нет — отказ схемы с путём ключа: срок не берётся умолчанием в коде",
          decl_refusal({"answer": {k: v for k, v in ANSWER.items() if k != "deadline_sec"}}, "answer")
          == "answer.deadline_sec (missing)")
    check("срок ровно на одну полную попытку (deadline_sec = 2 × timeout_sec) — принят",
          decl_refusal({"answer": {**ANSWER, "timeout_sec": 30, "deadline_sec": 60}}, "answer") == "принято")
    check("срок на волос короче одной попытки — отказ схемы с путём ключа: фаза timeout_sec не исполнилась бы ни разу",
          decl_refusal({"answer": {**ANSWER, "timeout_sec": 30, "deadline_sec": 59.999}}, "answer")
          == "answer.deadline_sec (deadline_under_attempt)")
    check("явный hosts в разделе ответа — отказ схемы: хост один, из объявленного адреса, списка рядом нет",
          decl_refusal({"answer": {**ANSWER, "hosts": ["api.provider.example"]}}, "answer")
          == "answer.hosts (hosts_derived)")
    for case, url, verdict in (
            ("подстановка в хосте", "https://{region}.api.example/v1", "x.url (host_templated)"),
            ("подстановка в середине хоста", "https://api.{region}.example/v1", "x.url (host_templated)"),
            ("подстановка сразу за хостом", "https://api.example{path}", "x.url (host_templated)"),
            ("адрес без хоста", "https:///v1/{model}", "x.url (host_templated)"),
            ("законный: подстановка в пути", "https://api.example/v1/{model}", "принято"),
            ("законный: подстановка в запросе", "https://api.example/x.json?id={model}", "принято")):
        check(f"адрес провайдера: {case} — {verdict}",
              decl_refusal({"x": {"url": url}}, "x", "url", kind=ProviderUrl) == verdict, url)
    (work / "answer.yaml").write_text(yaml.safe_dump({"answer": ANSWER}), encoding="utf-8")
    hosts = answers(Declaration(work / "answer.yaml", ContractError, ""), SLOT, DECLARED).policy.hosts
    check("читатель ставит хост из объявленного адреса до подстановки — ровно один",
          hosts == ("api.provider.example",), hosts)

    # Обновлено в B3: разделов обращения к объявленному адресу стало четыре (ответы и прозвонки раннера и туннеля);
    # в B4 — пять (вызов провайдера online.call) и правило схемы deadline_sec ≥ 2 × timeout_sec у всех; 20.10 — шесть
    # (сервер метрик соединителя туннеля `метрики`: готовность — событием поставщика, а не строкой журнала).
    live = {slot.section: slot for slot in SLOTS if issubclass(slot.schema, AddressedPolicy)}
    taken = {path: attempt(lambda: Declaration(ROOT / "config" / slot.file, ContractError, slot.file).section(
        *slot.section, schema=slot.schema))[1] for path, slot in live.items()}
    check("живые декларации: каждый раздел обращения к объявленному адресу проходит свою схему — ответы (online.answer, "
          "download.answer), вызов провайдера (online.call), прозвонка раннера (local.runner.health), прозвонка туннеля, "
          "сервер метрик соединителя",
          set(live) == {("online", "answer"), ("online", "call"), ("download", "answer"), ("local", "runner", "health"),
                        ("прозвонка",), ("метрики",)}
          and not any(taken.values()), {k: v and v.facts for k, v in taken.items()})
    providers = yaml.safe_load((ROOT / "config" / "providers.yaml").read_text(encoding="utf-8"))
    urls = [(f"{section}.{name}", body["url"]) for section, table in (
        ("online.live", providers["online"]["live"]), ("capabilities.online", providers["capabilities"]["online"]),
        ("online.http", providers["online"]["http"])) for name, body in table.items()]
    wrong = [(where, url) for where, url in urls
             if decl_refusal({"x": {"url": url}}, "x", "url", kind=ProviderUrl) != "принято"]
    check(f"живые декларации: у всех {len(urls)} адресов провайдеров хост объявлен, а не подставлен",
          len(urls) >= 9 and not wrong, wrong)

    print("== 18. Срок обращения: повтор — в одном месте стека, срок идёт вниз; два потолка паузы ==")

    class Clock:
        """Часы и сон события: сон двигает часы; молчащий сервер двигает их на срок фазы, который дверь отдала транспорту
        (как httpx: фаза кончается тайм-аутом). Журнал — сроки фаз и паузы."""

        def __init__(self):
            self.now, self.slept, self.phases = 0.0, [], []

        def sleep(self, seconds):
            self.slept.append(round(seconds, 6))
            self.now += seconds

        def time(self):
            return self.now

        def hung(self, request):
            phase = request.extensions["timeout"]["connect"]
            self.phases.append(round(phase, 6))
            self.now += phase
            raise httpx.ConnectTimeout("сервер молчит")

    LIVE = yaml.safe_load((ROOT / "config" / "providers.yaml").read_text(encoding="utf-8"))["online"]["answer"]

    def timed(handle, clock: Clock, within=None, **over):
        """Ответ двери на подставных часах — (значение, отказ). Политика — живая online.answer, разброс 0 и правки
        события поверх."""
        decl = work / "answer.yaml"
        decl.write_text(yaml.safe_dump({"answer": {**LIVE, "jitter": 0, **over}}, allow_unicode=True), encoding="utf-8")
        door = answers(Declaration(decl, ContractError, "ответа"), SLOT, DECLARED, transport=httpx.MockTransport(handle),
                       resolve=World().resolve, sleep=clock.sleep, clock=clock.time, proxies=dict)
        return attempt(lambda: door.answer(API, {"Authorization": "Bearer t"}, within=within))

    def replies(*answers_):
        """Ответы API по очереди (код, заголовки, тело), последний повторяется; журнал — пути запросов."""
        log: list[str] = []

        def handle(request):
            log.append(request.url.path)
            status, headers, body = answers_[min(len(log), len(answers_)) - 1]
            return httpx.Response(status, headers=headers, content=body)
        return handle, log

    after, before = Clock(), Clock()
    _, e_after = timed(after.hung, after)
    _, e_before = timed(before.hung, before, deadline_sec=10**9)
    check(f"зависший провайдер списка и спеки: всё обращение — в deadline_sec {LIVE['deadline_sec']} с (после "
          f"{after.now:g} с; без срока, как в B2 до доделки, — {before.now:g} с); фазы урезаны до половины остатка",
          e_after is not None and e_after.code == "PROVIDER_FAILED" and after.now <= LIVE["deadline_sec"]
          and after.phases == [30, 14.75, 6.875] and before.phases == [30, 30, 30] and before.now == 91.5,
          f"{after.phases} {after.slept} {after.now} | {before.phases} {before.now} {e_after and e_after.facts}")
    clock = Clock()
    _, e = timed(clock.hung, clock, within=10)
    check("срок вызывающего короче своего — обращение в него укладывается, первая фаза — половина остатка",
          e is not None and clock.phases[0] == 5 and clock.now <= 10, f"{clock.phases} {clock.now}")
    clock = Clock()
    handle, log = replies((503, {"Retry-After": "70"}, b""))
    _, e = timed(handle, clock)
    check("сервер назвал срок длиннее остатка обращения — повтора нет, отказ сразу, срок — в фактах: решает вызывающий",
          e is not None and e.facts == {"host": "api.provider.example", "status": 503, "retry_after_sec": 70.0}
          and len(log) == 1 and not clock.slept, f"{e and e.facts} {log} {clock.slept}")
    clock = Clock()
    handle, log = replies((503, {"Retry-After": "200"}, b""))
    _, e = timed(handle, clock, deadline_sec=10**9)
    check("срок сервера длиннее retry_after_max_sec — повтора нет и без срока обращения, срок — в фактах",
          e is not None and e.facts.get("retry_after_sec") == 200.0 and len(log) == 1 and not clock.slept,
          f"{e and e.facts} {log} {clock.slept}")
    clock = Clock()
    handle, log = replies((503, {"Retry-After": "100"}, b""), (200, {}, json.dumps(LIST).encode()))
    got, e = timed(handle, clock, deadline_sec=10**9)
    check("срок сервера выше своего потолка паузы (backoff_max_sec 8), но в retry_after_max_sec — ждём его целиком "
          "(прежде дверь резала его до 8 с)",
          e is None and got == LIST and clock.slept == [100.0] and len(log) == 2, f"{clock.slept} {e and e.facts}")
    clock = Clock()

    def drip(request):
        def chunks():
            for _ in range(3):
                clock.now += 25
                yield b" " * 1024
        return httpx.Response(200, content=chunks())

    _, e = timed(drip, clock)
    check("капля тела за срок обращения — кусок после срока не принят, отказ кодом политики; перебор — не больше "
          "одной фазы чтения",
          e is not None and e.facts == {"host": "api.provider.example", "status": "срок обращения истёк на 2048 байт"}
          and clock.now <= LIVE["deadline_sec"] + LIVE["timeout_sec"], f"{e and e.facts} {clock.now}")

    # Два потолка у всех потребителей двери с B1 (файл, длина): свой — backoff_max_sec, сервера — retry_after_max_sec.
    fresh()
    w = World()
    w.statuses = [(503, {"Retry-After": "100"})]
    f, pauses = fetcher(w, attempts=2)
    _, e = attempt(lambda: f.fetch(RESOLVE, files, PART, "inst1", size=len(BODY)))
    check("смена поведения у загрузки весов: срок сервера 100 с между своим потолком (60) и retry_after_max_sec (120) "
          "выжидается целиком — прежде 60",
          e is None and pauses == [100.0] and on_disk() == BODY, f"{pauses} {e and (e.code, e.facts)}")
    fresh()
    w = World()
    w.statuses = [(503, {"Retry-After": "300"})]
    f, pauses = fetcher(w, attempts=3)
    _, e = attempt(lambda: f.fetch(RESOLVE, files, PART, "inst1", size=len(BODY)))
    check("смена поведения: срок сервера длиннее retry_after_max_sec — повтора нет, код потребителя со сроком в "
          "фактах (прежде — ждали потолок и повторяли)",
          e is not None and e.code == "MODEL_SOURCE_FAILED" and not pauses
          and e.facts == {"host": CDN, "status": 503, "retry_after_sec": 300.0}
          and len([x for x in w.log if x["host"] == CDN]) == 1, f"{pauses} {e and (e.code, e.facts)}")
    w = World()
    w.statuses = [(503, {"Retry-After": "300"})]
    f, pauses = fetcher(w, attempts=3)
    got, e = attempt(lambda: f.length(RESOLVE, linked="X-Linked-Size"))
    check("длина по HEAD: срок сервера длиннее потолка — «не узнано» сразу, без повторов (решает вызывающий)",
          e is None and got == (None, f"HEAD {CDN}: 503") and isinstance(got[1], Unreached) and not pauses,
          f"{got} {pauses} {e and e.facts}")

    print("== 19. Статус без тела и судья петли (B3): код — улика, переход — как есть, внутренняя сеть — только петле ==")
    from core.declaration_schemas import ProbePolicy

    PROBE = {k: v for k, v in ANSWER.items() if k not in ("max_bytes", "speed_floor_bytes_per_sec", "speed_window_sec")}
    PROBE["codes"] = {"failed": "PROVIDER_FAILED"}
    PROBE_SLOT = Slot("probe.yaml", ("probe",), ProbePolicy)

    def peek(api: Api, declared: str = DECLARED, **over):
        """Статус двери на подставном API — (исход, отказ, паузы); загрузчик собран читателем из декларации прозвонки."""
        decl = work / "probe.yaml"
        decl.write_text(yaml.safe_dump({"probe": {**PROBE, **over}}), encoding="utf-8")
        pauses: list[float] = []
        got, e = attempt(lambda: answers(Declaration(decl, ContractError, "прозвонки"), PROBE_SLOT, declared,
                                         transport=httpx.MockTransport(api.handle), resolve=World().resolve,
                                         sleep=pauses.append, proxies=dict).status(API))
        return got, e, pauses

    api = Api((530, b"x" * 9000, {"server": "cloudflare"}))
    got, e, _ = peek(api)
    check("статус: 530 — ответ (код и заголовки), а не отказ двери; тело не вытянуто",
          e is None and got[0] == 530 and got[1].get("server") == "cloudflare" and api.pulled == 0,
          (got, e and e.facts, api.pulled))
    api = Api((302, b"", {"location": "https://evil.example/x"}), (200, b"", {}))
    got, e, _ = peek(api)
    check("статус: переход возвращается как есть — по нему не идём, к хосту из Location ни запроса",
          e is None and got[0] == 302 and len(api.log) == 1, (got, e and e.facts, api.log))
    api = Api((503, b"", {}), (200, b"", {}))
    got, e, pauses = peek(api, retry_on=[503], attempts=2)
    check("статус: код из retry_on повторяется общим циклом двери, прочие коды — ответ",
          e is None and got[0] == 200 and len(api.log) == 2 and len(pauses) == 1, (got, pauses, e and e.facts))
    api = Api((200, b"", {}))
    e = attempt(lambda: Fetcher(FetchPolicy.model_validate({**RESULTS, "hosts": ["api.provider.example"]}),
                                transport=httpx.MockTransport(api.handle), resolve=World().resolve,
                                proxies=dict).status(API))[1]
    check("статус без политики обращения к объявленному адресу — ошибка кода до запроса",
          e is not None and e.code == "НЕ_ОТКАЗ" and e.facts["exception"].startswith("TypeError") and not api.log,
          (e and e.facts, api.log))
    for case, declared, admitted in (
            ("литерал петли", "http://127.0.0.1:8770/run", True),
            ("литерал петли IPv6", "http://[::1]:8770/run", True),
            ("имя localhost — не литерал: разрешение подменило бы адрес", "http://localhost:8770/run", False),
            ("адрес внутренней сети — не петля", "http://10.0.0.5:8770/run", False)):
        decl = work / "probe.yaml"
        decl.write_text(yaml.safe_dump({"probe": {**PROBE, "schemes": ["http"], "block_private": False}}),
                        encoding="utf-8")
        e = attempt(lambda: answers(Declaration(decl, ContractError, "прозвонки"), PROBE_SLOT, declared))[1]
        check(f"судья петли: {case} при block_private: false — {'принят' if admitted else 'отказ до запроса'}",
              e is None if admitted else (e is not None and e.code == "DOWNLOAD_FORBIDDEN"), e and (e.code, e.facts))

    print("== 20. Вызов API с телом (B4, NI-05): та же дверь; повтор — только не применённого, ключ — один на вызов ==")
    from core.declaration_schemas import CallPolicy
    from core.providers.download import addressed

    CALL = {**ANSWER, "retry_on": [408, 429, 503, 504],
            "idempotent_methods": ["GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"], "unprocessed": [408, 503],
            "idempotency_header": None, "codes": {"failed": "PROVIDER_FAILED", "corrupt": "DOWNLOAD_INCOMPLETE",
                                                  "unreached": "RUNNER_NOT_RUNNING", "timeout": "PROVIDER_TIMEOUT",
                                                  "outcome_unknown": "PROVIDER_OUTCOME_UNKNOWN"}}
    CALL_SLOT = Slot("call.yaml", ("call",), CallPolicy)
    OK = json.dumps({"ok": True}).encode()

    def dial(*replies, method="POST", declared=DECLARED, sink=None, clock=None, resolve=None, auth="Bearer t", **over):
        """Вызов двери на подставном API — (значение, отказ, журнал, паузы). Ответ — (код, заголовки[, тело]),
        генератор тела или исключение сети; последний повторяется. Журнал — (метод, ключ идемпотентности, тело, Host)."""
        log, queue = [], list(replies)

        def handle(request):
            log.append((request.method, request.headers.get("idem"), request.read(), request.headers.get("host")))
            reply = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(reply, Exception):
                raise reply
            if callable(reply):
                return reply(request)
            status, headers, *body = reply
            return httpx.Response(status, headers=headers, content=body[0] if body else OK)
        decl = work / "call.yaml"
        decl.write_text(yaml.safe_dump({"call": {**CALL, **over}}), encoding="utf-8")
        pauses: list[float] = []
        timed = {"clock": clock.time, "sleep": clock.sleep} if clock else {"sleep": pauses.append}
        got, e = attempt(lambda: answers(Declaration(decl, ContractError, "вызова"), CALL_SLOT, declared,
                                         transport=httpx.MockTransport(handle), resolve=resolve or World().resolve,
                                         proxies=dict,
                                         **timed).call(API, {"Authorization": auth}, method=method,
                                                       payload={"json": {"image": "x"}}, sink=sink))
        return got, e, log, pauses

    got, e, log, _ = dial((200, {}))
    check("законный: POST с телом дошёл тем же путём — JSON тела, метод, имя в Host; ответ — JSON в память",
          e is None and got == {"ok": True}
          and [(m, k, json.loads(b), h) for m, k, b, h in log] == [("POST", None, {"image": "x"}, "api.provider.example")],
          (got, e and e.facts, log))
    got, e, log, _ = dial(httpx.ConnectError("порт закрыт"), (200, {}))
    check("соединение не открылось — запрос не ушёл: POST повторён",
          e is None and got == {"ok": True} and [m for m, *_ in log] == ["POST", "POST"], (e and e.facts, log))
    failures = [socket.gaierror("имя не разрешено")]

    def flaky(host, port, *args, **kwargs):
        if failures:
            raise failures.pop()
        return World().resolve(host, port)

    got, e, log, _ = dial((200, {}), resolve=flaky)
    check("имя не разрешилось — запрос не ушёл: вызов повторён и дошёл одним POST",
          e is None and got == {"ok": True} and len(log) == 1 and not failures, (e and (e.code, e.facts), log))
    got, e, log, _ = dial(httpx.ReadTimeout("ответ не пришёл"), (200, {}))
    check("ответ не пришёл в фазу — запрос мог дойти: один POST, код timeout политики",
          e is not None and e.code == "PROVIDER_TIMEOUT"
          and e.facts == {"host": "api.provider.example", "status": "ReadTimeout"} and len(log) == 1, (e and e.facts, log))
    got, e, log, pauses = dial((503, {"Retry-After": "2"}, b""), (200, {}))
    check("503 из unprocessed — сервер запрос не обработал: POST повторён с паузой сервера",
          e is None and got == {"ok": True} and len(log) == 2 and pauses == [2.0], (e and e.facts, pauses, log))
    got, e, log, pauses = dial((504, {"Retry-After": "5"}, b""), (200, {}))
    check("504 вне unprocessed, ключа нет — POST не повторён, срок сервера в фактах отказа",
          e is not None and e.code == "PROVIDER_FAILED"
          and e.facts == {"host": "api.provider.example", "status": 504, "retry_after_sec": 5.0}
          and len(log) == 1 and not pauses, (e and e.facts, log, pauses))
    got, e, log, _ = dial((429, {}, b""), (200, {}))
    check("429 вне unprocessed раздела — POST не повторён: «не применён» для 429 объявляет только провайдер",
          e is not None and e.code == "PROVIDER_FAILED" and len(log) == 1, (e and e.facts, log))
    got, e, log, _ = dial((504, {}, b""), (200, {}), idempotency_header="Idem")
    keys = [k for _, k, *_ in log]
    check("504 с объявленным ключом идемпотентности — повтор с ТЕМ ЖЕ ключом: вторая попытка — тот же запрос",
          e is None and len(keys) == 2 and keys[0] and len(set(keys)) == 1, (e and e.facts, keys))
    got, e, log, _ = dial((200, {}), idempotency_header="Idem")
    _, _, log2, _ = dial((200, {}), idempotency_header="Idem")
    check("ключ идемпотентности — новый на каждый вызов: два вызова — два ключа",
          log[0][1] and log2[0][1] and log[0][1] != log2[0][1], (log[0][1], log2[0][1]))
    got, e, log, _ = dial(httpx.ReadTimeout("ответ не пришёл"), (200, {}), method="GET")
    check("GET — метод из idempotent_methods: ответ не пришёл — повтор",
          e is None and [m for m, *_ in log] == ["GET", "GET"], (e and e.facts, log))

    def cut(request):
        def chunks():
            yield b"{" + b" " * 1023
            raise httpx.ReadError("соединение оборвано")
        return httpx.Response(200, headers={"content-length": "4096"}, content=chunks())

    got, e, log, _ = dial(cut, (200, {}))
    check("обрыв тела ответа после отправки неповторяемого POST — исход неизвестен (outcome_unknown) с принятым и "
          "заявленным, POST не повторён",
          e is not None and e.code == "PROVIDER_OUTCOME_UNKNOWN"
          and e.facts == {"host": "api.provider.example", "got": 1024, "expected": 4096} and len(log) == 1,
          (e and (e.code, e.facts), log))
    got, e, log, _ = dial(cut, method="GET")
    check("обрыв тела повторяемого GET на каждой попытке — «не дочитан» (corrupt) с принятым и заявленным после "
          "attempts попыток",
          e is not None and e.code == "DOWNLOAD_INCOMPLETE"
          and e.facts == {"host": "api.provider.example", "got": 1024, "expected": 4096}
          and [m for m, *_ in log] == ["GET"] * 3, (e and (e.code, e.facts), log))
    got, e, log, _ = dial((200, {}, b"[" + b"1," * 3000 + b"1]"))
    check("ответ сверх max_bytes — DOWNLOAD_TOO_LARGE с ключом предела раздела вызова",
          e is not None and e.code == "DOWNLOAD_TOO_LARGE"
          and e.facts == {"limit_mb": 0.004, "key": "config/call.yaml → call.max_bytes"}, e and (e.code, e.facts))
    got, e, log, _ = dial((200, {}), declared="https://inner.example/{model}")
    check("имя провайдера ведёт во внутреннюю сеть — отказ до запроса: ни тела, ни ключа туда",
          e is not None and e.code == "DOWNLOAD_FORBIDDEN" and not log, (e and e.facts, log))
    seen: list = []

    def sink(response, host, deadline, cut=None):
        seen.append((response.status_code, host, isinstance(deadline, float), cut and cut(host, 1, None).code))
        return "сток"

    got, e, log, _ = dial((200, {"content-type": "image/png"}, b"png"), sink=sink)
    check("сток получает открытый ответ, имя хоста (а не судимый IP), срок вызова и обрыв вызова (у POST — исход "
          "неизвестен); его значение — ответ вызова",
          e is None and got == "сток" and seen == [(200, "api.provider.example", True, "PROVIDER_OUTCOME_UNKNOWN")],
          (got, e and e.facts, seen))
    seen.clear()
    got, e, log, _ = dial((500, {}, b"{}"), sink=sink)
    check("код отказа — до стока: сток не зван", e is not None and e.code == "PROVIDER_FAILED" and not seen, (e, seen))

    clock = Clock()
    got, e, log, _ = dial(clock.hung, clock=clock, deadline_sec=60, attempts=5)
    check("висящее соединение на подставных часах: не ушедший запрос повторяется, но весь вызов — в deadline_sec "
          f"(конец {clock.now:g} с ≤ 60, фазы {clock.phases})",
          e is not None and e.code == "PROVIDER_TIMEOUT" and clock.now <= 60 and len(clock.phases) >= 2,
          (e and e.facts, clock.phases, clock.now))
    clock = Clock()

    def drip(request):
        def chunks():
            for _ in range(10):
                clock.now += 25
                yield b" " * 64
        return httpx.Response(200, content=chunks())

    got, e, log, _ = dial(drip, clock=clock, deadline_sec=60)
    check("капля тела на подставных часах: кусок после срока не принят — код timeout политики; перебор — не больше "
          f"одной фазы чтения (конец {clock.now:g} с ≤ 60 + 30), POST один",
          e is not None and e.code == "PROVIDER_TIMEOUT" and clock.now <= 60 + 30 and len(log) == 1,
          (e and e.facts, clock.now, log))
    check("схема: срок короче одной полной попытки — отказ декларации до сети (deadline_sec < 2 × timeout_sec)",
          dial((200, {}), deadline_sec=59)[1] is not None
          and dial((200, {}), deadline_sec=59)[1].facts.get("details") == "call.deadline_sec (deadline_under_attempt)")
    api = Api((200, OK, {}))
    e = attempt(lambda: Fetcher(AnswerPolicy.model_validate({**ANSWER}).model_copy(update={"hosts": ("api.provider.example",)}),
                                transport=httpx.MockTransport(api.handle), resolve=World().resolve,
                                proxies=dict).call(API, method="POST"))[1]
    check("вызов без политики вызова (CallPolicy) — ошибка кода до запроса",
          e is not None and e.code == "НЕ_ОТКАЗ" and e.facts["exception"].startswith("TypeError") and not api.log,
          (e and e.facts, api.log))
    policy = CallPolicy.model_validate({**CALL, "schemes": ["http"]})
    for case, declared, allowed in (
            ("литерал петли", "http://127.0.0.1:8770/run", None),
            ("публичный хост по http — открытый канал только петле, и при block_private: true",
             "http://api.provider.example/v1", "литерал петли (http)")):
        e = attempt(lambda: addressed(policy, CALL_SLOT, declared))[1]
        check(f"судья открытого канала: {case} — {'принят' if allowed is None else 'отказ до запроса'}",
              (e is None) if allowed is None else (e is not None and e.facts.get("allowed") == allowed),
              e and (e.code, e.facts))

    print("== 21. Значение заголовка судят библиотеки запроса до него: что они не несут — отказ с именем, без значения ==")
    CANARY = "канарейка-7f3a"

    def hidden(e, secret: str = CANARY) -> bool:
        """Значения нет ни в тексте отказа, ни в фактах, ни в трассе с цепочкой — том, что пишет журнал инцидента."""
        return e is not None and secret not in "".join(traceback.format_exception(e)) + repr(e.facts)

    fresh()
    w = World()
    got, e = pull(w, size=len(BODY), digest=SHA, headers={"Authorization": "Bearer\tt"})
    check("законный: ASCII с табуляцией — заголовок ушёл как есть, файл лёг",
          e is None and on_disk() == BODY and w.log[0]["auth"] == "Bearer\tt", (e and (e.code, e.facts), w.log[:1]))
    fresh()
    w = World()
    got, e = pull(w, size=len(BODY), digest=SHA, headers={"Authorization": f"Bearer {CANARY}"})
    check("токен не ASCII у загрузки весов — REQUEST_HEADER_INVALID с хостом и именем заголовка; ни разрешения имени, "
          "ни запроса; значения нет в отказе и в его трассе",
          e is not None and e.code == "REQUEST_HEADER_INVALID"
          and e.facts == {"host": "huggingface.co", "header": "Authorization"} and not w.log and not w.resolved
          and hidden(e), (e and (e.code, e.facts), w.log, w.resolved))
    for case, auth, secret in (("перевод строки", "Bearer canary7f3a\r\nX-Evil: 1", "canary7f3a"),
                               ("NUL", "Bearer canary7f3a\x00", "canary7f3a"), ("не ASCII", f"Bearer {CANARY}", CANARY)):
        got, e, log, _ = dial((200, {}), auth=auth)
        check(f"вызов API, {case} в ключе — отказ до запроса (не «мог дойти»): ни POST, значения нет",
              e is not None and e.code == "REQUEST_HEADER_INVALID"
              and e.facts == {"host": "api.provider.example", "header": "Authorization"} and not log
              and hidden(e, secret), (e and (e.code, e.facts), log))
finally:
    shutil.rmtree(work, ignore_errors=True)

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
