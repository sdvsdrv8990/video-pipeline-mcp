"""
core/transport/tunnel.py — Туннель к Claude AI Web через Cloudflare

## Назначение
Даёт облачному Claude AI Web публичный HTTPS-доступ к локальному серверу, поднимаясь ВМЕСТЕ с
сервером. Поставщик — cloudflared, он идёт дочерним процессом; ядро о туннеле не знает.

## Границы
- `quick` — эфемерный URL, он МЕНЯЕТСЯ при каждом запуске, и настроенный коннектор Claude
  после перезапуска смотрит в никуда; `named` — постоянный hostname.
- Keepalive-поток перезапускает cloudflared при падении; секреты туннеля — вне git.
"""

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Literal, assert_never

import httpx

from core.contracts import ContractError
from core.declaration import Declaration


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
    "cloudflared не найден в PATH. Установи его (см. install.sh) или бинарь "
    "проекта ./bin/cloudflared"
)


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
        self.hostname = cfg.get("hostname") or ""
        # named-режим подключается либо connector-токеном, либо локальными credentials.
        # Токен — секрет: приоритет у env MCP_TUNNEL_TOKEN, чтобы не держать его
        # в коммитимом config/tunnel.yaml.
        self.tunnel_token = os.environ.get("MCP_TUNNEL_TOKEN") or cfg.get("tunnel_token") or ""
        self.tunnel_id = cfg.get("tunnel_id") or ""
        self.tunnel_name = cfg.get("tunnel_name") or ""
        self.credentials_file = cfg.get("credentials_file") or ""

        # Бинарь: сначала локальный ./bin/cloudflared, затем PATH.
        local_bin = Path(__file__).resolve().parents[2] / "bin" / "cloudflared"
        self.binary = str(local_bin) if local_bin.exists() else "cloudflared"

        # Параметры повторов (backoff), чтобы не упереться в лимиты Cloudflare.
        self.retry_base = float(cfg.get("retry_base_seconds", 2))     # старт паузы
        self.retry_max = float(cfg.get("retry_max_seconds", 60))      # потолок паузы
        self.retry_reset = float(cfg.get("retry_reset_seconds", 60))  # «стабильный» прогон
        # Предел ПЕРВОГО подъёма: cloudflared, которому режут путь до края (VPN, firewall), живёт
        # и молчит — без предела ожидание было бесконечным, и вывод обрывался на полуслове.
        # Запасное значение ПРОИЗВОДНОЕ, а не второй литерал: потолок пауз уже объявлен, и число,
        # написанное здесь ещё раз, разошлось бы с декларацией молча.
        self.ready_timeout = float(cfg.get("ready_timeout_seconds") or self.retry_max)
        # Повтор ПЕРВОГО подъёма: супервизор сторожит только туннель, который хотя бы раз
        # соединился, поэтому у самой хрупкой попытки повторов не было вовсе. Нет объявления —
        # нет и повтора: одна попытка, как было до этой правки.
        self.first_attempts = int(cfg.get("first_attempts") or 1)

        self._proc: subprocess.Popen | None = None
        self._public_url: str | None = None
        self._stopping = False
        self._supervisor: threading.Thread | None = None

        # Статус здоровья (обновляется супервизором, читается через status()).
        self._status_lock = threading.Lock()
        self._connected = False        # есть ли живое edge-соединение
        self._connections = 0          # сколько connIndex зарегистрировано
        self._attempts = 0             # подряд неудачных перезапусков процесса
        self._last_error: str | None = None
        self._proc_started: float | None = None
        # Кто убил процесс, знает только тот, кто убил: код -9 сам по себе неотличим от чужого
        # `kill`, и вердикт «завершился code=-9» ничего не объясняет тому, кто его читает.
        self._killed_by_watchdog = False

    @staticmethod
    def _load_config(config_path: str | Path | None) -> dict:
        """Путь НЕ задан — режим без объявления (quick), это решение вызывающего. Путь задан, а
        файла нет — промах: молча поднятый на дефолтах туннель не соответствует ничему."""
        if not config_path:
            return {}
        return Declaration(
            config_path, ContractError, "туннеля",
            "Заведи config/tunnel.yaml — режим, порт и hostname объявлены там.").data

    def _binary_available(self) -> bool:
        return self.binary.startswith("/") and Path(self.binary).exists() \
            or shutil.which(self.binary) is not None

    def _build_command(self) -> list[str]:
        """Собирает команду cloudflared под выбранный режим.

        named — два пути (по требованиям Cloudflare):
          • token: `cloudflared tunnel run --token <TOKEN>` — дашбордовый туннель;
            ingress (домен → 127.0.0.1:port) настроен на стороне Cloudflare.
          • credentials: `cloudflared tunnel run --url <url> <id|name>` c
            --credentials-file — локально управляемый туннель, DNS-маршрут привязан
            к домену командой `cloudflared tunnel route dns <name> <hostname>`.

        Токен читается из env (MCP_TUNNEL_TOKEN), НЕ из config/tunnel.yaml.
        Env-приоритет: env > yaml. yaml используется ТОЛЬКО для не-секретных полей.
        """
        url = f"http://127.0.0.1:{self.port}"
        if self.mode == "named":
            # Путь 1: connector-токен (ID+секрет внутри токена).
            if self.tunnel_token:
                return [self.binary, "tunnel", "--no-autoupdate", "run", "--token", self.tunnel_token]
            # Путь 2: локальные credentials + UUID/имя туннеля.
            ref = self.tunnel_id or self.tunnel_name
            if not ref:
                raise ValueError(
                    "mode=named требует tunnel_token ЛИБО (tunnel_id/tunnel_name + credentials_file) "
                    "в config/tunnel.yaml. Получи их в дашборде Cloudflare после привязки домена."
                )
            cmd = [self.binary, "tunnel", "--no-autoupdate"]
            if self.credentials_file:
                cmd += ["--credentials-file", self.credentials_file]
            cmd += ["run", "--url", url, ref]
            return cmd
        # quick (по умолчанию): эфемерный trycloudflare-URL, без аккаунта/домена/токена.
        return [self.binary, "tunnel", "--no-autoupdate", "--url", url]

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
        """Готовность по СОСТОЯНИЮ СОЕДИНЕНИЯ, а не по таймеру.

        Читает поток строк cloudflared:
          • quick  → готов, когда есть URL И событие соединения;
          • named  → готов, когда есть событие соединения.
        Отказ = 'fatal'-строка ИЛИ закрытие потока (процесс вышел) →
        поднимаем TunnelError с РЕАЛЬНЫМ текстом cloudflared.

        Args:
            lines: итератор строк (stdout процесса или фейковые данные в тесте).
            poll_fn: функция → код возврата процесса или None (жив ли он).
        """
        url = None
        # Срок ждут ВСЕГДА: у прежнего ожидания предела не было вовсе, и туннель, которому режут
        # путь до края (VPN, firewall), держал подъём молча — вывод обрывался, вердикта не было.
        предел = deadline if deadline is not None else time.monotonic() + self.ready_timeout
        # Счётчик, а не признак: cloudflared держит НЕСКОЛЬКО рёбер, и `Unregistered connIndex=2`
        # при живых остальных не означает «связи нет». Супервизор считает так же — один словарь,
        # одна арифметика.
        connections = 0
        last_err = None

        for raw in lines:
            if not raw:
                break
            if time.monotonic() > предел:
                raise TunnelError(
                    f"соединение не установлено за {self.ready_timeout:.0f} с"
                    + (f" (последнее от cloudflared: {last_err})" if last_err else "")
                    + ". Путь до края Cloudflare (порт 7844) закрыт или идёт через сеть, которая "
                      "его режет — проверь VPN/файрвол")
            kind, val = self._classify_line(raw)
            if kind == "url":
                url = val
            elif kind == "connected":
                connections += 1
            elif kind == "disconnected":
                # Последнее ребро потеряно ДО готовности: держать прежнее «соединено» значит отдать
                # URL туннеля, которого уже нет. Ждём следующей регистрации.
                connections = max(0, connections - 1)
            elif kind == "fatal":
                raise TunnelError(f"cloudflared отказал: {val}")
            elif kind == "error":
                last_err = val  # мягкая ошибка/ретрай — как контекст
            elif kind is not None:
                assert_never(kind)

            # Триггер готовности — наличие соединения (для quick ещё и URL).
            if connections and (url is not None or self.mode == "named"):
                self._public_url = url if url is not None else self._named_url()
                return self._public_url

        # Поток закрылся → процесс завершился, соединение не установлено.
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
            try:
                url = self._one_attempt()
                break
            except TunnelCancelled:
                # Отмена не повторяется: нас останавливают, а не мы не смогли подняться.
                self.stop()
                raise
            except TunnelError as beda:
                self.stop()
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
        """Запуск процесса cloudflared (с фиксацией времени старта)."""
        self._proc_started = time.time()
        return subprocess.Popen(
            self._build_command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    # ── Backoff ─────────────────────────────────────────────────────────────

    def _next_attempts(self, prev: int, uptime: float) -> int:
        """Счётчик подряд-неудач: стабильный прогон (uptime≥reset) обнуляет разгон.

        Так «долго живший туннель, который умер один раз» перезапускается быстро,
        а «падающий по кругу» — с растущей паузой (защита от лимитов Cloudflare).
        """
        return 1 if uptime >= self.retry_reset else prev + 1

    def _backoff_delay(self, attempts: int) -> float:
        """Экспоненциальная пауза с потолком и джиттером."""
        import random
        delay = min(self.retry_base * (2 ** max(0, attempts - 1)), self.retry_max)
        return delay + random.uniform(0, self.retry_base)

    def _sleep_interruptible(self, delay: float):
        """Пауза, прерываемая stop() (не держим shutdown на всю паузу)."""
        end = time.time() + delay
        while not self._stopping:
            remaining = end - time.time()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))

    # ── Супервизор ──────────────────────────────────────────────────────────

    def _apply_line(self, raw: str):
        """Обновление статуса по строке лога (соединения/ошибки/URL)."""
        kind, val = self._classify_line(raw)
        with self._status_lock:
            if kind == "connected":
                self._connections += 1
                self._connected = True
                self._attempts = 0          # соединение восстановлено → сброс разгона
                self._last_error = None
            elif kind == "disconnected":
                self._connections = max(0, self._connections - 1)
                self._connected = self._connections > 0
            elif kind == "url":
                self._public_url = val
            elif kind == "error" or kind == "fatal":
                self._last_error = val
            elif kind is not None:
                assert_never(kind)

    def _start_supervisor(self):
        """Фоновый цикл: следит за процессом И соединением, дренажит stdout,
        перезапускает cloudflared с backoff при смерти процесса.

        Дренаж обязателен: после готовности start() перестаёт читать stdout —
        без чтения буфер пайпа переполнится и cloudflared зависнет на записи.
        Транзиентные обрывы соединения НЕ рестартят процесс (их лечит сам
        cloudflared, держащий пул edge-соединений) — рестарт только при смерти
        процесса, чтобы не плодить подключения и не упереться в лимиты.
        """
        def _run():
            while not self._stopping:
                proc = self._proc
                if proc is None or proc.stdout is None:
                    break

                # Блокирующее чтение до EOF: дренаж + обновление статуса соединения.
                for raw in proc.stdout:
                    if self._stopping:
                        return
                    self._apply_line(raw)

                # stdout закрылся → процесс cloudflared завершился (HP = down).
                if self._stopping:
                    break
                uptime = (time.time() - self._proc_started) if self._proc_started else 0.0
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
                    self._proc = self._spawn()
                except Exception as e:
                    with self._status_lock:
                        self._last_error = f"respawn failed: {e}"
                    break

        self._supervisor = threading.Thread(target=_run, daemon=True)
        self._supervisor.start()

    @staticmethod
    def _край_ответил(код: int, сервер: str) -> bool:
        """Кто автор ответа — край Cloudflare или наш процесс.

        Судится ЗАГОЛОВКОМ: он называет автора прямо, а код — нет (405 на GET законен у нас, 530
        бывает только у края). Заголовка нет — тогда по коду: 5xx снаружи означает, что до нас
        запрос не дошёл, наш транспорт таких не отдаёт на пустой GET.
        """
        return "cloudflare" in сервер.lower() or (not сервер and код >= 500)

    def probe(self, timeout: float = 8.0) -> dict:
        """Независимая улика: ДОЕЗЖАЕТ ли запрос по публичному адресу до нашего процесса.

        Лог cloudflared — свидетельство заинтересованной стороны: он говорит «Registered tunnel
        connection», когда ребро поднялось, и молчит о том, что край Cloudflare отдаёт 530 всем,
        кто пришёл снаружи. Здесь состояние спрашивается у самой сети.

        Автор ответа определяется ЗАГОЛОВКОМ, а не кодом: `Server: cloudflare` — ответил край,
        свой заголовок — доехало до нас. Код 405 на GET нормален (наш транспорт принимает POST),
        поэтому «дошло» и «ok» не одно и то же. Спрашивает `httpx`: адрес приходит СНАРУЖИ, а
        `urlopen` открыл бы и `file://`, отдав содержимое диска за ответ сети.
        """
        адрес = self._public_url or (self._named_url() if self.mode == "named" else "")
        if not адрес or адрес.startswith("https://<"):
            return {"дошло": False, "код": None, "кто": "адреса нет",
                    "текст": "публичный адрес неизвестен — спрашивать нечего"}
        try:
            with httpx.Client(timeout=timeout) as клиент:
                ответ = клиент.get(адрес, headers={"User-Agent": "vpm-tunnel-probe"})
            код, кто = ответ.status_code, ответ.headers.get("Server", "")
        except (httpx.HTTPError, httpx.InvalidURL) as beda:
            return {"дошло": False, "код": None, "кто": "нет ответа", "текст": str(beda)}
        край = self._край_ответил(код, кто)
        return {"дошло": not край, "код": код, "кто": кто or ("край" if край else "неизвестен"),
                "текст": (f"край Cloudflare отвечает {код} — до нашего процесса запрос не доходит"
                          if край else f"ответил наш процесс ({код})")}

    def status(self) -> dict:
        """Снимок здоровья туннеля (для мониторинга/логов сервера)."""
        proc = self._proc
        with self._status_lock:
            return {
                "running": proc is not None and proc.poll() is None,
                "connected": self._connected,
                "connections": self._connections,
                "attempts": self._attempts,
                "public_url": self._public_url,
                "last_error": self._last_error,
                "uptime_sec": round((time.time() - self._proc_started), 1) if self._proc_started else 0.0,
            }

    @property
    def public_url(self) -> str | None:
        return self._public_url

    def stop(self):
        """Останавливает туннель и keepalive."""
        self._stopping = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cloudflare-туннель для MCP-сервера")
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
