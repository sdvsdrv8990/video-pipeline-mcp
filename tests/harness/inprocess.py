"""tests/harness/inprocess.py — сервер, собранный в процессе набора: та же сборка, что у `server.py`, но журналы и дом
инстанса — во временных корнях, если MCP_LOGS и MCP_HOME не заданы явно. Набор, запущенный руками, не пишет в журналы
владельца и не читает его личность.

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


def create_server():
    """`server.create_server()` с корнем журналов из MCP_LOGS или временным и своим домом инстанса."""
    server.LOGS_OVERRIDE = os.environ.get("MCP_LOGS") or tempfile.mkdtemp(prefix="vpm_logs_")
    _own_home()
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
