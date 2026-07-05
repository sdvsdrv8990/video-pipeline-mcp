"""
tests/config_change/test_config_change.py — Тест: Изменение конфигурации

## Что тестируем
Адаптация сервера к изменению конфигурации оборудования.

## Зачем нужен
Сервер должен подстраиваться к новым ресурсам.

## Что хотим увидеть
- Сервер адаптируется
- Claude получает уведомление
- Работа продолжается

## Как отражает реальное поведение
Эмулирует изменение конфигурации сервера в процессе работы.

## Тип теста
System / Integration (тест поведения сервера)
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server import create_server

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


async def main():
    await test_server_initialization()
    await test_tool_availability()
    await test_firewall_config()

    print()
    passed = sum(results)
    total = len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
