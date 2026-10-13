"""tests/bot_army/test_bot_army.py — армия ботов против файрвола.

Массовое подключение с одного адреса: предел частоты, бан после порога нарушений и то, что
соседний легитимный адрес при этом проходит. Файрвол поднимается в процессе, сервер не нужен.
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.contracts import ContractError
from core.declaration_schemas import RateLimit
from core.firewall import Firewall, FirewallRequest
from core.firewall.rules.rate_limiter import RateLimiter

FIREWALL = ROOT / "config" / "firewall.yaml"

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {('- ' + str(detail)) if detail else ''}")


def test_rate_limiting():
    """Тест rate limiting: много запросов с одного IP."""
    print("=== Rate Limiting ===")

    fw = Firewall({}, RateLimit(enabled=True, window_sec=60, max_requests=5, ban_after_violations=3))
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

    fw = Firewall({}, RateLimit(enabled=True, window_sec=60, max_requests=2, ban_after_violations=3))
    ip = "203.0.113.9"

    # 2 запроса — проходят (лимит 2)
    for i in range(2):
        res = fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=2000.0 + i))
        check(f"Запрос {i+1}: проходит", res.decision.value == "allow")

    res = fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=2000.0 + 2))
    check("Запрос 3: rate_limit (нарушение 1)", res.decision.value == "rate_limit",
          f"decision={res.decision.value}")

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
    """Anomaly = EVENT-based (опасные инструменты), НЕ time-based «много разных».

    Текущая модель: множество РАЗНЫХ безопасных инструментов НЕ блокируется;
    деструктивный инструмент ПРОПУСКается (log-only), но СЧИТАется сигналом в get_stats.
    """
    print("\n=== Anomaly Detection (event-based) ===")

    fw = Firewall({}, RateLimit(enabled=True, window_sec=60, max_requests=60, ban_after_violations=3))
    ip = "198.51.100.5"

    # Много разных БЕЗОПАСНЫХ инструментов — НЕ блок.
    for i in range(5):
        res = fw.check(FirewallRequest(
            ip=ip, method="tools/call",
            params={"name": f"tool_{i}"}, timestamp=3000.0 + i
        ))
        check(f"Разный безопасный инструмент {i+1}: allow (не time-based anomaly)",
              res.decision.value == "allow", f"decision={res.decision.value}")

    # Деструктивный инструмент: ПРОПУЩЕН (log-only), но ПОСЧИТАН.
    fw2 = Firewall.declared(FIREWALL)
    resd = fw2.check(FirewallRequest(
        ip="198.51.100.6", method="tools/call",
        params={"name": "fs_delete"}, timestamp=3100.0
    ))
    check("fs_delete: allow (log-only, не глухой блок)", resd.decision.value == "allow", resd.reason)
    check("fs_delete: посчитан как аномалия (get_stats)", fw2.get_stats()["anomalies_detected"] == 1)


def test_legitimate_after_ban():
    """Тест что легитимные запросы проходят после бана другого IP."""
    print("\n=== Legitimate After Ban ===")

    fw = Firewall({}, RateLimit(enabled=True, window_sec=60, max_requests=2, ban_after_violations=2))

    # Баним IP1
    ip1 = "10.0.0.1"
    for i in range(4):
        fw.check(FirewallRequest(ip=ip1, method="ping", params={}, timestamp=4000.0 + i))
    check("IP1 забанен", fw.ip_blocklist.is_blocked(ip1))

    # IP2 — легитимный, должен проходить
    ip2 = "10.0.0.2"
    res = fw.check(FirewallRequest(ip=ip2, method="ping", params={}, timestamp=4000.0 + 10))
    check("IP2 проходит (легитимный)", res.decision.value == "allow")


def test_shipped_config_is_enforced():
    """Боевые пороги из `config/firewall.yaml` реально исполняются, а не только объявлены.

    Ожидаемое считается ИЗ конфига: осознанная правка порога тест не красит, а разрыв
    проводки (порог объявлен, но не читается) красит сразу.
    """
    import yaml
    print("=== Боевая конфигурация исполняется (F55) ===")

    cfg = yaml.safe_load(FIREWALL.read_text(encoding="utf-8"))
    limit = int(cfg["rate_limit"]["max_requests"])
    ban_after = int(cfg["rate_limit"]["ban_after_violations"])
    fw = Firewall.declared(FIREWALL)
    ip = "203.0.113.77"

    # Все запросы внутри ОДНОГО окна: шаг в секунду растянул бы их ровно на минуту, и первый
    # успевал бы выпасть из окна — тест мерил бы длину окна, а не порог.
    passed = [fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=9000.0 + i / 1000))
              for i in range(limit)]
    check(f"первые {limit} запросов (объявленный порог) проходят",
          all(r.decision.value == "allow" for r in passed),
          {r.decision.value for r in passed})

    over = fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=9000.0 + limit / 1000))
    check(f"запрос {limit + 1} упирается в объявленный порог",
          over.decision.value == "rate_limit", f"decision={over.decision.value}")

    for i in range(ban_after):
        fw.check(FirewallRequest(ip=ip, method="ping", params={}, timestamp=9000.0 + (limit + 1 + i) / 1000))
    check(f"после {ban_after} нарушений (объявленный порог) IP забанен",
          fw.ip_blocklist.is_blocked(ip))

    # Инъекция: список паттернов тоже боевой, а не тестовый.
    phrase = str((cfg["injection_detection"]["patterns"] or ["ignore previous instructions"])[0])
    fresh = Firewall.declared(FIREWALL)
    verdict = fresh.check(FirewallRequest(ip="203.0.113.78", method="tools/call",
                                          params={"arguments": {"q": phrase}}, timestamp=9500.0))
    check("фраза ИЗ боевого списка паттернов блокируется",
          verdict.decision.value == "block", f"decision={verdict.decision.value}")


def test_retry_after_is_remainder():
    """Срок повтора — остаток до выхода старейшего запроса окна, а не окно целиком: клиент ждёт ровно столько, сколько
    держит лимит. Часы — параметр запроса: событие на шве часов, без ожидания."""
    print("\n=== Срок повтора — остаток окна ===")
    limiter = RateLimiter(max_requests=3, ban_after=3, window_sec=60)
    for at in (1000.0, 1010.0, 1020.0):
        limiter.check("198.51.100.20", at)
    refused = limiter.check("198.51.100.20", 1030.0)
    check("отказ после предела в окне", not refused.allowed)
    check("reset_in — остаток до выхода старейшего (1000 + 60 − 1030 = 30)", refused.reset_in == 30.0, refused.reset_in)
    # Окно не 60: прежний литерал окна дал бы здесь 30, объявленное окно 40 — 10.
    fw = Firewall({}, RateLimit(enabled=True, window_sec=40, max_requests=3, ban_after_violations=3))
    for at in (2000.0, 2010.0, 2020.0):
        fw.check(FirewallRequest(ip="198.51.100.21", method="ping", params={}, timestamp=at))
    over = fw.check(FirewallRequest(ip="198.51.100.21", method="ping", params={}, timestamp=2030.5))
    check("файрвол отдаёт срок по объявленному окну, целыми секундами вверх (2000 + 40 − 2030.5 = 9.5 → 10)",
          over.decision.value == "rate_limit" and over.retry_after_sec == 10, (over.decision.value, over.retry_after_sec))


def test_declaration_is_judged_by_the_door():
    """Раздел лимита читается дверью деклараций: нет ключа — DECLARATION_INVALID с путём ключа, а не значение по
    умолчанию; битая декларация при перезагрузке — тот же отказ, а прежние правила остаются в силе."""
    print("\n=== Дверь деклараций: раздел лимита ===")
    text = FIREWALL.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        broken = Path(tmp) / "firewall.yaml"
        broken.write_text(text.replace("  window_sec:", "  # window_sec:", 1), encoding="utf-8")
        check("порча легла: ключ окна снят в копии", "# window_sec:" in broken.read_text(encoding="utf-8"))
        try:
            Firewall.declared(broken)
            check("декларация без window_sec отвергнута", False, "собрана")
        except ContractError as refused:
            check("декларация без window_sec — DECLARATION_INVALID с путём ключа",
                  refused.code == "DECLARATION_INVALID"
                  and any(e["field"] == "rate_limit.window_sec" for e in refused.errors), refused.errors)
        fw = Firewall.declared(FIREWALL)
        limiter = fw.rate_limiter
        try:
            fw.reload_declared(broken)
            check("перезагрузка битой декларации отвергнута", False, "применена")
        except ContractError as refused:
            check("перезагрузка битой декларации — отказ двери", refused.code == "DECLARATION_INVALID", refused.code)
        check("после отказа перезагрузки правила прежние (тот же лимитер)", fw.rate_limiter is limiter)


def main():
    test_shipped_config_is_enforced()
    test_rate_limiting()
    test_ban_after_violations()
    test_anomaly_detection()
    test_legitimate_after_ban()
    test_retry_after_is_remainder()
    test_declaration_is_judged_by_the_door()

    print()
    passed = sum(results)
    total = len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
