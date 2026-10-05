"""core/providers/download.py — один загрузчик сервера: части весов и результаты поставщиков по ссылке.

Ссылку выдаёт источник, то есть она приходит ИЗВНЕ и проверяется как любой чужой ввод: схема и адрес — ДО
запроса и на каждом переходе, иначе ссылка на служебный адрес облака или на петлю уводит сервер туда, куда
клиент не достаёт. Предел размера считается по ходу потока, свой остаток докачивается, длина и отпечаток
сверяются до того, как файл встанет под своё имя. Приёмка «файл действительно лёг» у результатов — в цикле задач.
"""

import contextlib
import errno
import hashlib
import ipaddress
import logging
import os
import random
import re
import socket
import time
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

from core.contracts import ContractError
from core.declaration import Declaration
from core.paths import Files, PathEscapeError
from .task_cycle import TaskCycleError

log = logging.getLogger(__name__)

REDIRECTS = {301, 302, 303, 307, 308}                   # ответы, у которых новый адрес — в Location (RFC 9110 §15.4)
PARTIAL = 206                                           # часть по Range (RFC 9110 §15.3.7)
REFUSED_FROM = 400                                      # 4xx и 5xx — отказ клиента и сервера (RFC 9110 §15.5–15.6)
PORTS = {"https": 443, "http": 80}
DIGEST_BLOCK = 2**20                                    # отметка работы на каждый МиБ сверки: 30 мс на медленном диске


def inner_address(raw: str) -> bool:
    """Адрес внутренней сети, петли, служебный: по нему сервер не ходит по чужой ссылке."""
    ip = ipaddress.ip_address(raw)
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def retry_after(value, ceiling: float, own: float) -> float:
    """Срок повтора, названный сервером: секунды или HTTP-дата (RFC 9110 §10.2.3), не больше потолка.

    Нечитаемое значение — своя пауза и запись в журнал: чужой заголовок не той формы не роняет вызов.
    """
    text = str(value or "").strip()
    if not text:
        return own
    if text.isdigit():
        return min(float(text), ceiling)
    try:
        seconds = parsedate_to_datetime(text).timestamp() - time.time()
    except (TypeError, ValueError):
        log.warning("Retry-After не разобран (%r): пауза своя, %.1f с", text[:64], own)
        return own
    return min(max(seconds, 0.0), ceiling)


def file_digest(path: Path, algo: str, mark=None) -> str:
    """Отпечаток файла потоком, не читая целиком; id git-блоба — sha1 от «blob <длина>\\0» и содержимого.

    `mark()` — на каждый прочитанный блок: долгая сверка большой части на медленном диске подаёт признаки работы.
    """
    digest = hashlib.sha1(f"blob {path.stat().st_size}\0".encode()) if algo == "git-blob-sha1" else hashlib.new(algo)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(DIGEST_BLOCK), b""):
            digest.update(block)
            if mark:
                mark()
    return digest.hexdigest()


class _Again(Exception):
    """Попытка оборвалась так, что повтор осмыслен (обрыв, перегрузка, молчание); несёт отказ на случай исчерпания."""

    def __init__(self, refusal: ContractError, wait: float = 0.0):
        super().__init__(refusal.code)
        self.refusal, self.wait = refusal, wait


