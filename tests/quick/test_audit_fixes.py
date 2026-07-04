"""
tests/quick/test_audit_fixes.py — Регрессия по исправлениям дефектов (D1–D13)

## Что тестируем
Эмпирически подтверждаем, что дефекты закрыты и не вернулись:
D1 path traversal, D2 загрузка firewall.yaml, D4 реестр реакций,
D5 валидация схемы, D6 rate-limit бан-после-порога, D8 аномалии по имени
инструмента, D9 uuid4-ID, D13 lifecycle (нотификации/версия/форма tools/list).

## Зачем нужен
Тест фиксирует дефекты как регрессию: если кто-то откатит фикс — тест покраснеет.

## Тип теста
Security / Unit (in-process, без поднятия сервера)

## Запуск
    python3 tests/quick/test_audit_fixes.py
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import server as S
from core.firewall import Firewall, FirewallRequest
from core.ids import IDGenerator

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {('- ' + str(detail)) if detail else ''}")


async def main():
    engine, transport, firewall = S.create_server()

    # D1: path traversal заблокирован, легитимный путь работает
    r = await engine.call("fs_read_file", {"path": "../../../../../etc/passwd"})
    check("D1 traversal /etc/passwd blocked", r.status == "error" and r.error.code == "PATH_ESCAPE",
          r.error.code if r.error else "")
    r2 = await engine.call("fs_read_file", {"path": "../server.py"})
    check("D1 traversal ../server.py blocked", r2.status == "error" and r2.error.code == "PATH_ESCAPE")
    await engine.call("fs_create_file", {"path": "ok/inside.txt", "content": "hi"})
    r3 = await engine.call("fs_read_file", {"path": "ok/inside.txt"})
    check("D1 legit path inside workspace works", r3.status == "success" and r3.data.get("content") == "hi")

    # D2: firewall.yaml реально загружен
    check("D2 firewall.yaml loaded (max_requests=60)", firewall.rate_limiter.max_requests == 60,
          firewall.rate_limiter.max_requests)
    check("D2 firewall.yaml loaded (ban_after=3)", firewall.rate_limiter.ban_after == 3,
          firewall.rate_limiter.ban_after)
    check("D2 injection patterns from yaml (>0)", len(firewall.injection_detector.patterns) > 0,
          len(firewall.injection_detector.patterns))

    # D5: валидация схемы (отсутствие required)
    r_req = await engine.call("fs_read_file", {})
    check("D5 missing required -> VALIDATION_ERROR", r_req.status == "error" and r_req.error.code == "VALIDATION_ERROR",
          r_req.error.code if r_req.error else "")

    # D4: реестр реакций подключён
    check("D4 reactions wired (recovery present)",
          r_req.error.recovery is not None and bool(r_req.error.recovery.reason))
    rtn = await engine.call("nope_tool", {})
    check("D4 TOOL_NOT_FOUND via registry",
          rtn.status == "error" and rtn.error.code == "TOOL_NOT_FOUND"
          and "инструмент" in (rtn.error.recovery.reason.lower() if rtn.error.recovery else ""))

    # D6: бан только после ban_after нарушений
    fw = Firewall({"rate_limit": {"max_requests_per_minute": 2, "ban_after_violations": 3}})
    ip = "203.0.113.9"
    decs, banned_trace = [], []
    for i in range(6):
        res = fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=1000.0 + i))
        decs.append(res.decision.value)
        banned_trace.append(fw.ip_blocklist.is_blocked(ip))
    check("D6 first breach = rate_limit, NOT banned", decs[2] == "rate_limit" and banned_trace[2] is False,
          f"{decs} banned@2={banned_trace[2]}")
    check("D6 ban only after ban_after=3 violations", banned_trace[-1] is True and banned_trace[3] is False,
          banned_trace)

    # D8: аномалии по имени инструмента
    fw2 = Firewall({"anomaly_detection": {"max_methods_per_window": 3}})
    ad_hit = False
    for i in range(6):
        res = fw2.check(FirewallRequest(ip="198.51.100.1", method="tools/call",
                                        params={"name": f"tool_{i}"}, timestamp=2000.0 + i))
        if res.decision.value == "block":
            ad_hit = True
    check("D8 anomaly fires on many distinct tool names", ad_hit)
    resd = Firewall({}).check(FirewallRequest(ip="198.51.100.2", method="tools/call",
                                              params={"name": "fs_delete"}, timestamp=3000.0))
    check("D8 dangerous tool flagged", resd.decision.value == "block", resd.reason)

    # D9: uuid4 (32 hex), без коллизий
    g = IDGenerator()
    vid = g.generate("video")
    uniq = vid.split("_", 1)[1]
    check("D9 id unique part = 32 hex (uuid4)", len(uniq) == 32, f"{vid} len={len(uniq)}")
    check("D9 is_valid_format accepts new id", g.is_valid_format(vid))
    ids = {g.generate("scene") for _ in range(20000)}
    check("D9 no collisions in 20k ids", len(ids) == 20000, len(ids))

    # D13: lifecycle
    resp = await transport.handle_request(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    check("D13 notifications/initialized -> no response (202)", resp is None, repr(resp))
    ri = json.loads(await transport.handle_request(json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})))
    check("D13 initialize negotiates client version", ri["result"]["protocolVersion"] == "2025-06-18",
          ri["result"]["protocolVersion"])
    ro = json.loads(await transport.handle_request(json.dumps(
        {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})))
    check("D13 initialize falls back to latest for unknown", ro["result"]["protocolVersion"] == "2025-06-18",
          ro["result"]["protocolVersion"])
    tl = json.loads(await transport.handle_request(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})))
    tools = tl["result"]["tools"]
    ok_shape = all({"name", "description", "inputSchema"}.issubset(t.keys()) for t in tools)
    check("D13 tools/list shape ok (name/description/inputSchema)", ok_shape and len(tools) > 0, f"{len(tools)} tools")

    print()
    passed = sum(results)
    total = len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
