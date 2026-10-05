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

from core.contracts import ContractError
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
           "retry_after_max_sec": 120, "jitter": 0, "retry_on": [429, 500, 502, 503, 504],
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
                         "auth": request.headers.get("authorization")})
        if host == "huggingface.co" and "/resolve/" in request.url.path:
            return httpx.Response(302, headers={"location": self.location or
                                                f"https://{CDN}/xet-bridge-us/72ce?Signature={self.signature}"})
        if host == CDN and request.url.params.get("Signature") != self.signature:
            return httpx.Response(403)                # подпись истекла
        if self.statuses:
            status, headers = self.statuses.pop(0)
            return httpx.Response(status, headers=headers)
        body, start = self.body[:self.short] if self.short else self.body, 0
        asked = request.headers.get("range", "")
        if asked and self.honor_range and request.headers.get("if-range") in {self.etag, self.modified} - {""}:
            start = int(asked.split("=")[1].rstrip("-"))
        headers = {k: v for k, v in (("etag", self.etag), ("last-modified", self.modified)) if v}
        if start:
            headers["content-range"] = f"bytes {start + self.shift}-{len(body) - 1}/{len(body)}"
        rest = body[start:]
        headers["content-length"] = str(len(rest))
        cut, self.cut = self.cut, None
        return httpx.Response(206 if start else 200, headers=headers, content=self._stream(rest, cut))

    @staticmethod
    def _stream(rest: bytes, cut: int | None):
        for i in range(0, len(rest), 1024):
            if cut is not None and i >= cut:
                raise httpx.ReadError("соединение оборвано")
            yield rest[i:i + 1024]


def fetcher(world: World, policy: dict = WEIGHTS, proxies: dict | None = None, **over) -> tuple[Fetcher, list]:
    """Загрузчик на подставном мире; прокси — только названные тестом, не из среды прогона (TS-09)."""
    pauses: list[float] = []
    return Fetcher({**policy, **over}, transport=httpx.MockTransport(world.handle), resolve=world.resolve,
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

    print("== 13. Политика — полная или отказ кодом при постройке, а не KeyError посреди загрузки ==")
    e = attempt(lambda: Fetcher({k: v for k, v in WEIGHTS.items() if k not in ("codes", "redirects")}))[1]
    check("в политике нет ключей — SCHEMA_INVALID, и он их называет",
          e is not None and e.code == "SCHEMA_INVALID" and "codes" in e.facts.get("details", "")
          and "redirects" in e.facts.get("details", ""), e and (e.code, e.facts))
    check("законный: полная политика строит загрузчик", attempt(lambda: Fetcher(dict(WEIGHTS)))[1] is None)

    print("== 14. Отметки работы: переход, кусок, начало паузы, блок сверки — простой не ловит законную тишину ==")
    fresh()
    w = World()
    w.statuses = [(503, {"Retry-After": "7"})]
    events: list[str] = []
    f = Fetcher({**WEIGHTS, "attempts": 2}, transport=httpx.MockTransport(w.handle), resolve=w.resolve,
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
          Fetcher(WEIGHTS).silence_sec() == 60.0
          and Fetcher({**WEIGHTS, "timeout_sec": 90, "backoff_max_sec": 200, "jitter": 0.5}).silence_sec() == 300.0,
          (Fetcher(WEIGHTS).silence_sec()))

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
finally:
    shutil.rmtree(work, ignore_errors=True)

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
