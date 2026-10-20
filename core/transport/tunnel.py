"""core/transport/tunnel.py — туннель к Claude AI Web через Cloudflare.

Даёт облачному клиенту публичный HTTPS-доступ к локальному серверу и поднимается ВМЕСТЕ с ним
дочерним процессом; ядро о туннеле не знает. Эфемерный режим меняет URL при каждом запуске, и
настроенный коннектор после перезапуска смотрит в никуда — постоянный hostname даёт только
именованный. Поток keepalive поднимает упавший процесс заново; секреты туннеля вне git.
"""

import logging
import os
import random
import re
import shutil
import socket
import subprocess
import threading
import time
from itertools import islice
from pathlib import Path
from typing import Annotated, ClassVar, Literal, assert_never
from urllib.parse import urlsplit

from pydantic import AfterValidator, BeforeValidator
from pydantic_core import PydanticCustomError

from core.contracts import ContractError
from core.declaration import Declaration
from core.declaration_schemas import TUNNEL_METRICS, TUNNEL_PROBE, TUNNEL_READINESS, TUNNEL_SUPERVISION
from core.engine.engine import incident
from core.environment import child_spawn
from core.jobs import backoff
from core.providers.download import answers
from core.transport.diagnostics import каталог_проверок, путь_бинаря

log = logging.getLogger(__name__)


# Словарь событий лога cloudflared объявлен ОДИН раз: и производитель (`_classify_line`), и оба
# потребителя судятся по нему. Вид, который потребитель забыл разобрать, называет mypy в гейте
# (`assert_never`), а не тишина в рантайме.
LineKind = Literal["url", "connected", "disconnected", "fatal", "error"]

# Публичный URL quick-туннеля cloudflared печатает в свой поток вывода.
_TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# Событие установленного соединения — ГЛАВНЫЙ триггер готовности (не время).
# lookbehind (?<!un) — чтобы "Unregistered..." НЕ считалось соединением.
_CONNECTED_RE = re.compile(r"(?<!un)registered tunnel connection|(?<!un)registered connindex", re.I)

# Событие потери соединения (транзиентное — cloudflared обычно сам восстановит).
_DISCONNECTED_RE = re.compile(r"unregistered tunnel connection|lost connection with the edge|connection .*(lost|closed|terminated)", re.I)

# Непоправимые причины отказа cloudflared — сразу отдаём как адекватную ошибку.
_FATAL_MARKERS = (
    "token is invalid",
    "provided tunnel token is invalid",
    "token is not valid",
    "failed to parse",
    "couldn't start tunnel",
    "not authorized",
    "unauthorized",
    "you need to login",
    "cannot determine default origin certificate",
    "error parsing tunnel",
    "invalid tunnel credentials",
    "tunnel credentials file",
    "no such tunnel",
)

_INSTALL_HINT = (
    "Бинарь туннеля не найден ни по пути каталога (config/tunnel_diagnostics.yaml, `бинари`), ни в PATH. "
    "Поставь его: ./install.sh"
)

# Имена среды, по которым cloudflared берёт личность туннеля (`tunnel run --help`): каждое главнее объявленного файла
# учётных данных, поэтому из наследства сервера ребёнку они не идут; токен ставится только своим именем.
_IDENTITY_ENV = ("TUNNEL_TOKEN", "TUNNEL_TOKEN_FILE", "TUNNEL_CRED_FILE", "TUNNEL_CRED_CONTENTS")
_TOKEN_ENV = "TUNNEL_TOKEN"


class TunnelError(RuntimeError):
    """Отказ туннеля с сохранением реального текста ошибки cloudflared."""


class TunnelCancelled(TunnelError):
    """Туннель остановлен ИЗВНЕ, а не сломался: cloudflared вышел кодом 0.

    Ctrl+C в терминале уходит всей группе процессов, поэтому дочерний cloudflared завершается
    штатно (`code=0`, `context canceled`) РАНЬШЕ, чем сервер успевает обработать сигнал. Без
    отдельного рода отмена читалась как отказ туннеля, и консоль объявляла «Статус: ГОТОВ»
    после того, как оператор уже остановил сервер.
    """


