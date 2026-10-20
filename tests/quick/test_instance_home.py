#!/usr/bin/env python3
"""tests/quick/test_instance_home.py — дом инстанса (SA-10): личность и ключ шифрования сервера набора живут в его доме, а
не в корне проекта, где лежит личность владельца.

Истина об открытиях — аудит-хук `open` этого процесса (только пути файлов личности, без содержимого); дом живого сервера —
ключ, рождённый в его временном корне. У владельца без переменных дом — родитель каталога деклараций, как до узла.
"""
import asyncio
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import server
from core.declaration import declarations_root, instance_home, workspace_root
from core.engine import Engine
from core.ids import IDGenerator
from core.ids.link_registry import REGISTRY_FILE
from core.integrity import KEY_FILE, InstanceIdentity
from core.secrets import BOX_KEY_FILE, fingerprint
from core.state import StateManager
from tests.harness.inprocess import create_server, register_basic_tools
from tests.harness.live_server import live_server

_checks = 0
_fails: list[str] = []
_opened: list[Path] = []


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def _audit(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        path = os.fsdecode(args[0])
        if os.path.basename(path) in (KEY_FILE, BOX_KEY_FILE):
            _opened.append(Path(os.path.realpath(path)))


sys.addaudithook(_audit)

print("== 1. Узел: у владельца дом — родитель каталога деклараций, MCP_HOME главнее ==")
check("без переменных дом — корень проекта, родитель config/ (поведение владельца то же, что до узла)",
      instance_home(declarations_root({}), {}) == ROOT.resolve(), instance_home(declarations_root({}), {}))
check("копия деклараций без MCP_HOME — дом у копии, как у инструментов прежде",
      instance_home(Path("/копия/config"), {}) == Path("/копия").resolve())
check("MCP_HOME главнее каталога деклараций",
      instance_home(ROOT / "config", {"MCP_HOME": "/дом-набора"}) == Path("/дом-набора").resolve())
check("без переменных рабочая область — workspace/ рядом с кодом; MCP_WORKSPACE главнее",
      workspace_root({}) == (ROOT / "workspace").resolve()
      and workspace_root({"MCP_WORKSPACE": "/рабочая"}) == Path("/рабочая").resolve())
check("дом и рабочая область независимы: задан один — другой берёт своё умолчание",
      workspace_root({"MCP_HOME": "/дом-набора"}) == (ROOT / "workspace").resolve()
      and instance_home(ROOT / "config", {"MCP_WORKSPACE": "/рабочая"}) == ROOT.resolve())

print("== 2. Дверь наборов: сборка в процессе выпускает и читает личность в доме набора ==")
os.environ.pop("MCP_HOME", None)                   # прямой запуск: дом даёт дверь, а не гейт
_ws = Path(tempfile.mkdtemp(prefix="home_ws_")) / "workspace"
atexit.register(shutil.rmtree, _ws.parent, ignore_errors=True)
_ws.mkdir(parents=True)
_sm = StateManager(_ws)
register_basic_tools(Engine(state_manager=_sm), IDGenerator(), _sm)
home = Path(os.environ.get("MCP_HOME", "")).resolve()
check("дверь дала свой дом вне корня проекта", os.environ.get("MCP_HOME") and not home.is_relative_to(ROOT.resolve()),
      os.environ.get("MCP_HOME"))
check("ключ подписи рождён в доме набора", (home / KEY_FILE).is_file(), sorted(p.name for p in home.iterdir()))
owner = [str(p) for p in _opened if p.is_relative_to(ROOT.resolve())]
check("личность в корне проекта не открыта ни разу; открытия — только в доме набора",
      _opened and not owner and all(p.is_relative_to(home) for p in _opened), (owner, _opened))

print("== 3. Живой сервер набора: свой дом в своём временном корне ==")
with live_server() as srv:
    born = (srv.workspace.parent / KEY_FILE).is_file()
check("ключ подписи живого сервера рождён в его временном корне (MCP_HOME харнесса)", born, srv.workspace.parent)

print("== 4. Ключ, внесённый скриптом владельца, читает сервер того же дома и той же рабочей области ==")
_ROW = {"resource_type": "tts_characters", "provider": "ElevenLabs", "fallback_provider": "", "daily_limit": -1,
        "current_usage": 0, "warning_threshold": -1, "model": "eleven_multilingual_v2", "response_format": "mp3",
        "speed": 1.0, "usage_unit": "character"}
_KEY = "sk-проба-ключа-владельца"
with live_server() as srv:
    srv.write("ключи/read.json", json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {"L1": _ROW}}}, ensure_ascii=False))
    _script = [sys.executable, str(ROOT / "scripts" / "set_provider_key.py"), "--channel", "ключи",
               "--provider", "ElevenLabs", "--stdin"]
    _env = {**os.environ, "MCP_HOME": str(srv.workspace.parent), "MCP_WORKSPACE": str(srv.workspace)}
    # Новая сессия — без терминала того, кто запустил набор: регресс ввода не повиснет на его терминале.
    entered = subprocess.run(_script, input=_KEY + "\n", cwd=ROOT, capture_output=True, text=True, env=_env,
                             start_new_session=True)
    status = srv.rpc.call_tool("media_provider_status", {"table": "ключи", "resource_type": "tts_characters"})["data"]
    if os.name == "posix":
        # Живой терминал без ввода — как консоль Windows: байт на нём значит, что скрипт ждёт терминал, а не stdin.
        import pty
        import selectors
        import threading
        _master, _slave = pty.openpty()      # свой конец держим до конца: без него чтение мастера — EIO раньше ребёнка
        _lead = ("import fcntl, os, sys, termios; os.setsid(); fd = os.open(sys.argv[1], os.O_RDWR); "
                 "fcntl.ioctl(fd, termios.TIOCSCTTY, 0); os.execv(sys.executable, [sys.executable, *sys.argv[2:]])")
        _err = tempfile.TemporaryFile()      # stderr ребёнка — в отчёт проверки: CI dcb0c41 на macOS висел без улики
        _child = subprocess.Popen([sys.executable, "-c", _lead, os.ttyname(_slave), *_script[1:]], cwd=ROOT, env=_env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=_err)
        _child.stdin.write((_KEY + "\n").encode())
        _child.stdin.close()
        # Первое из событий: байт на терминале, конец вывода ребёнка или его выход — вывод может держать открытым и не он
        # (CI dcb0c41: на macOS SelectSelector ждал 900 с без EOF, диагноз «kqueue, bpo-32517» синка 7 не подтвердился).
        _wake_r, _wake_w = os.pipe()
        threading.Thread(target=lambda: (_child.wait(), os.write(_wake_w, b"x")), daemon=True).start()
        _tty, _outb, _ended = b"", b"", ""
        with selectors.SelectSelector() as _sel:
            _sel.register(_master, selectors.EVENT_READ, "tty")
            _sel.register(_child.stdout, selectors.EVENT_READ, "out")
            _sel.register(_wake_r, selectors.EVENT_READ, "exit")
            while not _tty and not _ended:
                _ready = {key.data for key, _ in _sel.select()}
                if "tty" in _ready:
                    _tty = os.read(_master, 1024)
                elif "out" in _ready:
                    if not (_chunk := os.read(_child.stdout.fileno(), 4096)):
                        _ended = "конец вывода ребёнка"
                    _outb += _chunk
                else:
                    _ended = "ребёнок вышел, его вывод ещё открыт"
                    os.set_blocking(_child.stdout.fileno(), False)
                    try:
                        while _chunk := os.read(_child.stdout.fileno(), 4096):
                            _outb += _chunk
                    except BlockingIOError:
                        pass
        print(f"  · ожидание pty кончилось: {_ended or 'байт терминала'}")
        if _tty:
            _child.kill()
        _child.wait()
        _child.stdout.close()
        _out = _outb.decode(errors="replace")
        _err.seek(0)
        _out_err = _err.read().decode(errors="replace")
        _err.close()
        os.close(_wake_r)
        os.close(_wake_w)
        os.close(_slave)
        os.close(_master)
