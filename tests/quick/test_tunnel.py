"""
tests/quick/test_tunnel.py — Оффлайн-тест туннеля Cloudflare (D11)

## Что тестируем
Логику `core/transport/tunnel.py` БЕЗ живого запуска cloudflared:
1. Сборку команд для режимов quick / named-token / named-credentials.
2. Событийную готовность: триггер — соединение, а не таймер; отказ — с
   реальным текстом ошибки cloudflared (невалидный токен, выход процесса).
3. Супервизор: backoff (стабильный vs флап), переходы статуса соединения,
   коварный кейс "Unregistered..." ⊃ "registered...".

## Зачем нужен
Живой named-туннель требует домена/токена, поэтому проверяем парсер и
конечный автомат на реальных форматах логов cloudflared — это ловит регрессии
без сети (в т.ч. пойманный баг с lookbehind `(?<!un)`).

## Тип теста
Unit (offline, без процессов и сети)

## Запуск
    python3 tests/quick/test_tunnel.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.transport.tunnel import CloudflaredTunnel, TunnelError

CFG = str(ROOT / "config" / "tunnel.yaml")
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {('- ' + str(detail)) if detail else ''}")


def test_command_building():
    t = CloudflaredTunnel(port=8080, config_path=CFG)
    check("cmd quick", t._build_command()[1:] == ["tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8080"])

    t2 = CloudflaredTunnel(port=8080); t2.mode = "named"; t2.tunnel_token = "TOK"
    check("cmd named token", t2._build_command()[1:] == ["tunnel", "--no-autoupdate", "run", "--token", "TOK"])

    t3 = CloudflaredTunnel(port=8080); t3.mode = "named"; t3.tunnel_token = ""; t3.tunnel_id = "uuid-123"; t3.credentials_file = "/x/cred.json"
    check("cmd named credentials",
          t3._build_command()[1:] == ["tunnel", "--no-autoupdate", "--credentials-file", "/x/cred.json", "run", "--url", "http://127.0.0.1:8080", "uuid-123"])

    t4 = CloudflaredTunnel(port=8080); t4.mode = "named"; t4.tunnel_token = ""; t4.tunnel_id = ""; t4.tunnel_name = ""
    try:
        t4._build_command()
        check("cmd named empty -> ValueError", False)
    except ValueError:
        check("cmd named empty -> ValueError", True)


def test_readiness_event_driven():
    alive = lambda: None
    exited = lambda: 1

    quick_log = [
        "INF Requesting new quick Tunnel on trycloudflare.com...\n",
        "INF |  https://random-words-here.trycloudflare.com  |\n",
        "INF Registered tunnel connection connIndex=0 connection=abc location=fra\n",
    ]
    t = CloudflaredTunnel(port=8080); t.mode = "quick"
    url = t._await_ready(iter(quick_log), alive)
    check("quick: ready on CONNECTION, returns URL", url == "https://random-words-here.trycloudflare.com", url)

    t2 = CloudflaredTunnel(port=8080); t2.mode = "quick"
    try:
        t2._await_ready(iter(["INF |  https://abc.trycloudflare.com  |\n"]), exited)
        check("quick: URL without connection is NOT ready", False)
    except TunnelError:
        check("quick: URL without connection is NOT ready", True)

    t3 = CloudflaredTunnel(port=8080); t3.mode = "named"; t3.tunnel_token = "BAD"
    try:
        t3._await_ready(iter(['ERR Couldn\'t start tunnel error="provided Tunnel token is invalid"\n']), exited)
        check("named: bad token -> adequate error", False)
    except TunnelError as e:
        check("named: bad token -> adequate error", "token is invalid" in str(e).lower(), str(e)[:60])

    t4 = CloudflaredTunnel(port=8080); t4.mode = "named"
    try:
        t4._await_ready(iter(['ERR Failed to dial edge error="dial tcp: timeout"\n']), exited)
        check("exit: real error, not abstract timeout", False)
    except TunnelError as e:
        check("exit: real error, not abstract timeout", "без соединения" in str(e) and "Failed to dial" in str(e), str(e)[:60])

    t5 = CloudflaredTunnel(port=8080); t5.mode = "named"; t5.hostname = "mcp.example.com"
    url5 = t5._await_ready(iter(["INF Registered tunnel connection connIndex=0\n"]), alive)
    check("named: connected -> hostname URL", url5 == "https://mcp.example.com", url5)


def test_supervisor_backoff_and_status():
    t = CloudflaredTunnel(port=8080, config_path=CFG)

    check("stable run resets attempts to 1", t._next_attempts(5, uptime=120) == 1, t._next_attempts(5, 120))
    check("flapping increments attempts", t._next_attempts(3, uptime=1) == 4, t._next_attempts(3, 1))

    d1, d3, dbig = t._backoff_delay(1), t._backoff_delay(3), t._backoff_delay(20)
    check("backoff grows with attempts", d3 > d1, f"d1={d1:.2f} d3={d3:.2f}")
    check("backoff capped at retry_max(+jitter)", dbig <= t.retry_max + t.retry_base + 1e-3, f"dbig={dbig:.2f} cap={t.retry_max}")

    t._apply_line("INF Registered tunnel connection connIndex=0\n")
    t._apply_line("INF Registered tunnel connection connIndex=1\n")
    s = t.status(); check("two connections -> connected, count=2", s["connected"] and s["connections"] == 2, s["connections"])
    t._apply_line("INF Unregistered tunnel connection connIndex=1\n")
    s = t.status(); check("one drop -> still connected (1)", s["connected"] and s["connections"] == 1, s["connections"])
    t._apply_line("INF Lost connection with the edge\n")
    s = t.status(); check("all dropped -> disconnected (0)", (not s["connected"]) and s["connections"] == 0, s["connections"])

    t._attempts = 7; t._last_error = "boom"
    t._apply_line("INF Registered tunnel connection connIndex=0\n")
    s = t.status(); check("reconnect resets attempts & clears error", s["attempts"] == 0 and s["last_error"] is None,
                          f"att={s['attempts']} err={s['last_error']}")

    check("classify: Unregistered -> disconnected",
          CloudflaredTunnel._classify_line("x Unregistered tunnel connection connIndex=0")[0] == "disconnected")
    check("classify: Registered -> connected",
          CloudflaredTunnel._classify_line("x Registered tunnel connection connIndex=0")[0] == "connected")

    need = {"running", "connected", "connections", "attempts", "public_url", "last_error", "uptime_sec"}
    check("status() shape complete", need.issubset(t.status().keys()), sorted(t.status().keys()))


def main():
    test_command_building()
    test_readiness_event_driven()
    test_supervisor_backoff_and_status()
    print()
    passed, total = sum(results), len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