class CloudflaredTunnel:
    """Управление туннелем cloudflared: запуск, ожидание URL, keepalive, стоп.

    Режима два: `quick` (эфемерный URL) и `named` (постоянный `hostname`).
    """

    def __init__(self, port: int = 8080, config_path: str | Path | None = None):
        cfg = self._load_config(config_path)

        self.port = int(cfg.get("local_port", port) or port)
        self.mode = cfg.get("mode", "quick")
        self.hostname = declared_hostname(config_path)
        # named-режим подключается либо connector-токеном, либо локальными credentials; токен — только из среды
        # сервера: в файле декларации его читает любой локальный пользователь, и там он — отказ двери (`_load_config`).
        self.tunnel_token = os.environ.get("MCP_TUNNEL_TOKEN") or ""
        self.tunnel_id = cfg.get("tunnel_id") or ""
        self.tunnel_name = cfg.get("tunnel_name") or ""
        self.credentials_file = cfg.get("credentials_file") or ""

        # Бинарь — путь каталога (`бинари.cloudflared.в_дереве`, одно решение с диагностикой), затем PATH.
        self.binary = путь_бинаря(каталог_проверок().data, "cloudflared") or "cloudflared"

        # Надзор — политика сервера из закоммиченного каталога (`надзор`), а не из машинного файла: умолчаний в коде нет.
        надзор = каталог_проверок().section(*TUNNEL_SUPERVISION.section, schema=TUNNEL_SUPERVISION.schema)
        self.retry_base = надзор.retry_base_seconds         # старт паузы перезапуска
        self.retry_max = надзор.retry_max_seconds           # потолок паузы
        self.retry_reset = надзор.retry_reset_seconds       # «стабильный» прогон
        self.jitter = надзор.jitter                         # доля добавки к паузе
        self.ready_timeout = надзор.ready_timeout_seconds   # предел одной попытки первого подъёма
        self.first_attempts = надзор.first_attempts         # попыток первого подъёма
        self.stop_grace = надзор.stop_grace_seconds         # ждём конца соединителя после terminate, затем kill
        # Готовность — событием поставщика (сервер метрик соединителя), описание — данными каталога, а не кодом.
        self.readiness = каталог_проверок().section(*TUNNEL_READINESS.section, schema=TUNNEL_READINESS.schema)
        # Шов генератора добавки: худшее время перезапуска воспроизводимо; часы — шов класса `clock` (монотонные).
        self.rng = random.Random()

        self._proc: subprocess.Popen | None = None
        self._ended: threading.Event | None = None   # конец вывода процесса — событие его читателя (`_drain`)
        self._fatal_line: str | None = None          # непоправимый отказ из вывода соединителя
        self._public_url: str | None = None
        self._stopping = False
        self._stopped = threading.Event()   # остановка как событие: пауза перезапуска прерывается им, а не опросом
        self._supervisor: threading.Thread | None = None

        # Статус здоровья (обновляется супервизором, читается через status()).
        self._status_lock = threading.Lock()
        self._connected = False        # есть ли живое edge-соединение
        self._connections = 0          # сколько connIndex зарегистрировано
        self._attempts = 0             # подряд неудачных перезапусков процесса
        self._last_error: str | None = None
        self._supervision: dict = {"state": "идёт"}   # остановлен — с причиной и номером события в журнале
        self._proc_started: float | None = None
        # Кто убил процесс, знает только тот, кто убил: код -9 сам по себе неотличим от чужого
        # `kill`, и вердикт «завершился code=-9» ничего не объясняет тому, кто его читает.
        self._killed_by_watchdog = False

    @staticmethod
    def _load_config(config_path: str | Path | None) -> dict:
        """Путь НЕ задан — режим без объявления (quick), это решение вызывающего. Путь задан, а
        файла нет — промах: молча поднятый на дефолтах туннель не соответствует ничему. Непустой `tunnel_token` —
        отказ двери с путём ключа и без значения: токен живёт в среде сервера."""
        if not config_path:
            return {}
        дверь = Declaration(config_path, ContractError, "туннеля")
        if "tunnel_token" in дверь.data:
            дверь.need("tunnel_token", kind=_БЕЗ_СЕКРЕТА)
        for ключ in sorted(set(дверь.data) & set(TUNNEL_SUPERVISION.schema.model_fields)):
            дверь.need(ключ, kind=_ПЕРЕЕХАЛ)
        return дверь.data

    def _binary_available(self) -> bool:
        return self.binary.startswith("/") and Path(self.binary).exists() \
            or shutil.which(self.binary) is not None

    def _build_command(self) -> list[str]:
        """Собирает команду cloudflared под выбранный режим.

        named — два пути (по требованиям Cloudflare):
          • token: `cloudflared tunnel run` — дашбордовый туннель; токен уходит средой ребёнка
            (`_child_spawn`), а не аргументом: argv процесса читает любой локальный пользователь;
            ingress (домен → 127.0.0.1:port) настроен на стороне Cloudflare.
          • credentials: `cloudflared tunnel run --url <url> <id|name>` c
            --credentials-file — локально управляемый туннель, DNS-маршрут привязан
            к домену командой `cloudflared tunnel route dns <name> <hostname>`.

        Токен — только из среды сервера (MCP_TUNNEL_TOKEN); yaml — только не-секретные поля.
        """
        url = f"http://127.0.0.1:{self.port}"
        # Сервер метрик — на адресе, который назначили мы: по нему спрашивается готовность (`_ready`).
        head = [self.binary, "tunnel", self.readiness.metrics_flag, self.readiness.metrics_address, "--no-autoupdate"]
        if self.mode == "named":
            # Путь 1: connector-токен (ID+секрет внутри токена).
            if self._by_token():
                return [*head, "run"]
            # Путь 2: локальные credentials + UUID/имя туннеля.
            ref = self.tunnel_id or self.tunnel_name
            if not ref:
                raise ValueError(
                    "mode=named требует токен в среде сервера (MCP_TUNNEL_TOKEN) ЛИБО tunnel_id/tunnel_name "
                    "(+ credentials_file) в config/tunnel.yaml. Получи их в дашборде Cloudflare после привязки домена."
                )
            cmd = list(head)
            if self.credentials_file:
                cmd += ["--credentials-file", self.credentials_file]
            cmd += ["run", "--url", url, ref]
            return cmd
        # quick (по умолчанию): эфемерный trycloudflare-URL, без аккаунта/домена/токена.
        return [*head, "--url", url]

    def _named_url(self) -> str:
        """Публичный URL named-туннеля (постоянный домен)."""
        return (
            f"https://{self.hostname}" if self.hostname
            else "https://<домен, настроенный в дашборде Cloudflare>"
        )

    @staticmethod
    def _classify_line(line: str) -> tuple[LineKind | None, str | None]:
        """Классификация строки лога cloudflared.

        Возвращает ('url', <url>) | ('connected', None) | ('fatal', <текст>)
        | ('error', <текст>) | (None, None). 'error' — мягкий (транзиентный
        ретрай), запоминаем как контекст; 'fatal' — сразу отказ.
        """
        text = line.strip()
        low = text.lower()

        m = _TRYCLOUDFLARE_RE.search(text)
        if m:
            return ("url", m.group(0))
        # disconnected проверяем ПЕРЕД connected: "Unregistered..." содержит
        # подстроку "registered..." и иначе ложно матчился бы как connected.
        if _DISCONNECTED_RE.search(text):
            return ("disconnected", None)
        if _CONNECTED_RE.search(text):
            return ("connected", None)
        if " ftl " in f" {low} " or any(k in low for k in _FATAL_MARKERS):
            return ("fatal", text)
        if " err " in f" {low} ":
            return ("error", text)
        return (None, None)

    def _await_ready(self, lines, poll_fn, deadline: float | None = None) -> str:
        """Готовность — событием поставщика, а не строкой журнала и не таймером.

        Вывод соединителя читает его единственный читатель (`_drain`, свой поток): дренаж, текст ошибок, непоправимый
        отказ, конец вывода. Готов — сервер метрик называет соединения с краем (`_ready`), у quick есть и адрес
        (`_quick_url`); пока нет — следующий вопрос через объявленную паузу (`готовность.pause_sec`), её прерывает конец
        вывода. Отказ = непоправимая строка, конец вывода (процесс вышел) или срок — TunnelError с текстом соединителя.

        Args:
            lines: итератор строк (stdout процесса или фейковые данные в тесте).
            poll_fn: функция → код возврата процесса или None (жив ли он).
        """
        # Срок ждут ВСЕГДА: у прежнего ожидания предела не было вовсе, и туннель, которому режут
        # путь до края (VPN, firewall), держал подъём молча — вывод обрывался, вердикта не было.
        предел = deadline if deadline is not None else time.monotonic() + self.ready_timeout
        with self._status_lock:
            self._last_error = self._fatal_line = None
        ended = self._ended = threading.Event()
        threading.Thread(target=self._drain, args=(lines, ended), daemon=True, name="tunnel-drain").start()
        while True:
            if self._fatal_line:
                raise TunnelError(f"cloudflared отказал: {self._fatal_line}")
            if self._ready()["готов"]:
                адрес = self._named_url() if self.mode == "named" else self._quick_url()
                if адрес:
                    self._public_url = адрес
                    return адрес
            if ended.is_set():
                break
            if time.monotonic() > предел:
                raise TunnelError(
                    f"соединение не установлено за {self.ready_timeout:.0f} с"
                    + (f" (последнее от cloudflared: {self._last_error})" if self._last_error else "")
                    + ". Путь до края Cloudflare (порт 7844) закрыт или идёт через сеть, которая "
                      "его режет — проверь VPN/файрвол")
            ended.wait(self.readiness.pause_sec)
        if self._fatal_line:
            raise TunnelError(f"cloudflared отказал: {self._fatal_line}")
        last_err = self._last_error

        # Вывод кончился → процесс завершился, соединение не установлено.
        code = poll_fn()
        if code == 0:
            # УСПЕШНЫЙ код — не отказ ни при каком тексте лога: cloudflared так выходит по сигналу,
            # который получил вместе с нами (Ctrl+C уходит всей группе процессов).
            raise TunnelCancelled(
                "cloudflared остановлен извне (code=0)"
                + (f": {last_err}" if last_err else "") )
        if self._killed_by_watchdog or time.monotonic() > предел:
            почему = ("cloudflared молчал и остановлен по сроку" if self._killed_by_watchdog
                      else f"cloudflared завершился code={code}")
            raise TunnelError(
                f"соединение не установлено за {self.ready_timeout:.0f} с: {почему}"
                + (f"; последнее от него: {last_err}" if last_err else "")
                + ". Путь до края Cloudflare (порт 7844) закрыт или идёт через сеть, которая его "
                  "режет — проверь VPN/файрвол")
        if last_err:
            raise TunnelError(f"cloudflared завершился (code={code}) без соединения: {last_err}")
        raise TunnelError(f"cloudflared завершился (code={code}) без установки соединения")

    def start(self) -> str:
        """Запускает туннель и возвращает публичный URL.

        Готовность определяется СОСТОЯНИЕМ СОЕДИНЕНИЯ (событие cloudflared
        «Registered tunnel connection»), а НЕ таймером. При отказе (неверный/
        отсутствующий токен, нет авторизации, нет домена) возвращаем реальный
        текст ошибки cloudflared через TunnelError.

        Raises:
            RuntimeError: бинаря cloudflared нет.
            TunnelError: соединение не установлено (с текстом ошибки cloudflared).
        """
        if not self._binary_available():
            raise RuntimeError(_INSTALL_HINT)

        последняя: TunnelError | None = None
        for попытка in range(1, max(1, self.first_attempts) + 1):
            if self._stopping:
                raise self._cancelled()
            try:
                url = self._one_attempt()
                break
            except TunnelCancelled:
                # Отмена не повторяется: нас останавливают, а не мы не смогли подняться.
                self.stop()
                raise
            except TunnelError as beda:
                self._end_attempt()
                if self._stopping:              # попытку оборвала остановка, а не край: повтора нет
                    raise self._cancelled() from beda
                последняя = beda
                if попытка >= max(1, self.first_attempts) or self._fatal(beda):
                    raise
                пауза = self._backoff_delay(попытка)
                print(f"⏳ Туннель не поднялся ({beda}); повтор {попытка + 1} из "
                      f"{self.first_attempts} через {пауза:.0f} с")
                self._sleep_interruptible(пауза)
        else:  # pragma: no cover — цикл всегда выходит через break или raise
            raise последняя or TunnelError("туннель не поднят")
        with self._status_lock:
            self._connected = True
            self._connections = max(self._connections, 1)
        self._start_supervisor()
        return url

    @staticmethod
    def _fatal(beda: TunnelError) -> bool:
        """Непоправимое повтором: те же маркеры, по которым судит разбор строк.

        Список один на оба читателя — второй его копией разошёлся бы молча, и повтор молотил бы
        по неверному токену до конца попыток.
        """
        текст = str(beda).lower()
        return any(marker in текст for marker in _FATAL_MARKERS)

    def _one_attempt(self) -> str:
        """Один подъём под сторожевым таймером.

        Таймер нужен рядом с проверкой срока внутри `_await_ready`: та ловит «строки идут, а
        соединения нет», а этот — «строк нет вовсе», когда чтение потока блокируется намертво.
        Без него молчащий cloudflared держал бы подъём столько, сколько живёт терминал.
        """
        self._proc = self._spawn()
        if self._stopping:                      # stop() пришёл между проверкой флага и порождением: процесс видит он или мы
            self._end_attempt()
            raise self._cancelled()
        assert self._proc.stdout is not None
        self._killed_by_watchdog = False
        сторож = threading.Timer(self.ready_timeout + 2.0, self._kill_silent)
        сторож.daemon = True
        сторож.start()
        try:
            return self._await_ready(self._proc.stdout, self._proc.poll)
        finally:
            сторож.cancel()

    def _kill_silent(self) -> None:
        """Убить процесс, который не сказал ничего: чтение потока разблокируется закрытием."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._killed_by_watchdog = True
            proc.kill()

    def _spawn(self) -> "subprocess.Popen":
        """Запуск процесса cloudflared (с фиксацией времени старта); адрес его сервера метрик до запуска свободен."""
        self._metrics_address_free()
        self._proc_started = self.clock()
        return subprocess.Popen(
            self._build_command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            **self._child_spawn(),
        )

    # ── Backoff ─────────────────────────────────────────────────────────────

    def _next_attempts(self, prev: int, uptime: float) -> int:
        """Счётчик подряд-неудач: стабильный прогон (uptime≥reset) обнуляет разгон.

        Так «долго живший туннель, который умер один раз» перезапускается быстро,
        а «падающий по кругу» — с растущей паузой (защита от лимитов Cloudflare).
        """
        return 1 if uptime >= self.retry_reset else prev + 1

    def _backoff_delay(self, attempts: int) -> float:
        """Пауза `attempts`-го перезапуска — узлом паузы сервера (`core.jobs.backoff`): вдвое до потолка, добавка доли
        `jitter` от шва `rng` (WC-03)."""
        return next(islice(backoff(self.retry_base, self.retry_max, self.jitter, self.rng), max(0, attempts - 1), None))

    def _sleep_interruptible(self, delay: float):
        """Пауза, прерываемая stop() событием (WC-10): не держим shutdown на всю паузу и не опрашиваем флаг."""
        self._stopped.wait(delay)

    # ── Супервизор ──────────────────────────────────────────────────────────

    def _apply_line(self, raw: str):
        """Строка вывода соединителя — текст для человека: ошибка — в статус, непоправимый отказ — ещё и признак
        `_fatal_line`. Соединение, его потеря и адрес quick решает сервер метрик (`_ready`, `_quick_url`), а не строка."""
        kind, val = self._classify_line(raw)
        with self._status_lock:
            match kind:
                case "error":
                    self._last_error = val
                case "fatal":
                    self._last_error = self._fatal_line = val
                case "connected" | "disconnected" | "url" | None:
                    pass
                case _:
                    assert_never(kind)

    def _start_supervisor(self):
        """Фоновый цикл: ждёт конца процесса соединителя — события его читателя (`_drain`, он же дренаж: без чтения буфер
        трубы переполнится и соединитель встанет на записи) — и перезапускает его с паузой вдвое. Соединение и его
        обрывы видит сервер метрик (`status`); ради них процесс не перезапускается — обрывы лечит сам соединитель, держащий
        пул соединений с краем, — рестарт только при смерти процесса, без лишних подключений и лимитов края. Умерший
        процесс подобран и его труба закрыта до перезапуска (`_release`).
        """
        def _run():
            try:
                while not self._stopping:
                    proc, ended = self._proc, self._ended
                    if proc is None or ended is None:
                        break
                    ended.wait()                        # вывод кончился → процесс соединителя завершился
                    if self._stopping or self._supervision.get("state") != "идёт":
                        break                           # остановка или читатель вывода остановил надзор сбоем
                    self._release(proc)
                    uptime = (self.clock() - self._proc_started) if self._proc_started else 0.0
                    with self._status_lock:
                        self._connected = False
                        self._connections = 0
                        self._attempts = self._next_attempts(self._attempts, uptime)
                        attempts = self._attempts

                    # Пауза перед повторным запуском (backoff+jitter) — прерываемая.
                    self._sleep_interruptible(self._backoff_delay(attempts))
                    if self._stopping:
                        break
                    try:
                        fresh = self._spawn()
                    except Exception as e:
                        self._halted("перезапуск не удался", e)
                        return
                    self._ended, self._proc = threading.Event(), fresh
                    threading.Thread(target=self._drain, args=(fresh.stdout, self._ended), daemon=True,
                                     name="tunnel-drain").start()
                    if self._stopping:                  # остановка пришла, пока процесс порождался: гасим его здесь
                        self._end_attempt()
            except Exception as e:              # прочий сбой потока надзора — тем же узлом, а не трассой в stderr
                self._fell(e)

        self._supervisor = threading.Thread(target=_run, daemon=True)
        self._supervisor.start()

    def _fell(self, exc: BaseException) -> None:
        """Прочий сбой потока надзора (цикл перезапуска или читатель вывода) — одной причиной `_halted`."""
        self._halted("поток надзора упал", exc)

    def _halted(self, причина: str, exc: BaseException) -> None:
        """Надзор кончился сбоем: причина с трассировкой — в журнал сервера под номером события, состояние надзора —
        «остановлен» с причиной и номером: сам туннель больше не поднимается, и `status()` это называет."""
        event = incident(log, f"надзор туннеля: {причина}", exc)
        with self._status_lock:
            self._supervision = {"state": "остановлен", "reason": причина, "event": event}

    @staticmethod
    def _край_ответил(код: int, сервер: str) -> bool:
        """Кто автор ответа — край Cloudflare или наш процесс.

        Судится ЗАГОЛОВКОМ: он называет автора прямо, а код — нет (405 на GET законен у нас, 530
        бывает только у края). Заголовка нет — тогда по коду: 5xx снаружи означает, что до нас
        запрос не дошёл, наш транспорт таких не отдаёт на пустой GET.
        """
        return "cloudflare" in сервер.lower() or (not сервер and код >= 500)

    # Сеть, часы и сон прозвонки (MF-16): None — настоящий транспорт; подмена — на классе, как у адаптеров.
    transport: ClassVar = None
    resolve: ClassVar = staticmethod(socket.getaddrinfo)
    clock: ClassVar = staticmethod(time.monotonic)
    sleep: ClassVar = staticmethod(time.sleep)

    def probe(self, timeout: float = 8.0) -> dict:
        """Независимая улика: ДОЕЗЖАЕТ ли запрос по публичному адресу до нашего процесса.

        Лог cloudflared — свидетельство заинтересованной стороны: он говорит «Registered tunnel
        connection», когда ребро поднялось, и молчит о том, что край Cloudflare отдаёт 530 всем,
        кто пришёл снаружи. Здесь состояние спрашивается у самой сети.

        Автор ответа определяется ЗАГОЛОВКОМ, а не кодом: `Server: cloudflare` — ответил край,
        свой заголовок — доехало до нас. Код 405 на GET нормален (наш транспорт принимает POST),
        поэтому «дошло» и «ok» не одно и то же. Спрашивает дверь загрузчика (`Fetcher.status`, политика
        `tunnel_diagnostics.yaml` → `прозвонка`): код и заголовки без тела, хост — ровно хост своего публичного адреса
        (`_probe_host`), внутренняя сеть закрыта, `file://` и чужая схема — отказ схемы. `timeout` — срок вызывающего
        поверх срока политики. Отказ двери — улика «не дошло» с его причиной, а не исключение.
        """
        адрес = self._public_url or (self._named_url() if self.mode == "named" else "")
        if not адрес or (self.mode == "named" and not self.hostname):     # без hostname у named — подпись, не адрес
            return {"дошло": False, "код": None, "кто": "адреса нет",
                    "текст": "публичный адрес неизвестен — спрашивать нечего"}
        хост = self._probe_host(адрес)
        if not хост:
            return {"дошло": False, "код": None, "кто": "нет ответа",
                    "текст": "адрес — не свой публичный адрес туннеля: его не спрашиваем"}
        дверь = answers(каталог_проверок(), TUNNEL_PROBE, f"https://{хост}", transport=self.transport,
                        resolve=self.resolve, clock=self.clock, sleep=self.sleep)
        try:
            код, заголовки = дверь.status(адрес, {"User-Agent": "vpm-tunnel-probe"}, within=timeout)
        except ContractError as отказ:          # текст владельцу — реестром (`cli_text`), а не словарём фактов
            return {"дошло": False, "код": None, "кто": "нет ответа", "отказ": отказ,
                    "текст": f"{отказ.code} ({отказ.facts.get('status', '—')})"}
        кто = заголовки.get("server", "")
        край = self._край_ответил(код, кто)
        return {"дошло": not край, "код": код, "кто": кто or ("край" if край else "неизвестен"),
                "текст": (f"край Cloudflare отвечает {код} — до нашего процесса запрос не доходит"
                          if край else f"ответил наш процесс ({код})")}

    def _probe_host(self, адрес: str) -> str:
        """Хост прозвонки — ровно хост своего публичного адреса: named — объявленный `hostname`, quick — разобранный хост
        адреса целиком в зоне quick-туннеля (`_TRYCLOUDFLARE_RE` полным совпадением, а не поиском в строке); иначе пусто."""
        хост = (urlsplit(адрес).hostname or "").lower()
        if not хост:
            return ""
        if self.mode == "named":
            return хост if хост == self.hostname.lower() else ""
        return хост if _TRYCLOUDFLARE_RE.fullmatch(f"https://{хост}") else ""

    def status(self) -> dict:
        """Снимок здоровья туннеля (для мониторинга/логов сервера): соединение и адрес quick — живым ответом сервера
        метрик соединителя (`_ready`, `_quick_url`), а не строкой его журнала."""
        proc = self._proc
        running = proc is not None and proc.poll() is None
        готов = self._ready() if running else {"готов": False, "соединений": 0}
        адрес = self._quick_url() if готов["готов"] and self.mode != "named" else None
        with self._status_lock:
            self._connected, self._connections = bool(готов["готов"]), int(готов["соединений"])
            if адрес:
                self._public_url = адрес
            return {
                "running": running,
                "connected": self._connected,
                "connections": self._connections,
                "attempts": self._attempts,
                "public_url": self._public_url,
                "last_error": self._last_error,
                "supervision": dict(self._supervision),
                "uptime_sec": round((self.clock() - self._proc_started), 1) if self._proc_started else 0.0,
            }

    @property
    def public_url(self) -> str | None:
        return self._public_url

    def stop(self):
        """Останавливает туннель и keepalive: флаг остановки снимает подъём и надзор в любой их фазе, событие — паузу."""
        self._stopping = True
        self._stopped.set()
        self._end_attempt()

    def _end_attempt(self) -> None:
        """Гасит процесс текущей попытки, не трогая флаг остановки: повтор подъёма — не остановка туннеля."""
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=self.stop_grace)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None
        if proc:
            self._release(proc)

    def _release(self, proc: "subprocess.Popen") -> None:
        """Кончившийся процесс подобран, его труба закрыта, когда её больше не читают (читатель `_drain` кончился по концу
        вывода) — иначе их закрывает сборщик мусора с ResourceWarning: дескриптор течёт на каждую попытку и перезапуск."""
        proc.wait()
        ended = self._ended
        if ended is not None:
            ended.wait()
        if proc.stdout is not None:
            proc.stdout.close()

    @staticmethod
    def _cancelled() -> "TunnelCancelled":
        return TunnelCancelled("подъём снят остановкой сервера")

    def _by_token(self) -> bool:
        """Режим токена — одно решение для argv и среды ребёнка."""
        return self.mode == "named" and bool(self.tunnel_token)

    def _child_spawn(self) -> dict:
        """Аргументы порождения туннеля — из узла (среда без секретов сервера, вывод в utf-8; строки журнала — replace:
        битый байт не должен убить поток супервизора); своё — имена личности поставщика сняты из базы среды, а токен
        ставится только в режиме токена: каждое имя `_IDENTITY_ENV` главнее объявленного файла учётных данных."""
        без_личности = {k: v for k, v in os.environ.items() if k not in _IDENTITY_ENV}
        свой = {_TOKEN_ENV: self.tunnel_token} if self._by_token() else {}
        return child_spawn("replace", свой, environ=без_личности)

    def _metrics(self, path: str) -> object:
        """Ответ сервера метрик соединителя по пути `path` — дверью загрузчика: петля и порт — ровно объявленный адрес
        (`готовность.<поставщик>.metrics_address`), JSON в память в пределе политики `метрики`. Отказ двери — ContractError."""
        адрес = f"http://{self.readiness.metrics_address}"
        return answers(каталог_проверок(), TUNNEL_METRICS, адрес, transport=self.transport, resolve=self.resolve,
                       clock=self.clock, sleep=self.sleep).answer(f"{адрес}{path}", {})

    def _ready(self) -> dict:
        """Готовность соединителя — событием поставщика, а не строкой журнала: поле `ready_field` ответа `ready_path` —
        счёт соединений с краем, готов — больше нуля. Не слушает, 503 («соединений нет») или чужое тело — «не готов» с
        кодом причины, а не исключение."""
        try:
            ответ = self._metrics(self.readiness.ready_path)
        except ContractError as отказ:
            return {"готов": False, "соединений": 0, "почему": отказ.code}
        счёт = ответ.get(self.readiness.ready_field) if isinstance(ответ, dict) else None
        счёт = счёт if isinstance(счёт, int) and not isinstance(счёт, bool) else 0
        return {"готов": счёт > 0, "соединений": счёт, "почему": ""}

    def _quick_url(self) -> str | None:
        """Адрес quick-туннеля — поле `quick_field` ответа `quick_path` сервера метрик, а не регуляркой по журналу; нет
        ответа или поля — None."""
        try:
            ответ = self._metrics(self.readiness.quick_path)
        except ContractError:
            return None
        хост = ответ.get(self.readiness.quick_field) if isinstance(ответ, dict) else None
        return f"https://{хост}" if isinstance(хост, str) and хост else None

    def _metrics_address_free(self) -> None:
        """Адрес сервера метрик никто не слушает до запуска соединителя: иначе готовность прочлась бы у чужого процесса.
        Ответ любым кодом (и 503) — адрес слушают; сбой соединения — свободен. Занят — громкий отказ с адресом."""
        try:
            self._metrics(self.readiness.ready_path)
        except ContractError as отказ:
            if not isinstance(отказ.facts.get("status"), int):
                return
        raise TunnelError(f"адрес сервера метрик туннеля {self.readiness.metrics_address} уже слушает другой процесс — "
                          "освободи порт или смени готовность.cloudflared.metrics_address в config/tunnel_diagnostics.yaml")

    def _drain(self, lines, ended: threading.Event) -> None:
        """Единственный читатель вывода процесса соединителя: дренаж трубы (без чтения буфер переполнится, и соединитель
        встанет на записи), ошибки и непоправимый отказ — в статус; конец вывода — событие `ended`: процесс кончился."""
        try:
            for raw in lines:
                if not raw:
                    break
                self._apply_line(raw)
        except Exception as e:                  # сбой чтения или разбора — тем же узлом надзора, а не тихий конец вывода
            self._fell(e)
        finally:
            ended.set()


def declared_hostname(config_path: str | Path | None) -> str:
    """Публичный hostname туннеля из его объявления; не объявлен или объявления нет — пусто. Одно решение для туннеля
    (URL коннектора) и сервера (кому разрешено называться нашим хостом); дверь — та же, что у туннеля."""
    return CloudflaredTunnel._load_config(config_path).get("hostname") or ""


def _секрет_не_здесь(значение: str) -> str:
    """Секрет в файле декларации (права чтения для всех) — отказ двери; пустой ключ — законная пустота."""
    if значение:
        raise PydanticCustomError(
            "secret_in_declaration", "токен туннеля — в среде сервера (MCP_TUNNEL_TOKEN), не в файле декларации")
    return значение


_БЕЗ_СЕКРЕТА = Annotated[str, AfterValidator(_секрет_не_здесь)]


def _переехал(значение: object) -> object:
    """Ключ надзора в машинном файле — отказ двери с адресом переезда (до суда типа: число тоже): правка здесь молча
    ничего бы не меняла."""
    raise PydanticCustomError(
        "moved_key", "политика надзора туннеля — в config/tunnel_diagnostics.yaml → надзор, не в машинном файле")


_ПЕРЕЕХАЛ = Annotated[str, BeforeValidator(_переехал)]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Туннель для MCP-сервера")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[2] / "config" / "tunnel.yaml"))
    args = parser.parse_args()

    tunnel = CloudflaredTunnel(port=args.port, config_path=args.config)
    try:
        url = tunnel.start()
        print(f"Туннель поднят: {url}/mcp")
        print("Ctrl+C для остановки.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Ошибка туннеля: {e}")
    finally:
        tunnel.stop()


if __name__ == "__main__":
    main()
