"""tests/harness/inprocess.py — сервер, собранный в процессе набора: та же сборка, что у `server.py`, но журналы —
во временном корне, если MCP_LOGS не задан явно. Набор, запущенный руками, не пишет в журналы владельца.

Единственная дверь наборов к `server.create_server`: сборку мимо неё ловит гейт (`tests/test_suites.py`).
Живой сервер во внешнем процессе получает свой корень в `live_server.py` — там журналы ищут по серверу, здесь их
никто не читает.
"""

import os
import tempfile

import server


def create_server():
    """`server.create_server()` с корнем журналов из MCP_LOGS или временным."""
    server.LOGS_OVERRIDE = os.environ.get("MCP_LOGS") or tempfile.mkdtemp(prefix="vpm_logs_")
    return server.create_server()
