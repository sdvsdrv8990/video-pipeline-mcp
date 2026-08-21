"""
tests/config_change/test_config_change.py — сборка сервера на объявленной конфигурации.

## Назначение
`create_server()` отдаёт движок, транспорт и файрвол; инструменты зарегистрированы,
пороги файрвола пришли из конфига, а не остались нулями.
"""

import asyncio
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Область задаётся ДО импорта server: он резолвит её на импорте, и без этого набор
# создавал бы каталоги в боевой рабочей области владельца.
os.environ.setdefault("MCP_WORKSPACE", tempfile.mkdtemp(prefix="cfgchange_ws_"))

from server import create_server
from tests.harness import live_server

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {('- ' + str(detail)) if detail else ''}")


async def test_server_initialization():
    """Тест инициализации сервера с дефолтными настройками."""
    print("=== Server Initialization ===")

    engine, transport, firewall = create_server()

    check("Engine создан", engine is not None)
    check("Transport создан", transport is not None)
    check("Firewall создан", firewall is not None)
    check("Инструменты зарегистрированы", len(engine.tools) > 0, f"tools={len(engine.tools)}")


async def test_tool_availability():
    """Тест доступности инструментов после инициализации."""
    print("\n=== Tool Availability ===")

    engine, transport, firewall = create_server()

    expected_tools = ["fs_get_directory_tree", "fs_read_file", "fs_create_file", "json_read_snapshot"]
    for tool_name in expected_tools:
        check(f"Инструмент '{tool_name}' доступен", engine.has_tool(tool_name))


async def test_firewall_config():
    """Тест загрузки конфигурации файрвола."""
    print("\n=== Firewall Config ===")

    engine, transport, firewall = create_server()

    check("Firewall загружен", firewall is not None)
    check("Rate limiter настроен", firewall.rate_limiter is not None)
    check("Max requests > 0", firewall.rate_limiter.max_requests > 0,
          f"max_requests={firewall.rate_limiter.max_requests}")


async def test_hot_reload_rejects_broken_config():
    """Смена конфига на живом сервере — битый тип не должен уезжать в правила."""
    print("\n=== Hot-reload: битый конфиг ===")

    tmp = Path(tempfile.mkdtemp(prefix="cfgchange_"))
    shutil.copytree(ROOT / "config", tmp / "config")
    cfg = tmp / "config" / "firewall.yaml"

    with live_server(env={"MCP_CONFIG": str(tmp / "config")}) as srv:
        check("до правки сервер отвечает", srv.rpc.request("tools/list", {}).status_code == 200)

        text = cfg.read_text(encoding="utf-8")
        broken = re.sub(r"max_requests_per_minute:\s*\d+", 'max_requests_per_minute: "60 "', text, count=1)
        check("якорь порога найден", broken != text)
        cfg.write_text(broken, encoding="utf-8")

        for _ in range(20):
            time.sleep(2)
            if "[config]" in srv.console.text:
                break
        said = [ln for ln in srv.console.text.splitlines() if "[config]" in ln]
        # Успех перезагрузки здесь = битые правила приняты: дальше падает сравнение int со строкой,
        # и сервер отвечает отказом на КАЖДЫЙ запрос, объявив «перезагружен без рестарта».
        check("сервер объявил отказ применить, а не успех", any("НЕ применён" in ln for ln in said), said)

        resp = srv.rpc.request("tools/list", {})
        check("после битого конфига сервер продолжает обслуживать", resp.status_code == 200,
              resp.text[:120])
        check("текст исключения не уезжает клиенту", "not supported between" not in resp.text)


async def main():
    await test_server_initialization()
    await test_tool_availability()
    await test_firewall_config()
    await test_hot_reload_rejects_broken_config()

    print()
    passed = sum(results)
    total = len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
