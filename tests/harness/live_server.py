"""tests/harness/live_server.py — настоящий сервер под тестом: свой порт, своя рабочая область.

Поведенческий тест бьёт по живому серверу, а не по собранному в памяти движку: только так
проверяются транспорт, ключ, файрвол и то, что сервер СООБЩАЕТ в консоль. Рабочая область —
свежий временный каталог, иначе набор правил бы настоящие книги канала владельца и «зелёный тест»
стоил бы данных. Сервер поднимается С аутентификацией, ключ приходит переменной окружения, `.env`
проекта не трогается; калитка «без ключа» намеренно НЕ используется — иначе слой, который и надо
проверить, остался бы непроверенным.
"""

import atexit
import fnmatch
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from core.declaration import declarations_root
from core.observability import Logs

from .console import Console
from .jsonrpc import JsonRpc

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    """Порт, свободный ПРЯМО СЕЙЧАС. Фиксированный номер ронял бы набор при живом сервере рядом."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LiveServerError(RuntimeError):
    """Сервер не поднялся. С хвостом его консоли: причина всегда там, а не в нашей догадке."""


class LiveServer:
    """Живой `server.py` на своём порту. Контекстный менеджер — teardown гарантирован."""

    def __init__(self, env: dict | None = None, ready_timeout: float = 60.0, echo: bool = False):
        self.port = free_port()
        self.token = secrets.token_urlsafe(24)
        self.workspace = Path(tempfile.mkdtemp(prefix="live_ws_")) / "workspace"
        self.workspace.mkdir(parents=True)
        # Свой корень журналов: тестовый сервер не дописывает журнал владельца и не вытесняет его след.
        self.logs = self.workspace.parent / "logs"
        self.extra_env = env or {}
        self.ready_timeout = ready_timeout
        self.echo = echo
        self.proc: subprocess.Popen | None = None
        self.started = 0.0        # отметка старта: по ней сценарий отличает свою запись от прошлой
        self.console: Console | None = None
        self.rpc = JsonRpc(self.url, self.token)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    @property
    def journals(self) -> Logs:
        """Журналы этого сервера той же функцией, что строит их сам сервер: где его след и журнал фактов."""
        return Logs.from_config(declarations_root(self._env()), str(self.logs))

    def _env(self) -> dict:
        return {**os.environ,
                "MCP_HOST": "127.0.0.1", "MCP_PORT": str(self.port),
                "MCP_WORKSPACE": str(self.workspace),
                "MCP_LOGS": str(self.logs),
                # Свой дом инстанса: личность и ключ шифрования сервера набора, а не владельца.
                "MCP_HOME": str(self.workspace.parent),
                # Ключ через среду: `ensure_digest` берёт его первым и .env проекта не трогает.
                "MCP_AUTH_TOKEN": self.token,
                "PYTHONUNBUFFERED": "1",
                **self.extra_env}

    # ═══ Жизненный цикл ═══

    def _command(self) -> list[str]:
        """Команда запуска. При `VPM_SERVER_COVERAGE` — под покрытием: карта радиуса обязана мерить код,
        который исполняет ЗАПРОС (серверный процесс), а не процесс набора. `sigterm` в rc нужен потому,
        что сервер останавливается сигналом, и без него данные не сохранились бы вовсе."""
        script = [str(ROOT / "server.py"), "--port", str(self.port)]
        outdir = os.environ.get("VPM_SERVER_COVERAGE")
        if not outdir:
            return [sys.executable, *script]
        rc = Path(outdir) / "coveragerc"
        rc.parent.mkdir(parents=True, exist_ok=True)
        rc.write_text("[run]\nsource = core,tools,server\nparallel = True\nsigterm = True\n"
                      f"data_file = {Path(outdir) / '.coverage'}\n", encoding="utf-8")
        return [sys.executable, "-m", "coverage", "run", f"--rcfile={rc}", *script]

    def start(self) -> "LiveServer":
        self.started = time.time()
        self.proc = subprocess.Popen(
            self._command(),
            cwd=str(ROOT), env=self._env(), text=True, bufsize=1,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.console = Console(self.proc.stdout, echo=self.echo)
        self._await_ready()
        return self

    def _await_ready(self) -> None:
        """Готовность = сервер ОТВЕТИЛ на `tools/list`, а не «прошло N секунд» и не строка в логе.

        Строка в логе печатается до того, как поднят слушатель; таймер же врёт в обе стороны.
        Отвечающий протокол — единственный честный признак.
        """
        deadline = time.time() + self.ready_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                code = self.proc.returncode
                # Сигнал вместо кода выхода — сервер снят снаружи (обвязка его не снимала), а не отказал сам.
                cause = (f"снят сигналом {signal.Signals(-code).name} извне, а не кодом отказа сервера" if code < 0
                         else f"код {code}")
                raise LiveServerError(f"server.py завершился при старте ({cause}).\n{self.console.tail()}")
            try:
                if self.rpc.tools_list():
                    return
            except Exception:  # порт ещё не слушает
                pass
            time.sleep(0.1)
        self.stop()
        raise LiveServerError(f"Сервер не ответил за {self.ready_timeout} с.\n{self.console.tail()}")

    def stop(self) -> None:
        """Остановка и уборка. Гарантированная: временная область не должна пережить набор."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        shutil.rmtree(self.workspace.parent, ignore_errors=True)

    def __enter__(self) -> "LiveServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ═══ Удобства набора ═══

    def write(self, rel: str, content: str) -> Path:
        """Положить файл в рабочую область СЕРВЕРА — минуя инструменты (подготовка сцены)."""
        path = self.workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


