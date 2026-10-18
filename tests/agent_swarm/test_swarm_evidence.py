#!/usr/bin/env python3
"""tests/agent_swarm/test_swarm_evidence.py — упавший паттерн роя печатает строку провала и улики сервера.

Сервер-двойник принимает запрос, пишет в консоль и журнал фактов и молчит дольше срока клиента: паттерн падает
ReadTimeout. Истина — то, что двойник записал до паттерна и во время него: улики называют второе и не тянут первое.
"""
import contextlib
import io
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

# Рой — по голому имени (каталог набора не пакет); корень проекта в путях кладёт сам рой.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_agent_swarm as swarm

from tests.harness.console import Console
from tests.harness.jsonrpc import JsonRpc

_checks = 0
_fails: list[str] = []


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def silent_server() -> tuple[SimpleNamespace, threading.Event]:
    """Двойник живого сервера: консоль — канал, журнал фактов — файл; запрос принят, ответа нет, пока не отпустят."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    read_fd, write_fd = os.pipe()
    said = os.fdopen(write_fd, "w", encoding="utf-8", buffering=1)
    journal = Path(tempfile.mkdtemp(prefix="swarm_evidence_")) / "session.md"
    said.write("старт: двойник слушает\n")
    journal.write_text("## до паттерна\n", encoding="utf-8")
    release = threading.Event()

    def serve() -> None:
        conn, _ = listener.accept()
        conn.recv(65536)
        said.write("получен tools/call structure_create — ответа не будет\n")
        with journal.open("a", encoding="utf-8") as log:
            log.write("## во время паттерна: вызов принят, обработчик стоит\n")
        release.wait()
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    srv = SimpleNamespace(console=Console(os.fdopen(read_fd, encoding="utf-8")), port=port,
                          journals=SimpleNamespace(session=SimpleNamespace(path=journal)),
                          rpc=JsonRpc(f"http://127.0.0.1:{port}/mcp", "t", timeout=0.5))
    srv.console.wait_for("старт", timeout=5)
    return srv, release


def ran(pattern, srv) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        swarm.run_pattern(pattern, swarm.Ctx(srv), "implemented")
    return out.getvalue()


print("== Паттерн упал: строка провала и улики сервера за время паттерна ==")
srv, release = silent_server()
said = ran(swarm.honest_novice, srv)
release.set()
lines = said.splitlines()
check("строка провала со знаком провала — её видит test_suites, рядом исключение",
      any("✗" in line and "honest_novice" in line and "ReadTimeout" in line for line in lines), said)
check("консоль сервера за время паттерна напечатана, до паттерна — нет",
      any("консоль сервера │ получен tools/call" in line for line in lines)
      and not any("консоль сервера │ старт" in line for line in lines), said)
check("журнал фактов за время паттерна напечатан, до паттерна — нет",
      any("журнал фактов │ ## во время паттерна" in line for line in lines)
      and not any("до паттерна" in line for line in lines), said)
check("вердикт учтён как СЛОМАН", swarm._results[-1][:2] == ("СЛОМАН", "honest_novice"), swarm._results[-1:])

print("== Законный: паттерн прошёл — улик нет ==")
srv, release = silent_server()
said = ran(lambda ctx: (True, "успех"), srv)
release.set()
check("прошедший паттерн не печатает ни консоль, ни журнал",
      "консоль сервера │" not in said and "журнал фактов │" not in said and "✗" not in said, said)

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
