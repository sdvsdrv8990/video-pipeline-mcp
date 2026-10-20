"""core/providers/download.py — один загрузчик сервера: части весов, результаты поставщиков по ссылке, ответы API.

Ссылку выдаёт источник, то есть она приходит ИЗВНЕ и проверяется как любой чужой ввод: схема и адрес — ДО
запроса и на каждом переходе, иначе ссылка на служебный адрес облака или на петлю уводит сервер туда, куда
клиент не достаёт. Предел размера считается по ходу потока, свой остаток докачивается, длина и отпечаток
сверяются до того, как файл встанет под своё имя. Приёмка «файл действительно лёг» у результатов — в цикле задач.
"""

import contextlib
import errno
import functools
import hashlib
import ipaddress
import json
import logging
import math
import os
import random
import re
import secrets
import socket
import stat
import sys
import time
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

from core.contracts import ContractError
from core.declaration import Declaration
from core.declaration_schemas import (RESULT_FETCH, AddressedPolicy, AnswerPolicy, CallCodes, CallPolicy, FetchPolicy,
                                      RequestPolicy, ResultFetchPolicy, Slot, declared_host)
from core.paths import Files, PathEscapeError
from .task_cycle import TaskCycleError

log = logging.getLogger(__name__)

REDIRECTS = {301, 302, 303, 307, 308}                   # ответы, у которых новый адрес — в Location (RFC 9110 §15.4)
PARTIAL = 206                                           # часть по Range (RFC 9110 §15.3.7)
REFUSED_FROM = 400                                      # 4xx и 5xx — отказ клиента и сервера (RFC 9110 §15.5–15.6)
PORTS = {"https": 443, "http": 80}
DIGEST_BLOCK = 2**20                                    # отметка работы на каждый МиБ сверки: 30 мс на медленном диске

if sys.platform == "win32":
    def _unfollowed(part: Path) -> int:
        """Дозапись без ссылки там, где O_NOFOLLOW нет (он — только Unix): ссылка на месте остатка (lstat) или подмена
        между проверкой и открытием (fstat ≠ lstat) — ELOOP, как у O_NOFOLLOW. O_BINARY — без перевода строк CRT."""
        if part.is_symlink():
            raise OSError(errno.ELOOP, "ссылка на месте остатка", str(part))
        fd = os.open(part, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_BINARY, 0o666)
        opened, there = os.fstat(fd), os.lstat(part)
        if stat.S_ISLNK(there.st_mode) or (opened.st_dev, opened.st_ino) != (there.st_dev, there.st_ino):
            os.close(fd)
            raise OSError(errno.ELOOP, "остаток подменён ссылкой при открытии", str(part))
        return fd


def inner_address(raw: str) -> bool:
    """Адрес внутренней сети, петли, служебный: по нему сервер не ходит по чужой ссылке."""
    ip = ipaddress.ip_address(raw)
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _loopback(raw: str) -> bool:
    """IP-литерал петли — единственный хост, которому политика может открыть внутреннюю сеть: имя подменило бы
    разрешение, а соседний адрес внутренней сети петлёй не является."""
    try:
        return ipaddress.ip_address(raw).is_loopback
    except ValueError:
        return False


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
    digest = (hashlib.sha1(f"blob {path.stat().st_size}\0".encode(), usedforsecurity=False) if algo == "git-blob-sha1"
              else hashlib.new(algo))
    with path.open("rb") as f:
        for block in iter(lambda: f.read(DIGEST_BLOCK), b""):
            digest.update(block)
            if mark:
                mark()
    return digest.hexdigest()


class _Again(Exception):
    """Попытка оборвалась так, что повтор осмыслен (обрыв, перегрузка, молчание); несёт отказ на случай исчерпания и
    `applied` — запрос мог дойти и выполниться: неповторяемый вызов (`Fetcher.call`) такую попытку не повторяет."""

    def __init__(self, refusal: ContractError, wait: float = 0.0, *, applied: bool = True):
        super().__init__(refusal.code)
        self.refusal, self.wait, self.applied = refusal, wait, applied


def unapplied(error: BaseException) -> bool:
    """Отказ двери пришёл до того, как провайдер принял запрос: не ушёл или ответ из `unprocessed` — признак последней
    попытки (`_Again.applied`, причина отказа), а не догадка по коду. Иной отказ мог исполниться и списаться — False."""
    cause = error.__cause__
    return isinstance(cause, _Again) and not cause.applied


P = TypeVar("P", bound=RequestPolicy)
A = TypeVar("A", bound=AddressedPolicy)


