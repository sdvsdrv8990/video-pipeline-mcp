"""
tests/quick/test_tunnel.py — оффлайн-тест туннеля Cloudflare.

## Назначение
`core/transport/tunnel.py` без живого cloudflared: сборка команд для quick / named-token /
named-credentials, событийная готовность (триггер — соединение, а не таймер, отказ — с
реальным текстом ошибки) и супервизор: backoff, переходы статуса соединения.

## Границы
Образцы — реальные строки логов cloudflared: там `registered` живёт внутри `Unregistered`,
и регрессия в lookbehind `(?<!un)` ловится только такими образцами. Живой named-туннель
требует домена и токена, поэтому сети и процессов здесь нет.
"""

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.transport.tunnel import CloudflaredTunnel, TunnelCancelled, TunnelError

CFG = str(ROOT / "config" / "tunnel.yaml")
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {('- ' + str(detail)) if detail else ''}")


def test_command_building():
    # quick-режим = БЕЗ конфига (иначе подхватит машинный named config/tunnel.yaml и тест не изолирован).
    t = CloudflaredTunnel(port=8080)
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

    # Потеря соединения ДО готовности: признак «соединено» обязан сброситься, иначе сервер отдаёт
    # URL туннеля, которого уже нет. Обе стороны — потеря не пускает, повторная регистрация пускает.
    lost_log = [
        "INF Registered tunnel connection connIndex=0\n",
        "INF Unregistered tunnel connection connIndex=0\n",
        "INF |  https://gone.trycloudflare.com  |\n",
    ]
    t6 = CloudflaredTunnel(port=8080); t6.mode = "quick"
    try:
        t6._await_ready(iter(lost_log), exited)
        check("quick: connection lost before URL is NOT ready", False)
    except TunnelError:
        check("quick: connection lost before URL is NOT ready", True)

    t7 = CloudflaredTunnel(port=8080); t7.mode = "quick"
    url7 = t7._await_ready(iter([*lost_log, "INF Registered tunnel connection connIndex=1\n"]), alive)
    check("quick: re-registration after loss is ready again", url7 == "https://gone.trycloudflare.com", url7)

    # Рёбер у cloudflared несколько: падение ОДНОГО при живых остальных готовность не отменяет.
    t8 = CloudflaredTunnel(port=8080); t8.mode = "quick"
    url8 = t8._await_ready(iter([
        "INF Registered tunnel connection connIndex=0\n",
        "INF Registered tunnel connection connIndex=1\n",
        "INF Unregistered tunnel connection connIndex=0\n",
        "INF |  https://multi.trycloudflare.com  |\n",
    ]), alive)
    check("quick: one edge of many lost -> still ready", url8 == "https://multi.trycloudflare.com", url8)


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


def test_cancel_is_not_failure():
    """Отмена оператора и отказ туннеля — разные роды: судим КОДОМ выхода, а не текстом лога."""
    for текст in ('INF Initiating graceful shutdown due to signal interrupt\n',
                  'ERR failed to serve: context canceled\n',
                  'INF Registered tunnel connection\n'.replace("Registered", "Unregistered")):
        t = CloudflaredTunnel(port=8080); t.mode = "named"
        try:
            t._await_ready(iter([текст]), lambda: 0)
            check(f"code=0 -> отмена, а не отказ ({текст[:18]}…)", False)
        except TunnelCancelled:
            check(f"code=0 -> отмена, а не отказ ({текст[:18]}…)", True)
        except TunnelError:
            check(f"code=0 -> отмена, а не отказ ({текст[:18]}…)", False, "поймано как отказ")

    t2 = CloudflaredTunnel(port=8080); t2.mode = "named"
    try:
        t2._await_ready(iter(['ERR Failed to dial edge error="dial tcp: timeout"\n']), lambda: 1)
        check("ненулевой код остаётся ОТКАЗОМ", False)
    except TunnelCancelled:
        check("ненулевой код остаётся ОТКАЗОМ", False, "отмена подменила отказ")
    except TunnelError:
        check("ненулевой код остаётся ОТКАЗОМ", True)


def test_ready_has_a_limit():
    """У ожидания есть предел: молчащий туннель обязан кончиться вердиктом, а не тишиной."""
    t = CloudflaredTunnel(port=8080); t.mode = "named"

    def бесконечный():
        """Поток, который НЕ кончается: срок обязан рвать ожидание сам, а не ждать закрытия.

        Предел выдач — не украшение: без него набор с неработающим сроком не краснеет, а ВИСИТ,
        и молчание неотличимо от прохода.
        """
        for _ in range(10_000):
            yield "INF ничего не значащая строка\n"
        raise AssertionError("срок не сработал: ожидание не кончилось само")
    try:
        t._await_ready(бесконечный(), lambda: None, deadline=time.monotonic() - 1)
        check("срок вышел -> отказ, а не бесконечное ожидание", False)
    except TunnelCancelled:
        check("срок вышел -> отказ, а не бесконечное ожидание", False, "спутано с отменой")
    except TunnelError as e:
        check("срок вышел -> отказ, а не бесконечное ожидание",
              "не установлено за" in str(e), str(e)[:70])
    except AssertionError as e:
        # Вердикт словами, а не падением набора: иначе «сроку нечем сработать» читается как сбой
        # самого набора, и разбирать пришлось бы трейс вместо строки.
        check("срок вышел -> отказ, а не бесконечное ожидание", False, str(e))

    t2 = CloudflaredTunnel(port=8080); t2.mode = "named"; t2.hostname = "mcp.example.com"
    url = t2._await_ready(iter(["INF Registered tunnel connection connIndex=0\n"]), lambda: None,
                          deadline=time.monotonic() + 30)
    check("успевшее соединение сроком не наказывается", url == "https://mcp.example.com", url)

    # Убитый сторожем процесс отдаёт код -9, неотличимый от чужого `kill`: вердикт обязан назвать
    # ВИНОВНИКА, иначе читателю остаётся внутренний номер сигнала.
    t3 = CloudflaredTunnel(port=8080); t3.mode = "named"
    t3._killed_by_watchdog = True
    try:
        t3._await_ready(iter(["INF молчание\n", ""]), lambda: -9, deadline=time.monotonic() + 30)
        check("сторож называет себя в вердикте", False)
    except TunnelError as e:
        check("сторож называет себя в вердикте",
              "молчал и остановлен по сроку" in str(e) and "7844" in str(e), str(e)[:60])


