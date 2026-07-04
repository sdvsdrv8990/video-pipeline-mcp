"""
core/transport/tunnel.py — Туннель к Claude AI Web через Cloudflare

## Назначение
Даёт облачному Claude AI Web публичный HTTPS-доступ к локальному серверу,
поднимаясь ВМЕСТЕ с сервером (одна команда). Поставщик — cloudflared.

## Режимы
- quick  — эфемерный URL *.trycloudflare.com. Работает БЕЗ домена и аккаунта.
           URL меняется при каждом запуске (для теста/разработки).
- named  — постоянный hostname (нужен аккаунт Cloudflare + домен + credentials).
           URL стабилен — коннектор Claude настраивается один раз.

## Изоляция
Ядро о туннеле не знает. cloudflared запускается как дочерний процесс;
keepalive-поток перезапускает его при падении. Секреты — вне git (.gitignore).

## Запуск отдельно (диагностика)
    python -m core.transport.tunnel --port 8080
"""

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import yaml


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


class CloudflaredTunnel:
    """Управление туннелем cloudflared: запуск, ожидание URL, keepalive, стоп.

    Attributes:
        port: Локальный порт сервера (форвардим на него).
        mode: 'quick' | 'named'.
        hostname: Постоянный hostname для named-режима.
        binary: Путь/имя бинаря cloudflared.
    """

    def __init__(self, port: int = 8080, config_path: str | Path | None = None):
        cfg = self._load_config(config_path)

        self.port = int(cfg.get("local_port", port) or port)
        self.mode = cfg.get("mode", "quick")
        self.hostname = cfg.get("hostname") or ""
        # named-режим, два пути подключения:
        #  1) connector-ТОКЕН из дашборда (Zero Trust → Networks → Tunnels) —
        #     токен кодирует ID туннеля + секрет; ingress настраивается в дашборде.
        #  2) локальные credentials: UUID туннеля (tunnel_id) или имя + credentials_file
        #     (создаётся `cloudflared tunnel create <name>`), маршрут DNS привязан к домену.
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

    @staticmethod
    def _load_config(config_path: str | Path | None) -> dict:
        if not config_path:
            return {}
        path = Path(config_path)
        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return {}

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

        D31: токен читается из env (MCP_TUNNEL_TOKEN), НЕ из config/tunnel.yaml.
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
    def _classify_line(line: str) -> tuple[str | None, str | None]:
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

    def _await_ready(self, lines, poll_fn) -> str:
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
        connected = False
        last_err = None

        for raw in lines:
            if not raw:
                break
            kind, val = self._classify_line(raw)
            if kind == "url":
                url = val
            elif kind == "connected":
                connected = True
            elif kind == "fatal":
                raise TunnelError(f"cloudflared отказал: {val}")
            elif kind == "error":
                last_err = val  # мягкая ошибка/ретрай — как контекст

            # Триггер готовности — наличие соединения (для quick ещё и URL).
            if connected and (url is not None or self.mode == "named"):
                self._public_url = url if url is not None else self._named_url()
                return self._public_url

        # Поток закрылся → процесс завершился, соединение не установлено.
        code = poll_fn()
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

        self._proc = self._spawn()
        assert self._proc.stdout is not None
        try:
            url = self._await_ready(self._proc.stdout, self._proc.poll)
        except TunnelError:
            self.stop()
            raise
        with self._status_lock:
            self._connected = True
            self._connections = max(self._connections, 1)
        self._start_supervisor()
        return url

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
            elif kind in ("error", "fatal"):
                self._last_error = val

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
