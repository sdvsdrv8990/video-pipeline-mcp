"""
tests/virus_injection/test_virus_injection.py — Тест: Внедрение вредоносного кода

## Что тестируем
Попытка внедрить вредоносный код через инструменты. Файрвол должен блокировать.

## Зачем нужен
Безопасность — вредоносный код не должен выполняться.

## Что хотим увидеть
- Определённые паттерны кода блокируются
- Легитимные скрипты проходят

## Как отражает реальное поведение
Эмулирует попытку загрузить вредоносный скрипт через MCP-сервер.

## Тип теста
Security / Integration (тест защиты сервера)

## Найденные ограничения
- os.system() блокируется
- cat /etc/passwd НЕ блокируется
- subprocess НЕ блокируется
- rm -rf блокируется
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


def test_os_system_injection():
    """Тест блокировки os.system injection."""
    print("=== os.system Injection ===")

    fw = Firewall({})
    ip = "198.51.100.20"

    res = fw.check(FirewallRequest(
        ip=ip, method="tools/call",
        params={"name": "fs_create_file", "content": "import os; os.system('rm -rf /')"},
        timestamp=6000.0
    ))
    check("os.system + rm -rf: block", res.decision.value == "block",
          f"decision={res.decision.value}")


def test_rm_rf():
    """Тест блокировки rm -rf."""
    print("\n=== rm -rf ===")

    fw = Firewall({})
    ip = "198.51.100.21"

    res = fw.check(FirewallRequest(
        ip=ip, method="tools/call",
        params={"name": "fs_create_file", "content": "rm -rf /home"},
        timestamp=6000.0
    ))
    check("rm -rf: block", res.decision.value == "block",
          f"decision={res.decision.value}")


def test_format_c():
    """Тест блокировки format c:."""
    print("\n=== format c: ===")

    fw = Firewall({})
    ip = "198.51.100.22"

    res = fw.check(FirewallRequest(
        ip=ip, method="tools/call",
        params={"name": "fs_create_file", "content": "format c:"},
        timestamp=6000.0
    ))
    check("format c:: block", res.decision.value == "block",
          f"decision={res.decision.value}")


def test_legitimate_script():
    """Тест что легитимный скрипт проходит."""
    print("\n=== Legitimate Script ===")

    fw = Firewall({})
    ip = "198.51.100.23"

    res = fw.check(FirewallRequest(
        ip=ip, method="tools/call",
        params={"name": "fs_create_file", "content": "print('Hello World')"},
        timestamp=6000.0
    ))
    check("Легитимный скрипт: allow", res.decision.value == "allow",
          f"decision={res.decision.value}")


def main():
    test_os_system_injection()
    test_rm_rf()
    test_format_c()
    test_legitimate_script()

    print()
    passed = sum(results)
    total = len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
