"""tests/harness/inprocess.py — сервер, собранный в процессе набора: та же сборка, что у `server.py`, но журналы, дом
инстанса и рабочая область — во временных корнях, если MCP_LOGS, MCP_HOME и MCP_WORKSPACE не заданы явно. Набор,
запущенный руками, не пишет в журналы и рабочую область владельца и не читает его личность.

Единственная дверь наборов к `server.create_server` и `server.register_basic_tools`: сборку мимо неё ловит гейт
(`tests/test_suites.py`).
Живой сервер во внешнем процессе получает свой корень в `live_server.py` — там журналы ищут по серверу, здесь их
никто не читает.
"""

import atexit
import os
import shutil
import tempfile

import server
from core.declaration import workspace_root


def create_server():
    """`server.create_server()` с корнем журналов из MCP_LOGS или временным, своим домом и своей рабочей областью."""
    if not os.environ.get("MCP_LOGS"):
        server.LOGS_OVERRIDE = tempfile.mkdtemp(prefix="vpm_logs_")
        atexit.register(shutil.rmtree, server.LOGS_OVERRIDE, ignore_errors=True)
    else:
        server.LOGS_OVERRIDE = os.environ["MCP_LOGS"]
    _own_home()
    _own_workspace()
    return server.create_server()


def register_basic_tools(engine, id_generator, state_manager):
    """`server.register_basic_tools` со своим домом инстанса: сборка контекста выпускает и читает личность в нём."""
    _own_home()
    return server.register_basic_tools(engine, id_generator, state_manager)


def _own_home() -> None:
    """Дом инстанса набора — MCP_HOME или временный, убирается при выходе процесса."""
    if not os.environ.get("MCP_HOME"):
        os.environ["MCP_HOME"] = tempfile.mkdtemp(prefix="vpm_home_")
        atexit.register(shutil.rmtree, os.environ["MCP_HOME"], ignore_errors=True)


def _own_workspace() -> None:
    """Рабочая область набора — MCP_WORKSPACE или временная, убирается при выходе процесса. Сервер считает свою при
    импорте (`server.WORKSPACE_PATH`), поэтому дверь ставит её и ему: тем же узлом `workspace_root`."""
    if not os.environ.get("MCP_WORKSPACE"):
        os.environ["MCP_WORKSPACE"] = tempfile.mkdtemp(prefix="vpm_ws_")
        atexit.register(shutil.rmtree, os.environ["MCP_WORKSPACE"], ignore_errors=True)
    server.WORKSPACE_PATH = workspace_root()