def live_server(**kw) -> LiveServer:
    """`with live_server() as srv:` — сервер поднят, порт свой, область своя, ключ настоящий."""
    return LiveServer(**kw)


def declarations_copy(changes: dict[str, dict[str, object]] | None = None, root: Path | None = None) -> Path:
    """Копия деклараций проекта в `<root>/config` (инструменты считают корнем проекта родителя каталога; без `root` —
    свой временный), значения заменены по путям ключей (`spoil_slot`); боевой каталог не трогается. При выходе процесса
    убирается то, что положила копия: свой временный корень целиком, а в чужом `root` — только `config` (корень
    принадлежит вызывающему). Машинные декларации — их проект не ведёт (роль машины, туннель владельца) — в копию не
    идут: прогон не зависит от машины, на которой идёт."""
    source = declarations_root()
    copy = Path(root or tempfile.mkdtemp(prefix="vpm-decl-")) / "config"
    atexit.register(shutil.rmtree, copy.parent if root is None else copy, ignore_errors=True)
    shutil.copytree(source, copy, ignore=_machine_files(source))
    for name, values in (changes or {}).items():
        data = yaml.safe_load((copy / name).read_text(encoding="utf-8"))
        for key, value in values.items():
            parent, last = spoil_slot(data, key)
            parent[last] = value
        (copy / name).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return copy


def spoil_slot(data: object, key: str) -> tuple[dict | list, str | int]:
    """Где меняется значение по пути `a.b.0.c`: родитель и последний шаг. Шаг-число в списке — индекс; ключ словаря —
    самый длинный совпавший (ключ сам может нести точку: `media_provider_status.choice`); шага нет — KeyError с путём.
    Последний ключ словаря может быть новым: порча добавляет запись."""
    parts, node, at = key.split("."), data, 0
    while True:
        step, at = _step(node, parts, at, key)
        if at == len(parts):
            return node, step
        node = node[step]


def _step(node: object, parts: list[str], at: int, key: str) -> tuple[str | int, int]:
    if isinstance(node, list) and parts[at].isdigit() and int(parts[at]) < len(node):
        return int(parts[at]), at + 1
    if isinstance(node, dict):
        for end in range(len(parts), at, -1):
            if ".".join(parts[at:end]) in node:
                return ".".join(parts[at:end]), end
        if at == len(parts) - 1:
            return parts[at], at + 1
    raise KeyError(f"в пути `{key}` нет шага `{parts[at]}`")


def _machine_files(source: Path):
    """`copytree(ignore=…)` для файлов, которые проект не ведёт: их объявляет `.gitignore` корня проекта. Правила
    разбираются без git — копия для замера (`git archive`) репозитория не несёт, а `.gitignore` несёт. Файл вне корня
    проекта правилами проекта не судится."""
    rules = [_ignore_rule(line) for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    root = ROOT.resolve()

    def ignore(folder: str, names: list[str]) -> set[str]:
        out = set()
        for name in names:
            path = (Path(folder) / name).resolve()
            if path.is_relative_to(root) and _ignored(path.relative_to(root).as_posix(), path.is_dir(), rules):
                out.add(name)
        return out
    return ignore


def _ignore_rule(line: str) -> tuple[bool, bool, bool, str]:
    """Правило `.gitignore`: (отрицание `!`, только каталог — `/` в конце, от корня — `/` в начале или внутри, образец).
    Подмножество формата: `*` в образце проходит и через `/`, экранирование `\\` не разбирается."""
    negate = line.startswith("!")
    body = line[negate:].strip()
    return negate, body.endswith("/"), "/" in body.rstrip("/"), body.strip("/").replace("**", "*")


def _ignored(rel: str, is_dir: bool, rules: list[tuple[bool, bool, bool, str]]) -> bool:
    verdict = False
    for negate, dir_only, anchored, pattern in rules:
        if not (dir_only and not is_dir) and fnmatch.fnmatchcase(rel if anchored else rel.rsplit("/", 1)[-1], pattern):
            verdict = not negate
    return verdict
