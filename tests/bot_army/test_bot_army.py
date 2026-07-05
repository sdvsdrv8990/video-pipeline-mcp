"""
tests/bot_army/test_bot_army.py — Тест: Армия ботов

## Что тестируем
Массовое подключение с подозрительным поведением. Файрвол должен банить.

## Зачем нужен
Безопасность — армия ботов реальная угроза. Проверяем rate limiting и ban.

## Что хотим увидеть
- Rate limiting срабатывает после max_requests
- Бан наступает после ban_after_violations
- Легитимные запросы проходят

## Как отражает реальное поведение
Эмулирует подключение множества ботов с одинаковыми payload'ами.

## Тип теста
Security / Integration (тест защиты сервера)
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


def test_rate_limiting():
    """Тест rate limiting: много запросов с одного IP."""
    print("=== Rate Limiting ===")

    fw = Firewall({"rate_limit": {"max_requests_per_minute": 5, "ban_after_violations": 3}})
    ip = "198.51.100.1"

    # Первые 5 запросов — проходят
    for i in range(5):
        res = fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=1000.0 + i))
        check(f"Запрос {i+1}: проходит", res.decision.value == "allow")

    # 6-й запрос — rate_limit (превышен лимит 5)
    res = fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=1000.0 + 5))
    check("Запрос 6: rate_limit", res.decision.value == "rate_limit",
          f"decision={res.decision.value}")


def test_ban_after_violations():
    """Тест бана только после порога нарушений.

    Логика:
    - Первые max_requests запросов: ALLOW
    - Каждый запрос сверх лимита: RATE_LIMIT + нарушение
    - После ban_after нарушений: IP блокируется
    - Следующий запрос: BLOCK (IP в blocklist)
    """
    print("\n=== Ban After Violations ===")

    fw = Firewall({"rate_limit": {"max_requests_per_minute": 2, "ban_after_violations": 3}})
    ip = "203.0.113.9"

    # 2 запроса — проходят (лимит 2)
    for i in range(2):
        res = fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=2000.0 + i))
        check(f"Запрос {i+1}: проходит", res.decision.value == "allow")

    # 3-й запрос — rate_limit (нарушение 1)
    res = fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=2000.0 + 2))
    check("Запрос 3: rate_limit (нарушение 1)", res.decision.value == "rate_limit",
          f"decision={res.decision.value}")

    # 4-й запрос — rate_limit (нарушение 2)
    res = fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=2000.0 + 3))
    check("Запрос 4: rate_limit (нарушение 2)", res.decision.value == "rate_limit")

    # 5-й запрос — rate_limit + IP блокируется (нарушение 3 = ban_after)
    res = fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=2000.0 + 4))
    check("Запрос 5: rate_limit + ban", res.decision.value == "rate_limit",
          f"decision={res.decision.value}")
    check("IP заблокирован после 3 нарушений", fw.ip_blocklist.is_blocked(ip))

    # 6-й запрос — BLOCK (IP уже в blocklist)
    res = fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=2000.0 + 5))
    check("Запрос 6: block (IP забанен)", res.decision.value == "block",
          f"decision={res.decision.value}")


def test_anomaly_detection():
    """Тест поведенческого фактора: много разных инструментов."""
    print("\n=== Anomaly Detection (Behavioral) ===")

    fw = Firewall({"anomaly_detection": {"max_methods_per_window": 3}})
    ip = "198.51.100.5"

    # 3 разных инструмента — ок
    for i in range(3):
        res = fw.check(FirewallRequest(
            ip=ip, method="tools/call",
            params={"name": f"tool_{i}"}, timestamp=3000.0 + i
        ))
        check(f"Инструмент {i+1}: allow", res.decision.value == "allow")

    # 4-й разный инструмент — block (anomaly)
    res = fw.check(FirewallRequest(
        ip=ip, method="tools/call",
        params={"name": "tool_3"}, timestamp=3000.0 + 3
    ))
    check("Инструмент 4: anomaly block", res.decision.value == "block",
          f"decision={res.decision.value}")


def test_legitimate_after_ban():
    """Тест что легитимные запросы проходят после бана другого IP."""
    print("\n=== Legitimate After Ban ===")

    fw = Firewall({"rate_limit": {"max_requests_per_minute": 2, "ban_after_violations": 2}})

    # Баним IP1
    ip1 = "10.0.0.1"
    for i in range(4):
        fw.check(FirewallRequest(ip=ip1, method="ping", params={}, timestamp=4000.0 + i))
    check("IP1 забанен", fw.ip_blocklist.is_blocked(ip1))

    # IP2 — легитимный, должен проходить
    ip2 = "10.0.0.2"
    res = fw.check(FirewallRequest(ip=ip2, method="ping", params={}, timestamp=4000.0 + 10))
    check("IP2 проходит (легитимный)", res.decision.value == "allow")


def main():
    test_rate_limiting()
    test_ban_after_violations()
    test_anomaly_detection()
    test_legitimate_after_ban()

    print()
    passed = sum(results)
    total = len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
