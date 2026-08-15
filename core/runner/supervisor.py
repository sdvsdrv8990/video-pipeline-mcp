"""
core/runner/supervisor.py — раннер поднимают командой, и его состояние переживает сервер.

## Приём взят у соседей
Запуск внешнего процесса и ожидание готовности — то же, что уже делает `core/transport/tunnel.py`;
состояние на диске + прозвонка — то же, что у `core/providers/installer.py`. Новизны здесь нет,
кроме предмета.

## Чего здесь НЕТ намеренно
Авто-перезапуска. У туннеля он уместен: связь рвётся сама и восстанавливается сама. Раннер падает
по причине — OOM, битые веса, отсутствующая зависимость, — и тихо поднять его заново значит
спрятать эту причину и уронить его снова. Упал → это видно как `exited`, а поднимает человек или
ИИ той же командой. Заодно это то, что отличает раннер от демона (S15).

## Состояние на диске
Сервер перезапустили, а раннер остался жив — «не знаю про него» было бы враньём, и второй запуск
поднял бы второй процесс на занятый порт. Поэтому pid, порт и токен лежат в файле (только для
владельца), а живость проверяется прозвонкой, а не верой в запись.
"""

import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from core.providers.resolver import ProviderError

TOKEN_ENV = "MCP_RUNNER_TOKEN"