class Fetcher(Generic[P]):
    """Часть по ссылке в файл корня — один загрузчик сервера для весов и для результатов поставщиков.

    Остаток — под именем владельца и переживает процесс (MF-04); докачка — `Range` от его длины с `If-Range` валидатором
    того сервера, что отдавал часть (MF-03): заметка остатка хранит ETag или Last-Modified и конечный хост. Адрес
    судится до каждого запроса и на каждом переходе, соединение идёт на тот IP, который судили (MF-15). Длина и
    отпечаток сверяются до того, как файл встанет под своё имя (MF-05, MF-06). Политика и коды отказов потребителя —
    данные его декларации (`slot` — откуда она прочитана: путь ключа в рецепте отказа); транспорт, разрешение имён,
    сон и часы внедряются (MF-16).
    """

    def __init__(self, policy: P, *, transport=None, resolve=socket.getaddrinfo, sleep=time.sleep,
                 rng: random.Random | None = None, proxies=urllib.request.getproxies_environment, mark=None,
                 clock=time.monotonic, slot: Slot | None = None, port: int | None = None):
        self.policy = policy
        self.port = port                        # открытая петля (`addressed`): ровно объявленный порт, не любой на хосте
        self.transport = transport
        self.resolve = resolve
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.proxies = proxies
        self.mark = mark or (lambda: None)      # признак работы: переход, принятый кусок, начало паузы, блок сверки
        self.clock = clock                      # мера скорости тела и срок обращения меряются этими часами
        self.slot = slot

    def fetch(self, url: str, files: Files, path: str | Path, owner: str, *, size: int | None = None,
              digest: str = "", algo: str = "sha256", headers: dict | None = None) -> dict:
        """Скачать `url` в `path` корня `files` — исход {path, bytes, resumed_from, restarted, host}.

        Обрыв оставляет остаток с заметкой, и следующий вызов того же владельца докачивает. Каждая попытка идёт от
        исходного адреса: подписанная ссылка CDN истекает, а переход судится и разрешается заново.
        """
        body = self._body()
        part = files.remnant(path, owner)
        part.parent.mkdir(parents=True, exist_ok=True)
        resumed_from = self._usable(files, path, owner, part, size)[0]
        with contextlib.ExitStack() as stack:
            client = self._routes(stack)
            try:
                host, restarted = self._retry(
                    lambda: self._attempt(client, url, files, path, owner, part, size, headers))
            except _Again as e:
                raise e.refusal from e
        got = part.stat().st_size
        found = file_digest(part, algo, self.mark) if digest else ""
        if found != digest.lower():
            files.drop(path, owner)
            raise ContractError(body.codes.corrupt,
                                facts={"part": files.name(files.place(path)), "expected": digest, "got": found})
        return {"path": files.publish(path, owner), "bytes": got, "resumed_from": resumed_from,
                "restarted": restarted, "host": host}

    def _body(self) -> FetchPolicy:
        """Политика приёма тела: загрузчик, построенный с политикой без тела (длина по HEAD), тело не принимает — это
        ошибка кода вызывающего, а не декларации; проверка до первого запроса."""
        if not isinstance(self.policy, FetchPolicy):
            raise TypeError(f"приём тела требует FetchPolicy, а загрузчик построен с {type(self.policy).__name__}")
        return self.policy

    def _memory(self) -> AnswerPolicy:
        """Политика ответа в память: предел и срок обращения, хост — из объявленного адреса (читатель `answers`); иная —
        ошибка кода вызывающего, проверка до первого запроса."""
        if not (isinstance(self.policy, AnswerPolicy) and self.policy.hosts):
            raise TypeError("ответ в память требует AnswerPolicy с хостом объявленного адреса (читатель answers), "
                            f"а загрузчик построен с {type(self.policy).__name__}")
        return self.policy

    def _addressed(self) -> AddressedPolicy:
        """Политика обращения к объявленному адресу (статус): срок обращения и хост из адреса (читатель `answers`)."""
        if not (isinstance(self.policy, AddressedPolicy) and self.policy.hosts):
            raise TypeError("статус требует AddressedPolicy с хостом объявленного адреса (читатель answers), "
                            f"а загрузчик построен с {type(self.policy).__name__}")
        return self.policy

    def _deadline(self, policy: AddressedPolicy, within: float | None) -> float:
        """Срок обращения по часам двери: `deadline_sec` политики, срок вызывающего `within` (секунды) его только
        сокращает; бюджета нет — отказ до запроса."""
        budget = policy.deadline_sec if within is None else min(policy.deadline_sec, within)
        if budget <= 0:
            raise self._late(policy.hosts[0], "срок обращения исчерпан до запроса")
        return self.clock() + budget

    def _retry(self, attempt, deadline: float | None = None, *, replayable: bool = True):
        """Попытка до исхода — один цикл повтора для всех приёмников двери. Повторимое (`_Again`) — пауза: своя растёт
        вдвое от `backoff_sec` до `backoff_max_sec` с разбросом `jitter`; срок сервера (Retry-After) не короче своей и до
        `retry_after_max_sec`. Последний `_Again` уходит наружу — отказ это или «не узнано», решает приёмник — после
        `attempts` попыток, а раньше, если сервер назвал срок длиннее `retry_after_max_sec` или пауза не оставляет
        времени до срока обращения `deadline` (часы двери): названный сервером срок — в фактах отказа, повторять ли
        позже, решает вызывающий. Неповторяемый вызов (`replayable` False) повторяет только попытку, чей запрос не
        применён (`_Again.applied` False)."""
        cap, jitter = self.policy.backoff_max_sec, self.policy.jitter
        pause, left = self.policy.backoff_sec, self.policy.attempts
        while True:
            try:
                return attempt()
            except _Again as e:
                left -= 1
                wait = max(pause * (1 + jitter * self.rng.random()), e.wait)
                late = deadline is not None and self.clock() + wait >= deadline
                if not left or e.wait > self.policy.retry_after_max_sec or late or (e.applied and not replayable):
                    if e.wait:
                        e.refusal.with_facts(retry_after_sec=e.wait)
                    raise
                self.mark()
                self.sleep(wait)
                pause = min(pause * 2, cap)

    def _routes(self, stack: contextlib.ExitStack):
        """Клиент маршрута по прокси — один на прокси за время вызова, закрывается вместе со `stack`."""
        routes: dict[str, object] = {}

        def client(proxy: str):
            if proxy not in routes:
                routes[proxy] = stack.enter_context(self._client(proxy))
            return routes[proxy]
        return client

    @staticmethod
    def _usable(files: Files, path, owner: str, part: Path, size: int | None) -> tuple[int, str, str]:
        """Свой остаток, годный для докачки — (длина, валидатор, хост); без валидатора, с валидатором, который библиотеки
        запроса не понесут (`_carried`), или длиннее состава — сброшен."""
        note = (files.noted(path, owner) or {}) if part.exists() else {}
        validator = str(note.get("etag") or note.get("last_modified") or "")
        if part.exists() and (not validator or not Fetcher._carried("If-Range", validator)
                              or (size is not None and part.stat().st_size > size)):
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
            status = self._status(response, host)
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

    def _status(self, response, host: str, refuse=None, unprocessed: tuple[int, ...] = ()) -> int:
        """Код ответа, судимый одинаково у всех приёмников: повторимый по `retry_on` — `_Again` со сроком сервера как
        назван (потолки судит `_retry`), 4xx и 5xx — отказ, иначе код как есть. Код из `unprocessed` — запрос не применён
        (сервер его не обработал), прочий повторимый — мог примениться. Отказ переводит потребитель (`refuse(ответ)`),
        если знает причины своего источника, иначе — код политики."""
        status = response.status_code
        if status not in self.policy.retry_on and status < REFUSED_FROM:
            return status
        refusal = refuse(response) if refuse else self._failed(host, status)
        if status in self.policy.retry_on:
            raise self._again(response, refusal, applied=status not in unprocessed)
        raise refusal

    @staticmethod
    def _again(response, refusal: ContractError, *, applied: bool = True) -> _Again:
        """Повторимый ответ: отказ на случай исчерпания и срок, названный сервером, как назван (потолки судит `_retry`)."""
        return _Again(refusal, retry_after(response.headers.get("Retry-After"), math.inf, 0.0), applied=applied)

    @staticmethod
    def _append(files: Files, path, owner: str, part: Path) -> int:
        """Дескриптор дозаписи своего остатка, не проходя ссылку на его месте (FS-14): ссылку снимает `drop`, сама
        жертва цела, вызов — отказ; повтор начнёт с нуля."""
        try:
            if sys.platform == "win32":
                return _unfollowed(part)
            return os.open(part, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o666)
        except OSError as e:
            if e.errno != errno.ELOOP:
                raise
            files.drop(path, owner)
            raise PathEscapeError(files.name(part)) from e

    def _open(self, client, url: str, headers: dict | None, offset: int, validator: str, held: str, *,
              method: str = "GET", stop: str = "", deadline: float | None = None, payload: dict | None = None,
              cut=None):
        """Запрос с переходами — (ответ потоком, конечный хост). Каждый адрес судится до запроса.

        Напрямую — соединение на судимый IP (`Host` и SNI — исходное имя); через прокси среды — по имени, его разрешает
        прокси, а суд схемы и хоста тот же. Заголовки потребителя (ключ) уходят только исходному хосту; `Range` с
        `If-Range` — только хосту, чей валидатор лежит в заметке: валидатор одного сервера другому ничего не говорит.
        Заголовок, чьё значение библиотеки запроса не понесут (`_carried`), — отказ с его именем до разрешения имени.
        Ответ с заголовком `stop` возвращается, даже если это переход. При сроке обращения `deadline` срок фазы запроса
        урезан до половины остатка: соединение и ответ вместе в него укладываются; остатка нет — запрос не уходит.
        `payload` — тело запроса ключами `build_request` (`content`, `data`, `files`, `json`); `cut` — отказ обрыва ответа
        вызова (`_dropped`).
        """
        import httpx

        origin = httpx.URL(url).host
        for _ in range(self.policy.redirects + 1):
            target = httpx.URL(url)
            self._allowed(target)
            proxy = self._proxy(target)
            sent = dict(headers or {}) if target.host == origin else {}
            if offset and target.host == held:
                sent.update({"Range": f"bytes={offset}-", "If-Range": validator})
            # Файл — байты как пришли (NI-04): длина, предел и смещение Range — в одном представлении, без распаковки.
            sent.update({"Host": target.netloc.decode("ascii"), "Accept-Encoding": "identity"})
            unfit = next((name for name, value in sent.items() if not self._carried(name, value)), None)
            if unfit:
                # Значение может быть секретом: в отказе только имя заголовка.
                raise ContractError("REQUEST_HEADER_INVALID", facts={"host": target.host, "header": unfit})
            timeout = httpx.USE_CLIENT_DEFAULT if deadline is None else self._phase(target.host, deadline)
            route = client(proxy)
            if proxy:
                request = route.build_request(method, target, headers=sent, timeout=timeout, **(payload or {}))
            else:
                request = route.build_request(method, target.copy_with(host=self._judge(target)), headers=sent,
                                              timeout=timeout, extensions={"sni_hostname": target.host},
                                              **(payload or {}))
            try:
                self.mark()
                response = route.send(request, stream=True)
            except httpx.TransportError as e:
                raise self._dropped(target.host, e, cut) from e
            location = response.headers.get("location")
            if response.status_code not in REDIRECTS or not location or (stop and stop in response.headers):
                return response, target.host
            response.close()
            url = str(target.join(location))
        raise self._failed(httpx.URL(url).host, f"переходов больше {self.policy.redirects}")

    def _phase(self, host: str, deadline: float) -> float:
        """Срок одной фазы запроса при сроке обращения: не больше `timeout_sec` и половины остатка; остатка нет — `_Again`
        (повтор `_retry` уже не начнёт)."""
        left = deadline - self.clock()
        if left <= 0:
            raise _Again(self._late(host, "срок обращения исчерпан"), applied=False)
        return min(self.policy.timeout_sec, left / 2)

    def _proxy(self, target) -> str:
        """Прокси среды для адреса или пусто: схема и NO_PROXY — по правилам urllib, как у httpx с trust_env."""
        found = self.proxies()
        proxy = str(found.get(target.scheme) or found.get("all") or "")
        # есть в urllib.request 3.11–3.14 на любой ОС, вне typeshed и документации; NO_PROXY сторожит test_fetcher
        bypass = urllib.request.proxy_bypass_environment(target.host, found)  # type: ignore[attr-defined]
        return "" if proxy and bypass else proxy

    def _allowed(self, target) -> None:
        """Схема и хост — ровно из объявленных; `объявленный@чужой` — хост чужой, имя пользователя не в счёт."""
        schemes = [s.lower() for s in self.policy.schemes]
        hosts = [h.lower() for h in self.policy.hosts]
        if target.scheme not in schemes:
            raise ContractError("DOWNLOAD_FORBIDDEN", facts={"host": target.host, "allowed": ", ".join(schemes)})
        if hosts and target.host not in hosts:
            raise ContractError("DOWNLOAD_FORBIDDEN", facts={"host": target.host, "allowed": ", ".join(hosts)})
        if self.port is not None and (target.port or PORTS[target.scheme]) != self.port:
            raise ContractError("DOWNLOAD_FORBIDDEN", facts={"host": f"{target.host}:{target.port or PORTS[target.scheme]}",
                                                             "allowed": f"{target.host}:{self.port}"})

    def _judge(self, target) -> str:
        """IP для прямого соединения: все адреса имени — публичные, соединение — на первый, второго разрешения нет."""
        try:
            found = self.resolve(target.host, target.port or PORTS[target.scheme], type=socket.SOCK_STREAM)
        except OSError as e:
            raise self._dropped(target.host, e) from e
        addresses = list(dict.fromkeys(str(info[4][0]) for info in found))
        inner = next((a for a in addresses if inner_address(a)), None) if self.policy.block_private else None
        if inner or not addresses:
            raise ContractError("DOWNLOAD_FORBIDDEN", facts={"host": target.host, "address": inner or "—"})
        return addresses[0]

    def _client(self, proxy: str):
        """Клиент маршрута: напрямую — без прокси среды (соединение закреплено за судимым IP), иначе — через названный
        прокси. Подставной транспорт заменяет сеть целиком, маршрут через прокси тоже."""
        import httpx

        route = {"transport": self.transport} if self.transport is not None or not proxy else {"proxy": proxy}
        return httpx.Client(**route, timeout=self.policy.timeout_sec, follow_redirects=False, trust_env=False)

    @staticmethod
    def _validator(response) -> dict:
        """Валидатор для `If-Range`: сильный ETag, иначе Last-Modified; слабый ETag докачку не разрешает
        (RFC 9110 §13.1.5); значение, которое библиотеки запроса не понесут обратно (`_carried`), — не валидатор."""
        etag = str(response.headers.get("etag") or "")
        if etag and not etag.startswith("W/") and Fetcher._carried("If-Range", etag):
            return {"etag": etag}
        modified = str(response.headers.get("last-modified") or "")
        return {"last_modified": modified} if modified and Fetcher._carried("If-Range", modified) else {}

    @staticmethod
    def _length(response, offset: int) -> int | None:
        """Ожидаемая длина целого файла, когда состав её не назвал: смещение плюс Content-Length ответа."""
        told = str(response.headers.get("content-length") or "")
        return offset + int(told) if told.isdigit() else None

    def _failed(self, host: str, status) -> ContractError:
        return ContractError(self.policy.codes.failed, facts={"host": host, "status": status})

    @staticmethod
    def _short(host: str, got: int, expected: int | None) -> ContractError:
        return ContractError("DOWNLOAD_INCOMPLETE",
                             facts={"host": host, "got": got, "expected": "—" if expected is None else expected})

    def silence_sec(self) -> float:
        """Наибольшая тишина между отметками работы — свойство политики (`RequestPolicy.silence_sec`)."""
        return self.policy.silence_sec

    def take(self, response, files: Files, path, owner: str, *, host: str, size: int | None = None,
             offset: int = 0, deadline: float | None = None, cut=None) -> int:
        """Тело открытого ответа — в остаток владельца без своего буфера: принятое до обрыва лежит на диске (MF-04).
        Длина — из состава или заявленная ответом; больше состава или предела `max_bytes` политики — остаток снят и отказ
        кодом; обрыв или недобор — `_Again` с отказом `cut(хост, принято, ждали)` (по умолчанию «не дочитан» загрузки),
        мера работы и срок обращения `deadline` — те же, что у ответа в память (`_pour`). Сжатое тело вопреки `identity`
        — отказ до записи (`_identity`)."""
        self._identity(response, host)
        with os.fdopen(self._append(files, path, owner, files.remnant(path, owner)), "ab") as fh:
            got, over = self._pour(response, fh.write, host=host, size=size, offset=offset, cut=cut or self._short,
                                   deadline=deadline)
        if over:
            files.drop(path, owner)
            if over == "limit":
                raise self._too_large()
            raise ContractError(self._body().codes.corrupt,
                                facts={"part": files.name(files.place(path)), "expected": size, "got": got})
        return got

    def receive(self, response, files: Files, path, owner: str, *, accept, host: str = "",
                deadline: float | None = None, cut=None) -> Path:
        """Тело уже открытого ответа API в файл через остаток владельца (адрес судил вызывающий): тип — из объявленных до
        записи (NI-03), обрыв и срок обращения `deadline` — отказ без повтора и без остатка, POST не докачивается
        (NI-05); целое встаёт под имя. `host` — имя, к которому шёл запрос (соединение дверью идёт на судимый IP); `cut` —
        отказ обрыва от двери вызова: каким кодом звать обрыв, решает повторяемость вызова, а не сток."""
        self._body()
        host = host or response.url.host
        kind = str(response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if kind not in accept:
            raise self._failed(host, f"Content-Type {kind or '—'}")
        files.drop(path, owner)
        files.remnant(path, owner).parent.mkdir(parents=True, exist_ok=True)
        try:
            self.take(response, files, path, owner, host=host, deadline=deadline, cut=cut)
        except _Again as e:
            files.drop(path, owner)
            raise e.refusal from e
        return files.publish(path, owner)

    def length(self, url: str, headers: dict | None = None, *, linked: str = "") -> tuple[int | None, str]:
        """Длина файла по HEAD без тела — (байты или None, откуда длина или почему её нет).

        Переходы, суд адреса и повтор — те же, что у `fetch`; остановка — на ответе с заголовком `linked` (длина файла
        за переходом на CDN), иначе длина — `Content-Length` ответа, который не переход. Сеть не ответила или ответила
        повторимым и после всех попыток — (None, `Unreached`): хост не достигнут; ответил без длины — (None, причина).
        Чужой хост, внутренний адрес, отказ источника — отказ кодом.
        """
        with contextlib.ExitStack() as stack:
            client = self._routes(stack)
            try:
                return self._retry(lambda: self._head(client, url, headers, linked))
            except _Again as e:
                return None, Unreached(f"HEAD {e.refusal.facts['host']}: {e.refusal.facts['status']}")

    def _head(self, client, url: str, headers: dict | None, linked: str) -> tuple[int | None, str]:
        """Одна попытка HEAD — (байты или None, откуда длина или почему её нет); повторимое — `_Again`."""
        response, host = self._open(client, url, headers, 0, "", "", method="HEAD", stop=linked)
        response.close()
        status = self._status(response, host)
        named = linked if linked and linked in response.headers else "Content-Length"
        told = "" if named != linked and status in REDIRECTS else str(response.headers.get(named) or "")
        if not told.isdigit():
            return None, f"HEAD {host}: нет {' и '.join(filter(None, (linked, 'Content-Length')))}"
        return int(told), f"HEAD {host}: {named}"

    def status(self, url: str, headers: dict | None = None, *, within: float | None = None) -> tuple[int, dict]:
        """Код ответа и его заголовки без тела — (код, заголовки с именами в нижнем регистре). Та же дверь (NI-09): суд
        адреса до запроса, соединение на судимый IP, ключ — только исходному хосту, повтор и срок по политике
        (`deadline_sec`, срок вызывающего `within`). Тело не читается: поток закрыт после заголовков. Код — ответ, а не
        отказ: кто ответил — и есть улика; переход тоже возвращается как есть; повторяются только коды `retry_on`.
        Отказ — только у самой двери: адрес, сеть, срок."""
        deadline = self._deadline(self._addressed(), within)
        with contextlib.ExitStack() as stack:
            client = self._routes(stack)
            try:
                return self._retry(lambda: self._peek(client, url, headers, deadline), deadline)
            except _Again as e:
                raise e.refusal from e

    def _peek(self, client, url: str, headers: dict | None, deadline: float) -> tuple[int, dict]:
        """Одна попытка статуса — (код, заголовки); код из `retry_on` — `_Again` со сроком сервера."""
        response, host = self._open(client, url, headers, 0, "", "", stop="location", deadline=deadline)
        response.close()
        if response.status_code in self.policy.retry_on:
            raise self._again(response, self._failed(host, response.status_code))
        return response.status_code, dict(response.headers.items())

    def answer(self, url: str, headers: dict | None = None, *, refused=None, within: float | None = None) -> Any:
        """JSON ответа API в память — та же дверь, что у файла (NI-09): суд адреса до запроса и на каждом переходе,
        соединение на судимый IP, ключ — только исходному хосту, `identity`, повтор по политике. Тело — по ходу и не
        больше `max_bytes` (NI-06), разбор — из байтов и только целого (NI-07). Всё обращение с повторами укладывается в
        срок: `deadline_sec` политики, а если вызывающий передал свой остаток `within` (секунды) — меньший из двух.
        Отказ источника (4xx, 5xx) переводит `refused(ответ с телом, прочитанным в пределе)`, если потребитель знает
        причины своего источника; иначе — код политики. Отказы самой двери (адрес, предел, обрыв, срок) идут наверх как
        есть. Политика не AnswerPolicy или без хоста — ошибка кода: ответ в память — только с объявленного хоста."""
        deadline = self._deadline(self._memory(), within)
        with contextlib.ExitStack() as stack:
            client = self._routes(stack)
            try:
                body, host = self._retry(lambda: self._recall(client, url, headers, refused, deadline), deadline)
            except _Again as e:
                raise e.refusal from e
        return self._parsed(body, host)

    def _recall(self, client, url: str, headers: dict | None, refused, deadline: float, *, method: str = "GET",
                payload: dict | None = None, sink=None, unprocessed: tuple[int, ...] = (), cut=None) -> tuple[Any, str]:
        """Одна попытка ответа в память — (тело, конечный хост), со стоком — (что вернул `sink(ответ, хост, срок, cut)`,
        хост); повторимое — `_Again`, отказ источника — `_status`, обрыв — отказ `cut` (по умолчанию `_cut`)."""
        import httpx

        response, host = self._open(client, url, headers, 0, "", "", method=method, deadline=deadline, payload=payload,
                                    cut=cut)
        try:
            self._status(response, host, refused and (lambda r: refused(httpx.Response(
                r.status_code, headers=r.headers, content=self._kept(r, host, deadline, cut), request=r.request))),
                unprocessed)
            if sink:
                return sink(response, host, deadline, cut), host
            return self._kept(response, host, deadline, cut), host
        finally:
            response.close()

    def _kept(self, response, host: str, deadline: float, cut=None) -> bytes:
        """Тело ответа в память: байты как пришли, не больше `max_bytes` и до срока обращения; обрыв — `_Again` с отказом
        обрыва (`cut`, по умолчанию `_cut`): остатка для докачки у ответа нет."""
        self._identity(response, host)
        body = bytearray()
        _, over = self._pour(response, body.extend, host=host, size=None, offset=0, deadline=deadline,
                             cut=cut or self._cut)
        if over:
            raise self._too_large()
        return bytes(body)

    def _identity(self, response, host: str) -> None:
        """Тело — байты как пришли (NI-04): сжатое вопреки `identity` — отказ до приёма, распаковка сломала бы длину,
        предел и отпечаток."""
        coding = str(response.headers.get("content-encoding") or "identity").strip().lower()
        if coding != "identity":
            raise self._failed(host, f"Content-Encoding {coding}")

    def _pour(self, response, write, *, host: str, size: int | None, offset: int, cut,
              deadline: float | None = None) -> tuple[int, str]:
        """Тело открытого ответа в сток `write` по ходу — (принятая длина, перебор): «size» — больше состава, «limit» —
        больше `max_bytes` политики, пусто — нет; кусок сверх в сток не идёт. Приёмники двери различаются только стоком.

        Мера работы (NI-01): кусок — признак работы, если с начала окна принято не меньше предела × прошедшее; окно
        закрылось ниже предела — `_Again` с `DOWNLOAD_TOO_SLOW`, попытка потрачена; предел 0 — признак на каждом куске.
        Обрыв или недобор до длины из состава или заявленной ответом — `_Again` с отказом `cut(хост, принято, ждали)`.
        Кусок, пришедший после срока обращения `deadline`, не принимается — `_Again` с кодом политики.
        """
        import httpx

        expected = size if size is not None else self._length(response, offset)
        body = self._body()
        limit, floor, window = body.max_bytes, body.speed_floor_bytes_per_sec, body.speed_window_sec
        began, in_window = self.clock(), 0
        try:
            for chunk in response.iter_bytes():
                if deadline is not None and self.clock() >= deadline:
                    raise _Again(self._late(host, f"срок обращения истёк на {offset} байт"))
                offset += len(chunk)
                if size is not None and offset > size:
                    return offset, "size"
                if limit and offset > limit:
                    return offset, "limit"
                write(chunk)
                in_window += len(chunk)
                elapsed = self.clock() - began
                if in_window >= floor * elapsed:
                    self.mark()
                if elapsed >= window:
                    if in_window < floor * window:
                        raise _Again(ContractError("DOWNLOAD_TOO_SLOW", facts={
                            "host": host, "got": in_window, "window_sec": window, "floor": floor}))
                    began, in_window = self.clock(), 0
        except httpx.TransportError as e:
            raise _Again(cut(host, offset, expected)) from e
        if expected is not None and offset < expected:
            raise _Again(cut(host, offset, expected))
        return offset, ""

    def _too_large(self) -> ContractError:
        """Перебор предела — с путём ключа предела в декларации (`slot`): рецепт реестра называет, что поднять."""
        key = f"config/{self.slot.file} → {'.'.join((*self.slot.section, 'max_bytes'))}" if self.slot else "max_bytes"
        return ContractError("DOWNLOAD_TOO_LARGE", facts={"limit_mb": round(self._body().max_bytes / 2**20, 3),
                                                          "key": key})

    def call(self, url: str, headers: dict | None = None, *, method: str, payload: dict | None = None, sink=None,
             refused=None, within: float | None = None) -> Any:
        """Вызов API методом с телом (`payload` — ключи `build_request`) — та же дверь (NI-09): суд адреса до запроса,
        соединение на судимый IP, ключ — только исходному хосту, `identity`, срок обращения (`deadline_sec`, срок
        вызывающего `within` его только сокращает). Ответ — JSON в память в пределе `max_bytes`, а со стоком — то, что
        вернул `sink(ответ, хост, срок)` на открытом потоке после суда кода.

        Повтор (NI-05, RFC 9110 §9.2.2): повторяемый вызов — метод из `idempotent_methods` или с ключом `idempotency_header`
        (один на вызов, тот же на каждой попытке) — как у прочих приёмников; неповторяемый — только если запрос не
        применён: не ушёл (имя, соединение, срок до отправки) или ответ из `retry_on ∩ unprocessed`. Отказ источника
        переводит `refused`; отказы двери — кодами политики (`CallCodes`), срок сервера — в фактах (`retry_after_sec`)."""
        policy = self._calling()
        deadline = self._deadline(policy, within)
        sent = dict(headers or {})
        replayable = method.upper() in {m.upper() for m in policy.idempotent_methods}
        if policy.idempotency_header is not None:
            sent[policy.idempotency_header] = secrets.token_urlsafe()
            replayable = True
        cut = functools.partial(self._cut, replayable=replayable)
        with contextlib.ExitStack() as stack:
            client = self._routes(stack)
            try:
                body, host = self._retry(lambda: self._recall(
                    client, url, sent, refused, deadline, method=method, payload=payload, sink=sink,
                    unprocessed=policy.unprocessed, cut=cut), deadline, replayable=replayable)
            except _Again as e:
                raise e.refusal from e
        return body if sink else self._parsed(body, host)

    def _calling(self) -> CallPolicy:
        """Политика вызова с хостом объявленного адреса (читатель `answers`); иная — ошибка кода вызывающего, до запроса."""
        if not (isinstance(self.policy, CallPolicy) and self.policy.hosts):
            raise TypeError("вызов требует CallPolicy с хостом объявленного адреса (читатель answers), "
                            f"а загрузчик построен с {type(self.policy).__name__}")
        return self.policy

    def _parsed(self, body: bytes, host: str) -> Any:
        """JSON из байтов целого ответа (NI-07): кодировку узнаёт разбор, а не угадывание; не JSON — отказ кодом политики."""
        try:
            return json.loads(body)
        except ValueError as e:
            raise self._failed(host, "ответ не JSON") from e

    def _dropped(self, host: str, error: Exception, cut=None) -> _Again:
        """Сбой сети на пути запроса — повторимое `_Again`: запрос, который не ушёл (имя, соединение, его срок), не применён,
        прочее — мог дойти. Код — политики: у вызова (`CallCodes`) не ушедший — `unreached`, истёкшая фаза — `timeout`,
        оборванный ответ — отказ обрыва вызова (`cut`, по умолчанию `_cut`); у прочих приёмников — `failed` с именем сбоя."""
        import httpx

        unsent = isinstance(error, (OSError, httpx.ConnectError, httpx.ConnectTimeout))
        codes, status = self.policy.codes, type(error).__name__
        if not isinstance(codes, CallCodes):
            refusal = self._failed(host, status)
        elif isinstance(error, httpx.TimeoutException):
            refusal = ContractError(codes.timeout, facts={"host": host, "status": status})
        elif unsent:
            refusal = ContractError(codes.unreached, facts={"host": host, "status": status})
        elif isinstance(error, (httpx.ReadError, httpx.RemoteProtocolError)):
            refusal = (cut or self._cut)(host, 0, None)
        else:
            refusal = self._failed(host, status)
        return _Again(refusal, applied=not unsent)

    def _cut(self, host: str, got: int, expected: int | None, replayable: bool = True) -> ContractError:
        """Ответ оборван после отправки: у вызова (`CallCodes`) код по повторяемости — повторяемый «не дочитан»
        (`corrupt`), неповторяемый — исход неизвестен (`outcome_unknown`: задача могла создаться и списаться), с принятым
        и заявленным; у прочих приёмников — `failed`: остатка для докачки у ответа нет."""
        codes = self.policy.codes
        if isinstance(codes, CallCodes):
            return ContractError(codes.corrupt if replayable else codes.outcome_unknown,
                                 facts={"host": host, "got": got, "expected": "—" if expected is None else expected})
        return self._failed(host, f"ответ оборван на {got} байт")

    def _late(self, host: str, status: str) -> ContractError:
        """Срок обращения истёк: у вызова (`CallCodes`) — код `timeout`, у прочих приёмников — `failed`."""
        codes = self.policy.codes
        return ContractError(codes.timeout if isinstance(codes, CallCodes) else codes.failed,
                             facts={"host": host, "status": status})

    @staticmethod
    def _carried(name: str, value: str) -> bool:
        """Понесут ли значение заголовка библиотеки запроса: httpx его кодирует, h11 принимает (CR, LF, NUL — нет)."""
        import h11
        import httpx

        try:
            # HTTP/1.0 не требует Host: в запросе — только судимый заголовок, и сам Host судится без двойника.
            h11.Request(method="GET", target="/", headers=httpx.Headers({name: value}).raw, http_version="1.0")
        except (UnicodeEncodeError, h11.LocalProtocolError):
            return False
        return True


class Unreached(str):
    """Причина «длина не узнана» от `Fetcher.length`, когда дверь исчерпала повторы — хост не достигнут."""


def results(config_file: str | Path, **seams) -> Fetcher[ResultFetchPolicy]:
    """Загрузчик результатов поставщиков по `download.fetch` декларации задач; ссылки на файл (`url_fields`) — там же."""
    decl = Declaration(config_file, TaskCycleError, "ожидания")
    return Fetcher(decl.section(*RESULT_FETCH.section, schema=RESULT_FETCH.schema), slot=RESULT_FETCH, **seams)


def answers(decl: Declaration, slot: Slot[A], url: str, **seams) -> Fetcher[A]:
    """Загрузчик обращения к объявленному адресу (ответ в память, статус, вызов) по разделу `slot` декларации `decl` —
    судья адреса `addressed`."""
    return addressed(decl.section(*slot.section, schema=slot.schema), slot, url, **seams)


def addressed(policy: A, slot: Slot[A], url: str, **seams) -> Fetcher[A]:
    """Загрузчик обращения к объявленному адресу по политике `policy` раздела `slot` (вызывающий мог её перекрыть, суд
    схемы — его): хост — хост объявленного адреса `url` (`ProviderUrl` — хост без подстановки `{…}`), списка хостов рядом
    с адресом нет. Внутреннюю сеть (`block_private: false`) и открытый канал (схема не https) политика открывает только
    литералу петли — там сеть не пересекается, и ровно объявленному порту: на петле слушает кто угодно; иначе отказ до
    запроса."""
    host = declared_host(url)
    opened = [*(["block_private: false"] if not policy.block_private else []),
              *(s for s in policy.schemes if s.lower() != "https")]
    if opened and not _loopback(host):
        raise ContractError("DOWNLOAD_FORBIDDEN", facts={"host": host, "allowed": f"литерал петли ({', '.join(opened)})"})
    parts = urllib.parse.urlsplit(url)
    port = (parts.port or PORTS.get(parts.scheme.lower())) if opened else None
    return Fetcher(policy.model_copy(update={"hosts": (host,)}), slot=slot, port=port, **seams)
