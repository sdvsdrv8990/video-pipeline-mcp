#!/usr/bin/env python3
"""tests/quick/test_instance_home.py — дом инстанса (SA-10): личность и ключ шифрования сервера набора живут в его доме, а
не в корне проекта, где лежит личность владельца.

Истина об открытиях — аудит-хук `open` этого процесса (только пути файлов личности, без содержимого); дом живого сервера —
ключ, рождённый в его временном корне. У владельца без переменных дом — родитель каталога деклараций, как до узла.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.declaration import declarations_root, instance_home, workspace_root
from core.engine import Engine
from core.ids import IDGenerator
from core.ids.link_registry import REGISTRY_FILE
from core.integrity import KEY_FILE, InstanceIdentity
from core.secrets import BOX_KEY_FILE, fingerprint
from core.state import StateManager
from tests.harness.inprocess import register_basic_tools
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
    # Без управляющего терминала (новая сессия) getpass читает ввод из stdin.
    entered = subprocess.run([sys.executable, str(ROOT / "scripts" / "set_provider_key.py"), "--channel", "ключи",
                              "--provider", "ElevenLabs"], input=_KEY + "\n", cwd=ROOT, capture_output=True, text=True,
                             start_new_session=True,
                             env={**os.environ, "MCP_HOME": str(srv.workspace.parent), "MCP_WORKSPACE": str(srv.workspace)})
    status = srv.rpc.call_tool("media_provider_status", {"table": "ключи", "resource_type": "tts_characters"})["data"]
key = ((status.get("resolved") or [{}])[0]).get("key") or {}
check("скрипт ключей принял ключ без терминала", entered.returncode == 0 and "сохранён" in entered.stdout,
      (entered.returncode, entered.stdout[-200:], entered.stderr[-200:]))
check("сервер видит ключ, внесённый скриптом: тот же ключ шифрования (дом) и тот же канал (рабочая область)",
      key.get("present") is True and key.get("fingerprint") == fingerprint(_KEY) and not key.get("unreadable"), key)

print("== 5. --re-adopt присваивает рабочую область личности своего дома ==")
_home5 = Path(tempfile.mkdtemp(prefix="readopt_home_"))
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

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
