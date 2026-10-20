"""core/runner/supervisor.py — раннер поднимают командой, и его состояние переживает сервер.

pid, порт и токен лежат в файле только для владельца, а живость проверяется прозвонкой, а не верой
в запись: сервер перезапустили, а раннер жив — иначе второй запуск поднял бы второй процесс на
занятый порт. Авто-перезапуска НЕТ намеренно: в отличие от туннеля раннер падает по причине (нехватка
памяти, битые веса, отсутствующая зависимость), и тихо поднять его заново значит спрятать причину и
уронить снова. Упал — это видно, а поднимает человек или ИИ той же командой.
"""

import contextlib
import functools
import hashlib
import json
import logging
import os
import random
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import ClassVar, NamedTuple, NoReturn
from urllib.parse import urlparse

import psutil

from core.contracts import ContractError
from core.declaration import Declaration
from core.declaration_schemas import RUNNER_DOCKER_POLL, RUNNER_HEALTH, ProviderUrl
from core.engine.engine import incident
from core.environment import ChildSaid, child_spawn
from core.jobs import backoff, paced
from core.paths import Files
from core.providers.download import answers
from core.providers.resolver import ProviderError

if sys.platform == "win32":
    import msvcrt

log = logging.getLogger(__name__)

TOKEN_ENV = "MCP_RUNNER_TOKEN"
READY_ENV = "MCP_RUNNER_READY_FD"               # адрес канала готовности в среде раннера: дескриптор (POSIX) или хэндл
NOBODY = "65534:65534"                          # nobody образа раннера (/etc/passwd python:3.14-slim и video-pipeline-runner)
_CHILDREN: dict[int, subprocess.Popen] = {}     # раннеры, поднятые этим процессом: событие их конца и код — у владельца
_EXITS: dict[tuple[int, float], str] = {}       # смерть запуска (pid, started) — номер её события в журнале сервера, один раз


class _Ready(NamedTuple):
    """Одноразовый канал готовности раннера: конец чтения — у сервера, конец записи — ребёнку по адресу в его среде."""

    read: int
    write: int
    address: str
    spawn: dict