def test_first_attempt_retries():
    """Повтор ПЕРВОГО подъёма: непоправимое не молотится, отмена не повторяется."""
    t = CloudflaredTunnel(port=8080); t.mode = "named"; t.first_attempts = 3
    t._sleep_interruptible = lambda _: None          # паузы в наборе не ждём
    t.stop = lambda: None
    попытки = []

    def падает_дважды():
        попытки.append(1)
        if len(попытки) < 3:
            raise TunnelError('cloudflared завершился (code=1) без соединения: dial tcp timeout')
        return "https://mcp.example.com"
    t._one_attempt = падает_дважды
    t._start_supervisor = lambda: None
    try:
        поднят = t.start()
    except TunnelError as beda:
        поднят = f"отказ: {beda}"
    check("первый подъём ПОВТОРЯЕТСЯ, как и смерть процесса потом",
          поднят == "https://mcp.example.com" and len(попытки) == 3, f"{поднят} / {len(попытки)}")

    t2 = CloudflaredTunnel(port=8080); t2.mode = "named"; t2.first_attempts = 3
    t2._sleep_interruptible = lambda _: None; t2.stop = lambda: None
    было = []

    def неверный_токен():
        было.append(1)
        raise TunnelError('cloudflared отказал: provided Tunnel token is invalid')
    t2._one_attempt = неверный_токен
    try:
        t2.start()
        check("непоправимое не молотится повторами", False)
    except TunnelError:
        check("непоправимое не молотится повторами", len(было) == 1, len(было))

    t3 = CloudflaredTunnel(port=8080); t3.mode = "named"; t3.first_attempts = 3
    t3._sleep_interruptible = lambda _: None; t3.stop = lambda: None
    отмен = []

    def отменён():
        отмен.append(1)
        raise TunnelCancelled("cloudflared остановлен извне (code=0)")
    t3._one_attempt = отменён
    try:
        t3.start()
        check("отмена не повторяется: нас останавливают, а не мы не смогли", False)
    except TunnelCancelled:
        check("отмена не повторяется: нас останавливают, а не мы не смогли", len(отмен) == 1, len(отмен))


def test_probe_names_the_responder():
    """Кто ответил по публичному адресу — судится ЗАГОЛОВКОМ, код лишь дополняет."""
    край = CloudflaredTunnel._край_ответил
    check("cloudflare в заголовке — край, при ЛЮБОМ коде",
          all(край(код, "cloudflare") for код in (200, 405, 502, 530)))
    check("свой заголовок — дошло до нас, даже на 5xx",
          not any(край(код, "Python/3.14 aiohttp/3.9") for код in (200, 405, 500)))
    check("заголовка нет: 5xx — край, наш транспорт таких на GET не отдаёт",
          край(530, "") and край(502, "") and not край(405, ""))

    t = CloudflaredTunnel(port=8080); t.mode = "quick"; t._public_url = None
    улика = t.probe()
    check("адреса нет — это «улики нет», а не «не отвечает»",
          улика["кто"] == "адреса нет" and улика["дошло"] is False, улика)


def test_probe_refuses_foreign_scheme():
    """Публичный адрес приходит СНАРУЖИ — схему выбирает транспорт, а не доверие к строке."""
    подложка = Path(tempfile.mkdtemp()) / "секрет.txt"
    подложка.write_text("СОДЕРЖИМОЕ ДИСКА", encoding="utf-8")

    t = CloudflaredTunnel(port=8080); t.mode = "quick"; t._public_url = подложка.as_uri()
    улика = t.probe()
    check("чужая схема — «нет ответа», а не ответ сети",
          улика["дошло"] is False and улика["кто"] == "нет ответа", улика)
    check("содержимое диска не выдаётся за ответ сети",
          "СОДЕРЖИМОЕ ДИСКА" not in str(улика), улика)


def main():
    test_command_building()
    test_readiness_event_driven()
    test_supervisor_backoff_and_status()
    test_cancel_is_not_failure()
    test_ready_has_a_limit()
    test_first_attempt_retries()
    test_probe_names_the_responder()
    test_probe_refuses_foreign_scheme()
    print()
    passed, total = sum(results), len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
