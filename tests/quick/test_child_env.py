#!/usr/bin/env python3
"""tests/quick/test_child_env.py — ребёнок сервера: среда и вывод решаются одним узлом (`core/environment.child_spawn`).

Истина о выводе — байты, которые напечатал ребёнок; о среде — сам узел (а в разделах мест — ядро, `/proc/<pid>/environ`
живого двойника). Кодировка локали на Windows (cp1252) ломала вывод ребёнка, а наследство среды сервера несло ребёнку
его ключ доступа.
"""
import contextlib
import inspect
import os
import secrets
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.environment import SECRETS, child_spawn, child_text
from core.runner.supervisor import TOKEN_ENV, RunnerSupervisor

_checks = 0
_fails: list[str] = []
BROKEN = "import sys\nsys.stdout.buffer.write({payload!r})\n"
# Секреты сервера — истина теста, а не перечень узла: событие, взявшее их из SECRETS, не видит, что узел потерял имя.
SERVER_SECRETS = ("MCP_AUTH_TOKEN", "MCP_TUNNEL_TOKEN")


def windows_read(cmd: list[str], **spawn) -> object:
    """Вывод ребёнка так, как его читает `communicate` на Windows: поток-читатель `buffer.append(fh.read())` (CPython
    Lib/subprocess.py `_readerthread`, v3.12.10:1598) — отказ декодирования гаснет в потоке вместе с выводом."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **spawn)
    buffer: list = []
    reader = threading.Thread(target=lambda: buffer.append(proc.stdout.read()))
    reader.start()
    reader.join()
    proc.wait()
    proc.stdout.close()
    return buffer[0] if buffer else None


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def environ_of(pid: int) -> dict[str, str]:
    """Среда живого процесса глазами ядра."""
    pairs = (x.split(b"=", 1) for x in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if b"=" in x)
    return {k.decode(): v.decode() for k, v in pairs}


def restore(prior: dict[str, str | None]) -> None:
    for name, value in prior.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


print("== 1. Узел: среда без секретов, вывод в utf-8, обработка битых байтов — выбор места ==")
errors_param = inspect.signature(child_spawn).parameters["errors"]
check("errors обязателен — молчаливой замены по умолчанию нет", errors_param.default is inspect.Parameter.empty,
      errors_param.default)
args = child_spawn("strict", {"MCP_RUNNER_TOKEN": "свой"})
# Обновлено 2026-10-17 (Ч3 CI dce0461): strict отдаёт байты — на Windows текстовый strict гаснет в потоке-читателе.
check("узел даёт env; strict — байты (текст собирает вызывающий), replace — utf-8 с заменой; свой секрет кладёт выпускающий",
      "encoding" not in args and "errors" not in args
      and {k: v for k, v in child_spawn("replace").items() if k != "env"} == {"encoding": "utf-8", "errors": "replace"}
      and args["env"].get("MCP_RUNNER_TOKEN") == "свой" and args["env"].get("PYTHONUTF8") == "1",
      {k: v for k, v in args.items() if k != "env"})
check("двери-секреты узла называют секреты сервера", set(SERVER_SECRETS) <= SECRETS, sorted(SECRETS))
server = {name: f"секрет-{name}" for name in SERVER_SECRETS} | {"PATH": "/bin"}
leaked = sorted(set(child_spawn("replace", environ=server)["env"]) & set(SERVER_SECRETS))
check("секреты сервера в среду ребёнка не идут", not leaked, leaked)

print("== 2. Вывод ребёнка: разбираемое место отказывает, журнальное заменяет ==")
printer = [sys.executable, "-c", BROKEN.format(payload="привет ".encode() + bytes([0x81]))]
_strict = windows_read(printer, **child_spawn("strict"))
try:
    _decoded = child_text(_strict, "strict") if isinstance(_strict, bytes) else None
    check("strict: битый байт — громкий отказ у вызывающего, и при чтении как на Windows", False,
          "декодировано" if _decoded is not None else f"вывод пропал в потоке-читателе ({_strict!r})")
except UnicodeDecodeError:
    check("strict: битый байт — громкий отказ у вызывающего, и при чтении как на Windows", True)
said = subprocess.run(printer, capture_output=True, **child_spawn("replace")).stdout
check("replace: utf-8 цел, битый байт — U+FFFD", said == "привет �", repr(said))

print("== 3. Раннер: свой токен средой ребёнка, секретов сервера нет (ядро — оракул) ==")


class _Registry:
    config_file = Path("providers.yaml")
    config = {"local": {"runner": {"provider": "r", "stop_grace_sec": 5,
                                   "state_file": "state.json", "log_file": "runner.log"}},
              "online": {"http": {"r": {"url": "http://127.0.0.1:9/run"}}}}

    def need(self, *path, kind=None):
        node = self.config
        for key in path:
            node = node[key]
        return node


class _Pulse:
    """Подъём без узла заданий: ход — мимо, снятия нет."""
    cancelled = threading.Event()

    def __call__(self, progress=None, working=True):
        pass

    def on_cancel(self, hook):
        pass

    def refusal(self):
        return RuntimeError("снято")


# Обновлено 2026-10-18 (D02-e): готовность — знак в канал сервера тем же писателем, что у раннера (`ready_writer`), а не
# «вторая проба /health ответила»; `{before}` — что ребёнок делает до знака.
_READY = ("import os, signal, sys; sys.path.insert(0, sys.argv[1]); "
          "from core.runner.supervisor import READY_ENV, TOKEN_ENV, ready_sign, ready_writer; {before}"
          "ready_writer(os.environ[READY_ENV], ready_sign(os.environ[TOKEN_ENV]))(); signal.pause()")


class _Double(RunnerSupervisor):
    """Раннер-двойник: даёт знак готовности и ждёт сигнала; прозвонка без сети — «отвечает»."""

    def _command(self, mode, workspace):
        return [sys.executable, "-c", _READY.format(before=""), str(ROOT)]

    def _probe(self, token="", within=None):
        return {"ok": True}


if not Path("/proc/self/environ").exists():
    print("── не судится: в ОС нет /proc — среду ребёнка спросить не у кого")
else:
    server_secrets = {name: secrets.token_urlsafe(16) for name in SERVER_SECRETS}
    prior = {name: os.environ.get(name) for name in server_secrets}
    os.environ.update(server_secrets)
    with tempfile.TemporaryDirectory() as tmp:
        runner = _Double(_Registry(), tmp)
        try:
            runner.launch(Path(tmp) / "workspace", _Pulse())
            pid, issued = int(runner._read()["pid"]), runner.token()
            child = environ_of(pid)
        finally:
            runner.stop()
            restore(prior)
    check("ребёнок получил ровно выпущенный супервизором токен", bool(issued) and child.get(TOKEN_ENV) == issued,
          bool(child.get(TOKEN_ENV)))
    check("секретов сервера в среде раннера нет", not set(child) & set(server_secrets),
          sorted(set(child) & set(server_secrets)))
    check("раннер — в UTF-8 Mode", child.get("PYTHONUTF8") == "1")
    check("stop снял и подобрал раннера — в ядре его нет", not Path(f"/proc/{pid}").exists(), pid)

    # Упрямый раннер глушит SIGTERM: stop обязан снять его сам после срока, без вечного ожидания и без зомби.
    class _Stubborn(_Double):
        """Знак готовности ребёнок даёт ПОСЛЕ того, как заглушил SIGTERM: без этого stop успевал послать сигнал раньше
        заглушки, и «упрямый» умирал как обычный — событие не судило ничего."""

        def _command(self, mode, workspace):
            return [sys.executable, "-c", _READY.format(before="signal.signal(signal.SIGTERM, signal.SIG_IGN); "),
                    str(ROOT)]

    class _Grace(_Registry):
        config = {**_Registry.config, "local": {"runner": {**_Registry.config["local"]["runner"], "stop_grace_sec": 0.3}}}

    with tempfile.TemporaryDirectory() as tmp:
        stubborn = _Stubborn(_Grace(), tmp)
        try:
            stubborn.launch(Path(tmp) / "workspace", _Pulse())
            pid = int(stubborn._read()["pid"])
        finally:
            said = stubborn.stop()
    check("упрямый раннер (SIGTERM заглушён) снят по сроку и подобран — в ядре его нет",
          said["stopped"] and not Path(f"/proc/{pid}").exists(), (said, pid))
    with contextlib.suppress(OSError):
        os.kill(pid, 9)  # набор убирает своего двойника сам: сломанный stop (мутант) не оставит его жить сиротой

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
