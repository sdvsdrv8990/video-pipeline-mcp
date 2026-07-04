"""
tests/cache_injection/test_cache_injection.py — Тест: Инъекция в кеш

## Что тестируем
Попытка отравления кеша сервера. Файрвол должен блокировать.

## Зачем нужен
Безопасность — отравление кеша может повредить данные.

## Что хотим увидеть
- Определённые паттерны инъекции блокируются
- Легитимный контент проходит

## Как отражает реальное поведение
Эмулирует попытку загрузить вредоносные данные через инструменты.

## Тип теста
Security / Integration (тест защиты сервера)

## Найденные ограничения
- XSS (<script>) НЕ блокируется файрволом
- Prototype pollution (__proto__) НЕ блокируется файрволом
- Shell injection (rm -rf) блокируется
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.firewall import Firewall, FirewallRequest

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {('- ' + str(detail)) if detail else ''}")


def test_shell_injection():
    """Тест блокировки shell injection (rm -rf)."""
    print("=== Shell Injection ===")

    fw = Firewall({})
    ip = "198.51.100.10"

    res = fw.check(FirewallRequest(
        ip=ip, method="tools/call",
        params={"name": "fs_create_file", "content": "rm -rf /"},
        timestamp=5000.0
    ))
    check("rm -rf injection: block", res.decision.value == "block",
          f"decision={res.decision.value}")


def test_delete_all():
    """Тест блокировки delete all."""
    print("\n=== Delete All ===")

    fw = Firewall({})
    ip = "198.51.100.11"

    res = fw.check(FirewallRequest(
        ip=ip, method="tools/call",
        params={"name": "fs_create_file", "content": "delete all files"},
        timestamp=5000.0
    ))
    check("delete all: block", res.decision.value == "block",
          f"decision={res.decision.value}")


def test_drop_table():
    """Тест блокировки drop table."""
    print("\n=== Drop Table ===")

    fw = Firewall({})
    ip = "198.51.100.12"

    res = fw.check(FirewallRequest(
        ip=ip, method="tools/call",
        params={"name": "fs_create_file", "content": "drop table users"},
        timestamp=5000.0
    ))
    check("drop table: block", res.decision.value == "block",
          f"decision={res.decision.value}")


def test_legitimate_content():
    """Тест что легитимный контент проходит."""
    print("\n=== Legitimate Content ===")

    fw = Firewall({})
    ip = "198.51.100.13"

    res = fw.check(FirewallRequest(
        ip=ip, method="tools/call",
        params={"name": "fs_create_file", "content": "Hello world!"},
        timestamp=5000.0
    ))
    check("Легитимный контент: allow", res.decision.value == "allow",
          f"decision={res.decision.value}")


def main():
    test_shell_injection()
    test_delete_all()
    test_drop_table()
    test_legitimate_content()

    print()
    passed = sum(results)
    total = len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