key = ((status.get("resolved") or [{}])[0]).get("key") or {}
check("скрипт ключей принял ключ из stdin по флагу", entered.returncode == 0 and "сохранён" in entered.stdout,
      (entered.returncode, entered.stdout[-200:], entered.stderr[-200:]))
check("сервер видит ключ, внесённый скриптом: тот же ключ шифрования (дом) и тот же канал (рабочая область)",
      key.get("present") is True and key.get("fingerprint") == fingerprint(_KEY) and not key.get("unreadable"), key)
if os.name == "posix":
    check("ключ по флагу --stdin при живом терминале: терминал не тронут, ключ сохранён",
          not _tty and _child.returncode == 0 and "сохранён" in _out,
          (_tty[:80], _child.returncode, _ended, _out[-200:], _out_err[-300:]))
    # Флаг при stdin-терминале: набранный ключ печатался бы эхом — отказ до чтения, в доме ничего не появляется.
    import pty
    _home4b = Path(tempfile.mkdtemp(prefix="stdin_tty_"))
    atexit.register(shutil.rmtree, _home4b, ignore_errors=True)
    _m4b, _s4b = pty.openpty()
    os.write(_m4b, b"\n")   # строка уже в очереди терминала: чтение ключа с него кончается сразу, а не ждёт ввода
    _echo = subprocess.run(_script, stdin=_s4b, cwd=ROOT, capture_output=True, text=True,
                           env={**os.environ, "MCP_HOME": str(_home4b), "MCP_WORKSPACE": str(_home4b)})
    os.close(_s4b)
    os.close(_m4b)
    check("--stdin со stdin-терминалом — отказ кодом 2 до чтения ключа, в доме ничего",
          _echo.returncode == 2 and "--stdin" in _echo.stdout and not any(_home4b.iterdir()),
          (_echo.returncode, _echo.stdout[-160:], sorted(p.name for p in _home4b.iterdir())))