class Fetcher:
    """Часть по ссылке в файл корня — один загрузчик сервера для весов и для результатов поставщиков.

    Остаток — под именем владельца и переживает процесс (MF-04); докачка — `Range` от его длины с `If-Range` валидатором
    того сервера, что отдавал часть (MF-03): заметка остатка хранит ETag или Last-Modified и конечный хост. Адрес
    судится до каждого запроса и на каждом переходе, соединение идёт на тот IP, который судили (MF-15). Длина и
    отпечаток сверяются до того, как файл встанет под своё имя (MF-05, MF-06). Политика и коды отказов потребителя —
    данные его декларации; транспорт, разрешение имён и сон внедряются (MF-16).
    """

    # Политика загрузчика — одна форма для каждой декларации потребителя (веса, результаты поставщиков).
    POLICY = ("schemes", "hosts", "block_private", "max_bytes", "timeout_sec", "redirects", "attempts",
              "backoff_sec", "backoff_max_sec", "retry_after_max_sec", "jitter", "retry_on", "codes")

    def __init__(self, policy: dict, *, transport=None, resolve=socket.getaddrinfo, sleep=time.sleep,
                 rng: random.Random | None = None, proxies=urllib.request.getproxies_environment, mark=None):
        missing = [key for key in self.POLICY if key not in policy]
        if missing:
            details = f"в политике загрузки не объявлено: {', '.join(missing)}"
            raise ContractError("SCHEMA_INVALID", details, facts={"details": details})
        self.policy = policy
        self.transport = transport
        self.resolve = resolve
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.proxies = proxies
        self.mark = mark or (lambda: None)      # признак работы: переход, принятый кусок, начало паузы, блок сверки

    def fetch(self, url: str, files: Files, path: str | Path, owner: str, *, size: int | None = None,
              digest: str = "", algo: str = "sha256", headers: dict | None = None) -> dict:
        """Скачать `url` в `path` корня `files` — исход {path, bytes, resumed_from, restarted, host}.

        Обрыв оставляет остаток с заметкой, и следующий вызов того же владельца докачивает. Каждая попытка идёт от
        исходного адреса: подписанная ссылка CDN истекает, а переход судится и разрешается заново.
        """
        part = files.remnant(path, owner)
        part.parent.mkdir(parents=True, exist_ok=True)
        resumed_from = self._usable(files, path, owner, part, size)[0]
        cap, jitter = float(self.policy["backoff_max_sec"]), float(self.policy["jitter"])
        pause, attempts, restarted = float(self.policy["backoff_sec"]), max(1, int(self.policy["attempts"])), False
        with contextlib.ExitStack() as stack:
            routes: dict[str, object] = {}

            def client(proxy: str):
                if proxy not in routes:
                    routes[proxy] = stack.enter_context(self._client(proxy))
                return routes[proxy]

            for attempt in range(attempts):
                try:
                    host, again = self._attempt(client, url, files, path, owner, part, size, headers)
                    restarted = restarted or again
                    break
                except _Again as e:
                    if attempt + 1 == attempts:
                        raise e.refusal from e
                    self.mark()
                    self.sleep(min(max(pause, e.wait), cap) * (1 + jitter * self.rng.random()))
                    pause = min(pause * 2, cap)
        got = part.stat().st_size
        found = file_digest(part, algo, self.mark) if digest else ""
        if found != digest.lower():
            files.drop(path, owner)
            raise ContractError(self.policy["codes"]["corrupt"],
                                facts={"part": files.name(files.place(path)), "expected": digest, "got": found})
        return {"path": files.publish(path, owner), "bytes": got, "resumed_from": resumed_from,
                "restarted": restarted, "host": host}

    @staticmethod
    def _usable(files: Files, path, owner: str, part: Path, size: int | None) -> tuple[int, str, str]:
        """Свой остаток, годный для докачки — (длина, валидатор, хост); без валидатора или длиннее состава — сброшен."""
        note = (files.noted(path, owner) or {}) if part.exists() else {}
        validator = str(note.get("etag") or note.get("last_modified") or "")
        if part.exists() and (not validator or (size is not None and part.stat().st_size > size)):
            files.drop(path, owner)
        if not part.exists():
            return 0, "", ""
        return part.stat().st_size, validator, str(note.get("host") or "")

    def _attempt(self, client, url: str, files: Files, path, owner: str, part: Path, size: int | None,
                 headers: dict | None) -> tuple[str, bool]:
        """Одна попытка до конца тела — (конечный хост, начато ли заново); повторимое — `_Again`, иное — отказ кодом."""
        offset, validator, held = self._usable(files, path, owner, part, size)
        if size is not None and offset == size and offset:
            return held, False
        response, host = self._open(client, url, headers, offset, validator, held)
        try:
            status = response.status_code
            if status in {int(c) for c in self.policy["retry_on"]}:
                raise _Again(self._failed(host, status), retry_after(
                    response.headers.get("Retry-After"), float(self.policy["retry_after_max_sec"]), 0.0))
            if status >= REFUSED_FROM:
                raise self._failed(host, status)
            restarted = False
            if status == PARTIAL:
                got = str(response.headers.get("content-range") or "")
                start = re.fullmatch(r"bytes (\d+)-\d+/(?:\d+|\*)", got.strip())
                if not start or int(start.group(1)) != offset:
                    raise ContractError("DOWNLOAD_RANGE_INVALID",
                                        facts={"host": host, "expected": offset, "got": got[:80]})
            else:                                           # 200 — целиком: Range не принят или источник сменил файл
                restarted, offset = offset > 0, 0
                files.drop(path, owner)
            mark = self._validator(response)
            if mark:
                files.note(path, owner, {**mark, "host": host})
            self.take(response, files, path, owner, host=host, size=size, offset=offset)
        finally:
            response.close()
        return host, restarted

    @staticmethod
    def _append(files: Files, path, owner: str, part: Path) -> int:
        """Дескриптор дозаписи своего остатка, не проходя ссылку на его месте (FS-14): ссылку снимает `drop`, сама
        жертва цела, вызов — отказ; повтор начнёт с нуля."""
        try:
            return os.open(part, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o666)
        except OSError as e:
            if e.errno != errno.ELOOP:
                raise
            files.drop(path, owner)
            raise PathEscapeError(files.name(part)) from e

    def _open(self, client, url: str, headers: dict | None, offset: int, validator: str, held: str):
        """Запрос с переходами — (ответ потоком, конечный хост). Каждый адрес судится до запроса.

        Напрямую — соединение на судимый IP (`Host` и SNI — исходное имя); через прокси среды — по имени, его разрешает
        прокси, а суд схемы и хоста тот же. Заголовки потребителя (ключ) уходят только исходному хосту; `Range` с
        `If-Range` — только хосту, чей валидатор лежит в заметке: валидатор одного сервера другому ничего не говорит.
        """
        import httpx

        origin = httpx.URL(url).host
        for _ in range(int(self.policy["redirects"]) + 1):
            target = httpx.URL(url)
            self._allowed(target)
            proxy = self._proxy(target)
            sent = dict(headers or {}) if target.host == origin else {}
            if offset and target.host == held:
                sent.update({"Range": f"bytes={offset}-", "If-Range": validator})
            sent["Host"] = target.netloc.decode("ascii")
            route = client(proxy)
            if proxy:
                request = route.build_request("GET", target, headers=sent)
            else:
                request = route.build_request("GET", target.copy_with(host=self._judge(target)), headers=sent,
                                              extensions={"sni_hostname": target.host})
            try:
                self.mark()
                response = route.send(request, stream=True)
            except httpx.TransportError as e:
                raise _Again(self._failed(target.host, type(e).__name__)) from e
            location = response.headers.get("location")
            if response.status_code not in REDIRECTS or not location:
                return response, target.host
            response.close()
            url = str(target.join(location))
        raise self._failed(httpx.URL(url).host, f"переходов больше {self.policy['redirects']}")

    def _proxy(self, target) -> str:
        """Прокси среды для адреса или пусто: схема и NO_PROXY — по правилам urllib, как у httpx с trust_env."""
        found = self.proxies()
        proxy = str(found.get(target.scheme) or found.get("all") or "")
        # есть в urllib.request 3.11–3.14 на любой ОС, вне typeshed и документации; NO_PROXY сторожит test_fetcher
        bypass = urllib.request.proxy_bypass_environment(target.host, found)  # type: ignore[attr-defined]
        return "" if proxy and bypass else proxy

    def _allowed(self, target) -> None:
        """Схема и хост — ровно из объявленных; `объявленный@чужой` — хост чужой, имя пользователя не в счёт."""
        schemes = [str(s).lower() for s in self.policy["schemes"]]
        hosts = [str(h).lower() for h in self.policy["hosts"]]
        if target.scheme not in schemes:
            raise ContractError("DOWNLOAD_FORBIDDEN", facts={"host": target.host, "allowed": ", ".join(schemes)})
        if hosts and target.host not in hosts:
            raise ContractError("DOWNLOAD_FORBIDDEN", facts={"host": target.host, "allowed": ", ".join(hosts)})

    def _judge(self, target) -> str:
        """IP для прямого соединения: все адреса имени — публичные, соединение — на первый, второго разрешения нет."""
        try:
            found = self.resolve(target.host, target.port or PORTS[target.scheme], type=socket.SOCK_STREAM)
        except OSError as e:
            raise _Again(self._failed(target.host, type(e).__name__)) from e
        addresses = list(dict.fromkeys(str(info[4][0]) for info in found))
        inner = next((a for a in addresses if inner_address(a)), None) if self.policy["block_private"] else None
        if inner or not addresses:
            raise ContractError("DOWNLOAD_FORBIDDEN", facts={"host": target.host, "address": inner or "—"})
        return addresses[0]

    def _client(self, proxy: str):
        """Клиент маршрута: напрямую — без прокси среды (соединение закреплено за судимым IP), иначе — через названный
        прокси. Подставной транспорт заменяет сеть целиком, маршрут через прокси тоже."""
        import httpx

        route = {"transport": self.transport} if self.transport is not None or not proxy else {"proxy": proxy}
        timeout = float(self.policy["timeout_sec"])
        return httpx.Client(**route, timeout=timeout, follow_redirects=False, trust_env=False)

    @staticmethod
    def _validator(response) -> dict:
        """Валидатор для `If-Range`: сильный ETag, иначе Last-Modified; слабый ETag докачку не разрешает
        (RFC 9110 §13.1.5)."""
        etag = str(response.headers.get("etag") or "")
        if etag and not etag.startswith("W/"):
            return {"etag": etag}
        modified = str(response.headers.get("last-modified") or "")
        return {"last_modified": modified} if modified else {}

    @staticmethod
    def _length(response, offset: int) -> int | None:
        """Ожидаемая длина целого файла, когда состав её не назвал: смещение плюс Content-Length ответа."""
        told = str(response.headers.get("content-length") or "")
        return offset + int(told) if told.isdigit() else None

    def _failed(self, host: str, status) -> ContractError:
        return ContractError(self.policy["codes"]["failed"], facts={"host": host, "status": status})

    @staticmethod
    def _short(host: str, got: int, expected: int | None) -> ContractError:
        return ContractError("DOWNLOAD_INCOMPLETE",
                             facts={"host": host, "got": got, "expected": "—" if expected is None else expected})

    def silence_sec(self) -> float:
        """Наибольшая тишина между отметками работы: на переходе — соединение и ожидание ответа (2 × timeout_sec,
        httpx меряет их порознь), либо пауза перед повтором (backoff_max_sec × (1 + jitter) — потолок сна в `fetch`);
        число попыток ограничено `attempts`, так что отметки не вечны."""
        timeout, cap = float(self.policy["timeout_sec"]), float(self.policy["backoff_max_sec"])
        return max(2 * timeout, cap * (1 + float(self.policy["jitter"])))

    def take(self, response, files: Files, path, owner: str, *, host: str, size: int | None = None,
             offset: int = 0) -> int:
        """Тело открытого ответа — в остаток владельца без своего буфера: принятое до обрыва лежит на диске (MF-04).
        Длина — из состава или заявленная ответом; больше состава или предела `max_bytes` политики — остаток снят и отказ
        кодом; обрыв или недобор — `_Again` с принятым. Признак работы — на каждом принятом куске (M4)."""
        import httpx

        part = files.remnant(path, owner)
        expected = size if size is not None else self._length(response, offset)
        limit, over = int(self.policy["max_bytes"]), ""
        with os.fdopen(self._append(files, path, owner, part), "ab") as fh:
            try:
                for chunk in response.iter_bytes():
                    offset += len(chunk)
                    if size is not None and offset > size:
                        over = "size"
                        break
                    if limit and offset > limit:
                        over = "limit"
                        break
                    fh.write(chunk)
                    self.mark()
            except httpx.TransportError as e:
                raise _Again(self._short(host, offset, expected)) from e
        if over:
            files.drop(path, owner)
            if over == "limit":
                raise ContractError("DOWNLOAD_TOO_LARGE", facts={"limit_mb": round(limit / 2**20, 3)})
            raise ContractError(self.policy["codes"]["corrupt"],
                                facts={"part": files.name(files.place(path)), "expected": size, "got": offset})
        if expected is not None and offset < expected:
            raise _Again(self._short(host, offset, expected))
        return offset


def results(config_file: str | Path, **seams) -> Fetcher:
    """Загрузчик результатов поставщиков по `download.fetch` декларации задач; ссылки на файл (`url_fields`) — там же."""
    decl = Declaration(config_file, TaskCycleError, "ожидания",
                       "Заведи config/media_tasks.yaml — правила загрузки объявлены там.")
    return Fetcher(decl.need("download", "fetch", kind=dict), **seams)