class RunnerSupervisor:
    """Жизненный цикл процесса раннера: поднять, прозвонить, остановить."""

    # Сеть, часы и сон прозвонки (MF-16): None — настоящий транспорт; подмена — на классе, как у адаптеров.
    transport: ClassVar = None
    resolve: ClassVar = staticmethod(socket.getaddrinfo)
    clock: ClassVar = staticmethod(time.monotonic)
    sleep: ClassVar = staticmethod(time.sleep)
    rng: ClassVar[random.Random] = random.Random()     # разброс пауз опроса контейнера; подмена — воспроизводимый генератор

    def __init__(self, registry, project_root: str | Path):
        self.registry = registry
        self.root = Path(project_root)

    # ═══ Декларация ═══

    @property
    def rules(self) -> dict:
        return ((self.registry.config.get("local") or {}).get("runner")) or {}

    @property
    def endpoint(self) -> dict:
        name = str(self.rules.get("provider") or "")
        decl = (((self.registry.config.get("online") or {}).get("http")) or {}).get(name)
        if not decl:
            raise self._decl.link_refused(
                "runner_undeclared", "провайдер раннера объявлен блоком online.http под тем же именем",
                [("local", "runner", "provider")], self._decl, [("online", "http", name)] if name else [])
        return decl

    @functools.cached_property
    def _decl(self) -> Declaration:
        """Дверь `providers.yaml` для политики прозвонки (`local.runner.health`) — схемой, с путём ключа в отказе."""
        return Declaration(self.registry.config_file, ProviderError, "провайдеров")

    @property
    def base_url(self) -> str:
        """Адрес раннера без пути: `/run` и `/health` висят на одном хосте."""
        parsed = urlparse(str(self.endpoint.get("url") or ""))
        return f"{parsed.scheme}://{parsed.netloc}"

    def _path(self, key: str) -> Path:
        return self.root / self.registry.need("local", "runner", key, kind=str)

    @property
    def state_file(self) -> Path:
        return self._path("state_file")

    @property
    def log_file(self) -> Path:
        return self._path("log_file")

    # ═══ Состояние ═══

    def _read(self) -> dict:
        try:
            return json.loads(Files(self.state_file.parent).read_text(self.state_file.name))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, state: dict) -> None:
        # Токен лежит здесь: сервер перезапускается, а раннер живёт дальше, и без записи новый
        # сервер не смог бы с ним говорить. Права — только владельцу, как у ключа: с первого байта.
        Files(self.state_file.parent).replace(self.state_file.name, json.dumps(state, ensure_ascii=False), mode=0o600)

    def token(self) -> str:
        """Токен живого раннера. Нет раннера — нет токена, и это не «пустая строка на удачу»."""
        return str(self._read().get("token") or "")

    # ═══ Прозвонка ═══

    def _probe(self, token: str = "", within: float | None = None) -> dict:
        """Что отвечает `/health` — дверью загрузчика по политике петли `local.runner.health`: хост — литерал петли из
        объявленного адреса раннера, срок — её `deadline_sec` или остаток вызывающего `within`. Молчание — это `{}`, а
        не исключение: молчание тоже ответ; отказ двери (не поднят, отказ ответа, чужое тело, срок) — в журнал с кодом."""
        headers = {}
        scheme = str(self.endpoint.get("auth") or "")
        if token and scheme.startswith("header:"):
            headers[scheme.split(":", 1)[1]] = token
        declared = self.registry.need("online", "http", str(self.rules.get("provider") or ""), "url", kind=ProviderUrl)
        fetcher = answers(self._decl, RUNNER_HEALTH, declared, transport=self.transport, resolve=self.resolve,
                          clock=self.clock, sleep=self.sleep)
        try:
            health = fetcher.answer(f"{self.base_url}/health", headers, within=within)
        except ContractError as e:
            log.debug("раннер %s не ответил на /health: %s %s", self.base_url, e.code, e.facts)
            return {}
        if not isinstance(health, dict):
            log.debug("на порту раннера %s ответил не раннер: /health — не объект JSON", self.base_url)
            return {}
        return health

    @staticmethod
    def _alive(pid: int) -> bool:
        """Жив ли процесс. Мёртвый ребёнок — не «жив», хотя система отвечает именно так.

        Раннер запущен сервером, то есть его ребёнок: умерев, он остаётся зомби, пока его не
        подберут, а `kill -0` на зомби отвечает успехом. Без этого упавший раннер вечно числился
        бы «встаёт» — ровно то молчание вместо причины, ради которого раннер и выносили.
        Ветку ОС держит psutil: на Windows `os.kill(pid, 0)` не проба, а TerminateProcess.
        """
        if not pid:
            return False
        child = _CHILDREN.get(pid)
        if child is not None:
            return child.poll() is None         # свой ребёнок: конец и код — у владельца, а не проба ОС по pid
        try:
            proc = psutil.Process(pid)
            if proc.status() == psutil.STATUS_ZOMBIE:
                with contextlib.suppress(ChildProcessError, OSError):
                    os.waitpid(pid, 0)  # свой зомби подбирается сразу; чужой — не наш ребёнок
                return False
            return proc.is_running()
        except psutil.Error:
            return False

    @staticmethod
    def _born(pid: int) -> float | None:
        """Время создания процесса по ОС (psutil) — личность pid; процесса нет — None."""
        try:
            return psutil.Process(pid).create_time()
        except psutil.Error:
            return None

    def _ours(self, state: dict) -> bool:
        """Процесс записи о запуске жив и это он: pid сверен со временем создания из записи — ОС отдаёт pid умершего
        раннера другому процессу, и сигнал по голому pid ушёл бы постороннему. Записи без времени верить нельзя."""
        pid = int(state.get("pid") or 0)
        if pid in _CHILDREN or not self._alive(pid):
            return self._alive(pid)
        return self._born(pid) == state.get("created")

    def status(self) -> dict:
        """Жив, не поднят, умер или занял чужой порт — но никогда не молчание."""
        state = self._read()
        pid = int(state.get("pid") or 0)
        health = self._probe(str(state.get("token") or ""))
        phase = "stopped"
        note = "Раннер не поднят. Локальные модели считаются в самом сервере."
        if health.get("ok"):
            phase = "running" if pid and self._ours(state) else "foreign"
            note = ("Раннер отвечает." if phase == "running" else
                    "На объявленном порту кто-то отвечает, но это не наш процесс: наш pid мёртв. "
                    "Останови постороннего или смени порт в config/providers.yaml.")
        elif state:
            phase = "running_silent" if self._ours(state) else "exited"
            note = ("Процесс жив, но `/health` не отвечает — он ещё встаёт или завис."
                    if phase == "running_silent" else
                    "Раннер умер: процесса нет, а запись о запуске осталась. Причина — в журнале.")
        current = {
            "phase": phase, "pid": pid, "url": self.base_url, "note": note,
            "mode": state.get("mode", ""), "started": state.get("started", 0),
            "uptime_sec": round(time.time() - float(state.get("started") or 0), 1) if state else 0.0,
            "pool": health.get("pool") or {}, "calls": health.get("calls", 0),
            "last_error": health.get("last_error", ""),
        }
        if phase in ("exited", "running_silent"):
            current["log"] = Files(self.root).name(self.log_file)   # место журнала — фактом от корня, не в тексте
        child = _CHILDREN.get(pid) if phase == "exited" else None
        if child is not None and child.returncode is not None:
            current |= _ending(child.returncode)         # код владельца: как раннер кончился — фактом, а не текстом
        if phase == "exited":
            # Слова раннера о смерти — в журнал сервера один раз на запуск; клиенту — номер события, а не хвост (TX-02).
            death = (pid, float(state.get("started") or 0))
            if death not in _EXITS:
                _EXITS[death] = incident(log, f"раннер {pid} вышел (код {current.get('exit_code', 'не известен')})",
                                         ChildSaid(self._log_tail()))
            current["event"] = _EXITS[death]
        return current

    def _log_tail(self, lines: int = 12) -> str:
        """Последние строки журнала: причина смерти обязана пережить сам процесс."""
        try:
            return "\n".join(self.log_file.read_text(encoding="utf-8",
                                                     errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    # ═══ Запуск ═══

    def _command(self, mode: str, workspace: Path) -> list[str]:
        """Чем поднимать: своим интерпретатором или контейнером (изоляция зависимостей)."""
        if mode == "process":
            return [sys.executable, "-m", "core.runner",
                    "--config", str(self.registry.config_file),
                    "--workspace", str(workspace)]
        if mode != "docker":
            raise ProviderError("VALIDATION_ERROR", errors=[
                {"field": "mode", "rule": "enum", "expected": ["process", "docker"]}])
        docker = self.rules.get("docker") or {}
        parsed = urlparse(str(self.endpoint.get("url") or ""))
        command = ["docker", "run", "--rm", "--name", str(docker.get("container") or "runner"),
                   # Порт публикуется ТОЛЬКО на петлю: `-p 8770:8770` без адреса открыл бы раннер
                   # всей локальной сети, и токен остался бы единственной преградой.
                   "-p", f"{parsed.hostname}:{parsed.port}:{parsed.port}",
                   # Имя переменной без значения: docker возьмёт его из нашего окружения. Написать
                   # `-e ИМЯ=токен` значило бы показать токен в выводе `ps` любому процессу.
                   "-e", TOKEN_ENV,
                   # OWASP Docker Security Cheat Sheet #3, #4, #8: без возможностей ядра, без повышения прав, образ только
                   # на чтение — писать можно в тома и во временную память, туда же HOME для кешей библиотек.
                   "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only", "--tmpfs=/tmp",
                   "-e", "HOME=/tmp", *self._container_user()]
        for device in docker.get("devices") or []:
            command += ["--device", str(device)]
        for gid in self._device_gids(docker.get("devices") or []):
            command += ["--group-add", gid]
        for host_path, inner in (docker.get("mounts") or {}).items():
            source = workspace if str(host_path) == "workspace" else self.root / str(host_path)
            command += ["-v", f"{Path(source).resolve()}:{inner}"]
        return command + [str(docker.get("image") or "video-pipeline-runner")]

    def _container_user(self) -> list[str]:
        """Кем раннер пишет в тома хоста. Демон с root — пользователем сервера (uid:gid): файлы области получают владельца
        хоста, а не root. Демон без root (rootless) — без `--user`: uid 0 его пространства имён и есть пользователь хоста,
        а uid n — subuid + (n − 1) (docker docs rootless/uid-gid-mapping.md:14-16). Сервер root или ОС без uid — nobody.
        userns-remap флагом не решается: uid 0 там — первый subuid, это настройка демона (там же, :11-13)."""
        if self._rootless():
            return []
        if sys.platform == "win32" or not hasattr(os, "getuid"):    # ОС без uid/gid: владельца хоста флагом не задать
            return ["--user", NOBODY]
        uid = os.getuid()
        return ["--user", f"{uid}:{os.getgid()}" if uid else NOBODY]

    @staticmethod
    def _rootless() -> bool:
        """Демон docker без root: признак `rootless` в Security Options `docker info` (docker docs rootless/_index.md:142-146).
        Демон не ответил — команда строится как для демона с root, а отказ назовёт сам `docker run`."""
        try:
            said = subprocess.run(["docker", "info", "--format", "{{json .SecurityOptions}}"], capture_output=True,
                                  check=False, **child_spawn("replace"))
        except OSError:
            return False
        return "name=rootless" in said.stdout

    @staticmethod
    def _device_gids(devices) -> list[str]:
        """Группа берётся у САМОГО устройства, а не по имени.

        Имя не переносится: в `python:3.14-slim` есть `video`, но нет `render` — а карту на этой
        машине держит именно `render`, и `--group-add render` уронил бы контейнер ровно там, где
        доступ к карте и нужен. Числовой GID работает независимо от базы групп образа.
        """
        gids: list[str] = []
        for path in devices:
            node = Path(str(path))
            for item in (sorted(node.iterdir()) if node.is_dir() else [node]):
                try:
                    gid = str(item.stat().st_gid)
                except OSError:
                    continue                    # устройства нет — про это скажет сам docker
                if gid != "0" and gid not in gids:
                    gids.append(gid)
        return gids

    def _exited(self, proc: subprocess.Popen) -> NoReturn:
        """Раннер вышел, не став готовым: запись о запуске убрана; код и сигнал — фактами отказа, его слова — причиной
        (в журнал сервера под номером события, а не в текст клиенту)."""
        self.state_file.unlink(missing_ok=True)
        raise ProviderError("RUNNER_EXITED", facts=_ending(proc.returncode)) from ChildSaid(self._log_tail())

    def running(self) -> dict | None:
        """Раннер уже отвечает или встаёт — его состояние (`already`); на порту чужой — отказ; не поднят — None."""
        current = self.status()
        if current["phase"] in ("running", "running_silent"):
            return {**current, "already": True}
        if current["phase"] == "foreign":
            raise ProviderError("RUNNER_PORT_TAKEN", facts={"url": self.base_url})
        return None

    def launch(self, workspace: Path, pulse, mode: str = "process") -> dict:
        """Поднять раннер работой задания (`pulse` — узел заданий) и дождаться готовности: готов — состояние; процесс
        вышел — отказ RUNNER_EXITED; задание снято — процесс остановлен, отказ снятия. Срока по часам нет. Процесс даёт
        знак готовности сам (`_signalled`); контейнер знака не даёт — его `/health` опрашивается темпом
        `local.runner.docker_poll` (`jobs.backoff`)."""
        token = secrets.token_urlsafe(32)
        command = self._command(mode, workspace)
        if mode == "docker":
            poll = self._decl.section(*RUNNER_DOCKER_POLL.section, schema=RUNNER_DOCKER_POLL.schema)
            proc = self._spawn(command, mode, token, workspace)

            def ready() -> dict | None:
                if self._probe(token).get("ok"):
                    return {**self.status(), "already": False}
                if proc.poll() is not None:
                    self._exited(proc)
                return None

            with self._held():
                return paced(ready, pulse, backoff(poll.pause_sec, poll.pause_max_sec, poll.jitter, self.rng))
        channel = self._channel()
        proc = self._spawn(command, mode, token, workspace, channel)
        with self._held():
            return self._signalled(proc, channel.read, ready_sign(token), pulse)

    def _spawn(self, command: list[str], mode: str, token: str, workspace: Path,
               channel: _Ready | None = None) -> subprocess.Popen:
        """Процесс раннера: вывод — в его журнал, токен и адрес канала готовности — в его среду, запись о запуске — для
        следующего сервера. Конец записи канала после запуска — только у ребёнка: конец канала приходит с его концом."""
        extra, spawn = ({READY_ENV: channel.address}, channel.spawn) if channel else ({}, {})
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as log:
                log.write(f"\n=== запуск {time.strftime('%Y-%m-%d %H:%M:%S')} ({mode}) ===\n")
                log.flush()
                proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=str(self.root),
                                        **child_spawn("replace", {TOKEN_ENV: token, **extra}), **spawn)
        except BaseException:
            if channel:
                os.close(channel.read)
            raise
        finally:
            if channel:
                os.close(channel.write)
        _CHILDREN[proc.pid] = proc
        self._write({"pid": proc.pid, "created": self._born(proc.pid), "token": token, "mode": mode,
                     "started": time.time(), "workspace": str(workspace), "command": command})
        return proc

    @staticmethod
    def _channel() -> _Ready:
        """Канал готовности для ребёнка. Ветка ОС — здесь: POSIX передаёт ребёнку дескриптор (`pass_fds`); Windows —
        наследуемый хэндл, и только его (`handle_list`): иначе ребёнок унаследовал бы все наследуемые хэндлы сервера."""
        read, write = os.pipe()
        if sys.platform == "win32":
            handle = msvcrt.get_osfhandle(write)
            os.set_handle_inheritable(handle, True)
            return _Ready(read, write, str(handle),
                          {"startupinfo": subprocess.STARTUPINFO(lpAttributeList={"handle_list": [handle]})})
        return _Ready(read, write, str(write), {"pass_fds": (write,)})

    def _signalled(self, proc: subprocess.Popen, channel: int, sign: str, pulse) -> dict:
        """Готовность процесса — событием, без опроса и без своего предела: будят знак раннера в канале, конец канала без
        знака (раннер вышел) и снятие задания; зависание снимает клиент или владелец, статус задания отвечает всё время."""
        woke, ready = threading.Event(), threading.Event()

        def listen() -> None:
            with os.fdopen(channel, encoding="ascii", errors="replace") as said:
                for line in said:
                    if line.strip() == sign:
                        ready.set()
                        break
                else:
                    proc.wait()                 # канал кончился без знака: его закрыл конец процесса
            woke.set()

        pulse.on_cancel(woke.set)
        pulse({"ждёт": "знак готовности раннера"})
        threading.Thread(target=listen, name=f"runner-ready-{proc.pid}", daemon=True).start()
        woke.wait()
        if pulse.cancelled.is_set():
            raise pulse.refusal()
        if ready.is_set():
            return {**self.status(), "already": False}
        self._exited(proc)

    @contextlib.contextmanager
    def _held(self) -> Iterator[None]:
        """Ожидание готовности: не дождались (отказ, снятие) — процесс остановлен, он не остаётся без задания."""
        try:
            yield
        except BaseException:
            self.stop()
            raise

    # ═══ Остановка ═══

    def stop(self) -> dict:
        """Остановить и убрать за собой. Не поднят — это не ошибка, а тот же ответ."""
        state = self._read()
        pid = int(state.get("pid") or 0)
        stopped = False
        if state.get("mode") == "docker":
            container = str((self.rules.get("docker") or {}).get("container") or "")
            stopped = subprocess.run(["docker", "stop", container], check=False,
                                     capture_output=True, **child_spawn("replace")).returncode == 0
        elif self._ours(state):
            # Ветку ОС держит psutil: terminate — SIGTERM на POSIX и TerminateProcess на Windows; wait ждёт событие
            # конца процесса (и подбирает ребёнка), а не опрашивает его.
            with contextlib.suppress(psutil.NoSuchProcess):
                proc = psutil.Process(pid)
                proc.terminate()
                try:
                    proc.wait(timeout=self.registry.need("local", "runner", "stop_grace_sec", kind=float))
                except psutil.TimeoutExpired:
                    proc.kill()                       # не отдал по-хорошему — модель могла зависнуть
                    proc.wait()
            stopped = True
        taken = not stopped and state.get("mode") != "docker" and self._alive(pid)
        _CHILDREN.pop(pid, None)
        self.state_file.unlink(missing_ok=True)
        return {"phase": "stopped", "stopped": stopped, "pid": pid,
                "note": ("Раннер остановлен — локальные модели снова считаются в сервере." if stopped else
                         f"Раннер уже не жив: его pid {pid} занят другим процессом — сигнала ему нет, запись о запуске "
                         "убрана." if taken else "Раннер и не был поднят.")}

    @staticmethod
    def ended(pid: int) -> int | None:
        """Конец своего раннера событием владельца: ждёт выхода процесса, поднятого этим сервером, и отдаёт код (сигнал на
        POSIX — отрицательный); чужой pid — None. Своего предела нет: зависание ловит граница вызывающего."""
        child = _CHILDREN.get(pid)
        return None if child is None else child.wait()


def ready_sign(token: str) -> str:
    """Знак готовности раннера — отпечаток его токена: строку канала судят, а не принимают любую; токен в канал не идёт."""
    return hashlib.sha256(token.encode()).hexdigest()


def ready_writer(address: str, sign: str) -> Callable[..., None] | None:
    """Крючок `print` у `web.run_app` раннера: aiohttp зовёт его, когда все сайты слушают, — знак в канал сервера и конец
    канала. Адреса нет (раннер поднят руками) — None: aiohttp молчит, как при `print=None`."""
    if not address:
        return None

    def signal_ready(*_said: object) -> None:
        if sys.platform == "win32":
            fd = msvcrt.open_osfhandle(int(address), os.O_WRONLY)
        else:
            fd = int(address)
        with os.fdopen(fd, "w", encoding="ascii") as channel:
            channel.write(sign + "\n")

    return signal_ready


def _ending(code: int) -> dict:
    """Как кончился свой ребёнок — фактами: код и, если его убил сигнал (POSIX: код отрицательный), имя сигнала."""
    facts: dict = {"exit_code": code}
    with contextlib.suppress(ValueError):
        if code < 0:
            facts["signal"] = signal.Signals(-code).name
    return facts