class RunnerSupervisor:
    """Жизненный цикл процесса раннера: поднять, прозвонить, остановить."""

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
            raise ProviderError(
                "PROVIDER_ADAPTER_MISSING", "Раннер не объявлен провайдером — поднимать нечего.",
                reason="Объяви его в config/providers.yaml: имя в local.runner.provider и блок с "
                       "адресом в online.http под тем же именем.")
        return decl

    @property
    def base_url(self) -> str:
        """Адрес раннера без пути: `/run` и `/health` висят на одном хосте."""
        parsed = urlparse(str(self.endpoint.get("url") or ""))
        return f"{parsed.scheme}://{parsed.netloc}"

    def _path(self, key: str, default: str) -> Path:
        return self.root / str(self.rules.get(key) or default)

    @property
    def state_file(self) -> Path:
        return self._path("state_file", "vendor/runner/state.json")

    @property
    def log_file(self) -> Path:
        return self._path("log_file", "vendor/runner/runner.log")

    # ═══ Состояние ═══

    def _read(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, state: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        # Токен лежит здесь: сервер перезапускается, а раннер живёт дальше, и без записи новый
        # сервер не смог бы с ним говорить. Права — только владельцу, как у ключа.
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_file)

    def token(self) -> str:
        """Токен живого раннера. Нет раннера — нет токена, и это не «пустая строка на удачу»."""
        return str(self._read().get("token") or "")

    # ═══ Прозвонка ═══

    def _probe(self, token: str = "", timeout: float = 3.0) -> dict:
        """Что отвечает `/health`. Молчание — это `{}`, а не исключение: молчание тоже ответ."""
        import httpx

        headers = {}
        scheme = str(self.endpoint.get("auth") or "")
        if token and scheme.startswith("header:"):
            headers[scheme.split(":", 1)[1]] = token
        try:
            response = httpx.get(f"{self.base_url}/health", headers=headers, timeout=timeout)
        except Exception:                                   # noqa: BLE001 — не поднят/не отвечает
            return {}
        if response.status_code >= 400:
            return {}
        try:
            return response.json()
        except Exception:                                   # noqa: BLE001 — не наш ответ на порту
            return {}

    @staticmethod
    def _alive(pid: int) -> bool:
        """Жив ли процесс. Мёртвый ребёнок — не «жив», хотя система отвечает именно так.

        Раннер запущен сервером, то есть его ребёнок: умерев, он остаётся зомби, пока его не
        подберут, а `kill -0` на зомби отвечает успехом. Без этого упавший раннер вечно числился
        бы «встаёт» — ровно то молчание вместо причины, ради которого раннер и выносили.
        """
        if not pid:
            return False
        try:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                return False
        except ChildProcessError:
            pass                        # не наш ребёнок: сервер перезапускали, раннер его пережил
        except OSError:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def status(self) -> dict:
        """Жив, не поднят, умер или занял чужой порт — но никогда не молчание."""
        state = self._read()
        pid = int(state.get("pid") or 0)
        health = self._probe(str(state.get("token") or ""))
        phase = "stopped"
        note = "Раннер не поднят. Локальные модели считаются в самом сервере."
        if health.get("ok"):
            phase = "running" if pid and self._alive(pid) else "foreign"
            note = ("Раннер отвечает." if phase == "running" else
                    "На объявленном порту кто-то отвечает, но это не наш процесс: наш pid мёртв. "
                    "Останови постороннего или смени порт в config/providers.yaml.")
        elif state:
            phase = "running_silent" if self._alive(pid) else "exited"
            note = ("Процесс жив, но `/health` не отвечает — он ещё встаёт или завис. "
                    if phase == "running_silent" else
                    "Раннер умер: процесса нет, а запись о запуске осталась. Причина — в журнале. ")
            note += f"Журнал: {self.log_file}."
        return {
            "phase": phase, "pid": pid, "url": self.base_url, "note": note,
            "mode": state.get("mode", ""), "started": state.get("started", 0),
            "uptime_sec": round(time.time() - float(state.get("started") or 0), 1) if state else 0.0,
            "pool": health.get("pool") or {}, "calls": health.get("calls", 0),
            "last_error": health.get("last_error", ""),
            "log_tail": self._log_tail() if phase in ("exited", "running_silent") else "",
        }

    def _log_tail(self, lines: int = 12) -> str:
        """Последние строки журнала: причина смерти обязана пережить сам процесс."""
        try:
            return "\n".join(self.log_file.read_text(encoding="utf-8",
                                                     errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    # ═══ Запуск ═══

    def start(self, workspace: Path, mode: str = "process") -> dict:
        """Поднять раннер и дождаться, пока он ответит. Уже поднят — вернуть как есть."""
        current = self.status()
        if current["phase"] in ("running", "running_silent"):
            return {**current, "already": True}
        if current["phase"] == "foreign":
            raise ProviderError(
                "INVALID_ACTION", f"Порт раннера ({self.base_url}) занят посторонним процессом.",
                reason="Второй раннер на тот же порт не встанет. Останови занявшего или смени "
                       "адрес в config/providers.yaml → online.http.")

        token = secrets.token_urlsafe(32)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        command = self._command(mode, workspace)
        with self.log_file.open("a", encoding="utf-8") as log:
            log.write(f"\n=== запуск {time.strftime('%Y-%m-%d %H:%M:%S')} ({mode}) ===\n")
            log.flush()
            proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                    env={**os.environ, TOKEN_ENV: token}, cwd=str(self.root))
        state = {"pid": proc.pid, "token": token, "mode": mode, "started": time.time(),
                 "workspace": str(workspace), "command": command}
        self._write(state)

        deadline = time.time() + float(self.rules.get("ready_timeout_sec") or 60)
        pause = float(self.rules.get("poll_sec") or 0.3)
        while time.time() < deadline:
            if self._probe(token).get("ok"):
                return {**self.status(), "already": False}
            if proc.poll() is not None:
                # Умер, не начав отвечать: причина уже в журнале, и она важнее нашей догадки.
                self.state_file.unlink(missing_ok=True)
                raise ProviderError(
                    "LOCAL_INFERENCE_FAILED",
                    f"Раннер завершился при запуске (код {proc.returncode}).",
                    reason=f"Последнее, что он сказал:\n{self._log_tail()}\nЧастая причина — не "
                           "поставлена среда инференса: pip install -e \".[local]\".")
            time.sleep(pause)
        self.stop()
        raise ProviderError(
            "PROVIDER_TIMEOUT", f"Раннер не ответил за {self.rules.get('ready_timeout_sec', 60)} с.",
            reason=f"Процесс поднимался, но готовности не сообщил. Журнал: {self.log_file}.")

    def _command(self, mode: str, workspace: Path) -> list[str]:
        """Чем поднимать: своим интерпретатором или контейнером (изоляция зависимостей)."""
        if mode == "process":
            return [sys.executable, "-m", "core.runner",
                    "--config", str(self.registry.config_file),
                    "--workspace", str(workspace)]
        if mode != "docker":
            raise ProviderError(
                "VALIDATION_ERROR", f"Неизвестный режим запуска раннера: '{mode}'.",
                reason="Объявлены process (тем же интерпретатором) и docker (в контейнере).")
        docker = self.rules.get("docker") or {}
        parsed = urlparse(str(self.endpoint.get("url") or ""))
        command = ["docker", "run", "--rm", "--name", str(docker.get("container") or "runner"),
                   # Порт публикуется ТОЛЬКО на петлю: `-p 8770:8770` без адреса открыл бы раннер
                   # всей локальной сети, и токен остался бы единственной преградой.
                   "-p", f"{parsed.hostname}:{parsed.port}:{parsed.port}",
                   # Имя переменной без значения: docker возьмёт его из нашего окружения. Написать
                   # `-e ИМЯ=токен` значило бы показать токен в выводе `ps` любому процессу.
                   "-e", TOKEN_ENV]
        for device in docker.get("devices") or []:
            command += ["--device", str(device)]
        for group in docker.get("groups") or []:
            command += ["--group-add", str(group)]
        for host_path, inner in (docker.get("mounts") or {}).items():
            source = workspace if str(host_path) == "workspace" else self.root / str(host_path)
            command += ["-v", f"{Path(source).resolve()}:{inner}"]
        return command + [str(docker.get("image") or "video-pipeline-runner")]

    # ═══ Остановка ═══

    def stop(self) -> dict:
        """Остановить и убрать за собой. Не поднят — это не ошибка, а тот же ответ."""
        state = self._read()
        pid = int(state.get("pid") or 0)
        stopped = False
        if state.get("mode") == "docker":
            container = str((self.rules.get("docker") or {}).get("container") or "")
            stopped = subprocess.run(["docker", "stop", container], check=False,
                                     capture_output=True).returncode == 0
        elif self._alive(pid):
            os.kill(pid, signal.SIGTERM)
            deadline = time.time() + float(self.rules.get("stop_grace_sec") or 5)
            while time.time() < deadline and self._alive(pid):
                time.sleep(0.1)
            if self._alive(pid):
                os.kill(pid, signal.SIGKILL)          # не отдал по-хорошему — модель могла зависнуть
            stopped = True
        self.state_file.unlink(missing_ok=True)
        return {"phase": "stopped", "stopped": stopped, "pid": pid,
                "note": ("Раннер остановлен — локальные модели снова считаются в сервере."
                         if stopped else "Раннер и не был поднят.")}
