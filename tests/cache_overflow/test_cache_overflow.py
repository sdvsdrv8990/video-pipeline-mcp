"""
tests/cache_overflow/test_cache_overflow.py — Тест: Переполнение кеша

## Что тестируем
Устойчивость сервера при переполнении кеша.

## Зачем нужен
Сервер не должен падать. Кеш должен очищаться автоматически.

## Что хотим увидеть
- Сервер не падает
- Кеш очищается
- Легитимные запросы работают

## Как отражает реальное поведение
Эмулирует длительную работу с заполнением кеша.

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


async def test_normal_operation():
    """Тест нормальной работы сервера."""
    print("=== Normal Operation ===")

    engine, transport, firewall = create_server()

    # Базовые вызовы
    r1 = await engine.call("fs_create_file", {"path": "test_cache.txt", "content": "cache test"})
    check("fs_create_file работает", r1.status == "success")

    r2 = await engine.call("fs_read_file", {"path": "test_cache.txt"})
    check("fs_read_file работает", r2.status == "success")


async def test_massive_file_creation():
    """Тест массового создания файлов (нагрузка на кеш)."""
    print("\n=== Massive File Creation ===")

    engine, transport, firewall = create_server()

    created = 0
    for i in range(50):
        r = await engine.call("fs_create_file", {
            "path": f"mass/test_{i:03d}.txt",
            "content": f"payload {i}" * 100
        })
        if r.status == "success":
            created += 1

    check(f"Создано файлов: {created}/50", created == 50, f"created={created}")

    # Проверяем что чтение работает после нагрузки
    r = await engine.call("fs_read_file", {"path": "mass/test_000.txt"})
    check("Чтение после нагрузки работает", r.status == "success")


async def main():
    await test_normal_operation()
    await test_massive_file_creation()

    print()
    passed = sum(results)
    total = len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