else:
    print("  ── не судится: в ОС нет pty — живой терминал без ввода поднять нечем")

print("== 5. --re-adopt присваивает рабочую область личности своего дома ==")
_home5 = Path(tempfile.mkdtemp(prefix="readopt_home_"))
atexit.register(shutil.rmtree, _home5, ignore_errors=True)      # в доме — закрытый ключ прогона
_ws5 = _home5 / "workspace"
_ws5.mkdir()
adopted = subprocess.run([sys.executable, str(ROOT / "server.py"), "--re-adopt"], cwd=ROOT, capture_output=True, text=True,
                         env={**os.environ, "MCP_HOME": str(_home5), "MCP_WORKSPACE": str(_ws5),
                              "MCP_LOGS": str(_home5 / "logs")})
registry = json.loads((_ws5 / REGISTRY_FILE).read_text(encoding="utf-8")) if (_ws5 / REGISTRY_FILE).is_file() else {}
own = InstanceIdentity(_home5, _ws5)
own.ensure_key()
check("--re-adopt подписал реестр рабочей области ключом своего дома",
      adopted.returncode == 0 and registry.get("_seal")
      and own.verify({"entities": registry.get("entities", {})}, registry["_seal"]) == "",
      (adopted.returncode, adopted.stderr[-300:], registry.get("_seal")))

print("== 6. Дверь create_server: рабочая область набора, а не владельца ==")
# Оракул — файловая система: в workspace/ владельца нет ничего новее метки, запись легла под MCP_WORKSPACE набора.
os.environ.pop("MCP_WORKSPACE", None)              # прямой запуск: рабочую область даёт дверь
_mark = Path(tempfile.mkdtemp(prefix="ws_mark_"))
atexit.register(shutil.rmtree, _mark, ignore_errors=True)
_owner_ws = (ROOT / "workspace").resolve()
_engine, _, _ = create_server()
_wrote = asyncio.run(_engine.call("fs_write_file", {"path": "проба-двери/запись.txt", "content": "набор"}))
_suite_ws = Path(os.environ.get("MCP_WORKSPACE", "")).resolve()
_newer = sorted(str(p.relative_to(ROOT)) for p in (_owner_ws, *_owner_ws.rglob("*"))
                 if p.stat().st_mtime > _mark.stat().st_mtime) if _owner_ws.is_dir() else []
check("дверь дала рабочую область вне workspace/ владельца, и сервер пишет в неё",
      os.environ.get("MCP_WORKSPACE") and not _suite_ws.is_relative_to(_owner_ws)
      and server.WORKSPACE_PATH.resolve() == _suite_ws, (os.environ.get("MCP_WORKSPACE"), server.WORKSPACE_PATH))
check("запись инструмента легла в рабочую область набора",
      _wrote.status == "success" and (_suite_ws / "проба-двери" / "запись.txt").is_file(),
      _wrote.error.code if _wrote.error else "")
check("в workspace/ владельца ничего новее старта двери", not _newer and not (_owner_ws / "проба-двери").exists(),
      _newer[:5])

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
