#!/usr/bin/env python3
"""tests/quick/test_journal_door.py — причина, на которую ответ клиенту отсылает номером события, лежит в журнале сервера.

Ответ инструмента говорит «причина в журнале сервера, событие N»; истина — файл журнала фактов на диске под корнем
журналов набора, а не логгер в памяти: тест, который вешает свой обработчик на логгер, не видит, что у сервера записи
никуда не ложатся (CI dce0461, П5). Причина подаётся там, где рождается, — в обработчике инструмента; следствие
проверяется на двери — в файле.
"""
import asyncio
import os
import re
import sys
import threading
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import server
from core.contracts import ContractError, ToolResult
from tests.harness.inprocess import create_server

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


async def _crash(**_):
    raise RuntimeError("падение-в-глубине")


async def _refuse(**_):
    raise ContractError("PROVIDER_FAILED", "провайдер отказал") from RuntimeError("чужая-причина-отказа")


async def _deprecated(**_):
    warnings.warn("устаревший-вызов-зависимости", DeprecationWarning, stacklevel=1)
    return ToolResult(status="success", data={"ok": 1})


async def _quiet(**_):
    return ToolResult(status="success", data={"ok": 1})


def served():
    engine, _, _ = create_server()
    for name, handler in (("j_crash", _crash), ("j_refuse", _refuse), ("j_warn", _deprecated), ("j_quiet", _quiet)):
        engine.register(name, "событие журнала", {"type": "object", "properties": {}}, handler)
    return engine, Path(server.LOGS_OVERRIDE) / "session.md"


def journal(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


engine, session = served()

print("== больное: падение инструмента — номер события из ответа и трассировка в журнале ==")
said = asyncio.run(engine.call("j_crash", {}))
event = (re.search(r"событие (\w+)", said.error.message or "") or [None, ""])[1] if said.error else ""
text = journal(session)
check("ответ — INTERNAL_ERROR с номером события", said.error is not None and said.error.code == "INTERNAL_ERROR" and event,
      said.error and said.error.message)
check("номер события из ответа — в журнале на диске", bool(event) and event in text, session)
check("в журнале — причина с трассировкой", "падение-в-глубине" in text and "Traceback" in text)

print("== больное: отказ с чужой причиной — номер события фактом, причина в журнале ==")
said = asyncio.run(engine.call("j_refuse", {}))
event = (said.error.facts or {}).get("event", "") if said.error else ""
text = journal(session)
check("ответ — код отказа и номер события фактом", said.error is not None and said.error.code == "PROVIDER_FAILED" and event)
check("номер события и чужая причина — в журнале", bool(event) and event in text and "чужая-причина-отказа" in text)

print("== больное: предупреждение устаревания в пути исполнения — в журнале, а не в пустоте ==")
asyncio.run(engine.call("j_warn", {}))
check("DeprecationWarning зависимости — в журнале", "устаревший-вызов-зависимости" in journal(session))

print("== больное: фоновый поток упал мимо узла границы — сетка кладёт причину в журнал ==")
_runaway = threading.Thread(target=lambda: (_ for _ in ()).throw(RuntimeError("поток-упал-молча")), name="j-поток")
_runaway.start()
_runaway.join()
text = journal(session)
check("исключение потока — в журнале с именем потока и трассировкой",
      "поток-упал-молча" in text and "j-поток" in text and "Traceback" in text)

print("== законное: тихий успех не добавляет записей logging ==")
before = journal(session)
said = asyncio.run(engine.call("j_quiet", {}))
added = journal(session)[len(before):]
check("успех без предупреждений — ни одной записи уровня", said.status == "success"
      and not re.search(r"^## \[.*\] (DEBUG|INFO|WARNING|ERROR|CRITICAL) ", added, re.M), added[:200])

print("== повторная сборка сервера в том же процессе — одна запись на событие, а не две ==")
os.environ["MCP_LOGS"] = str(session.parent)      # тот же корень: оставшийся прежний обработчик писал бы туда же
engine, session = served()
said = asyncio.run(engine.call("j_refuse", {}))
event = (said.error.facts or {}).get("event", "") if said.error else ""
records = journal(session).count(f"- **событие:** {event}\n") if event else 0
check("после второй сборки событие записано ровно одной записью", records == 1, records)
del os.environ["MCP_LOGS"]

print("== разрушительное: журнал не пишется — клиент всё равно получает свой код, а не падение ==")
broken = session.parent / "сломанный"
broken.mkdir()
(broken / "session.md").mkdir()          # на месте файла журнала — каталог: запись невозможна на любой ОС
os.environ["MCP_LOGS"] = str(broken)
engine_b, _, _ = create_server()
del os.environ["MCP_LOGS"]
engine_b.register("j_refuse", "событие журнала", {"type": "object", "properties": {}}, _refuse)
said = asyncio.run(engine_b.call("j_refuse", {}))
check("сбой журнала не превратил отказ в падение", said.error is not None and said.error.code == "PROVIDER_FAILED",
      said.error and said.error.code)

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
