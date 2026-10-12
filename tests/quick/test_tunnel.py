"""tests/quick/test_tunnel.py — туннель без живого cloudflared.

Судятся сборка команд трёх видов туннеля, событийная готовность (триггер — соединение, а не
таймер; отказ — с реальным текстом ошибки) и супервизор с его отступом и переходами статуса.
Образцы — настоящие строки логов cloudflared: там `registered` живёт внутри `Unregistered`, и
регрессия разделителя ловится только такими образцами. Живой именованный туннель требует домена и
токена, поэтому ни сети, ни процессов здесь нет.
"""

import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.transport.tunnel import CloudflaredTunnel, TunnelCancelled, TunnelError
from tests.harness.live_server import declarations_copy

# Декларация туннеля — у прогона, в копии деклараций харнесса: машинный config/tunnel.yaml владельца в копию не идёт,
# и пороги отступа супервизора судятся по объявленным здесь, а не по чужой машине.
CFG = declarations_copy() / "tunnel.yaml"
CFG.write_text("mode: quick\nretry_base_seconds: 2\nretry_max_seconds: 60\nretry_reset_seconds: 60\n", encoding="utf-8")
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {('- ' + str(detail)) if detail else ''}")


def test_command_building():
    # quick-режим = БЕЗ конфига (иначе подхватит машинный named config/tunnel.yaml и тест не изолирован).
    t = CloudflaredTunnel(port=8080)
    check("cmd quick", t._build_command()[1:] == ["tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8080"])

    t2 = CloudflaredTunnel(port=8080); t2.mode = "named"; t2.tunnel_token = "TOK"
    check("cmd named token: токена в argv нет — он уходит средой ребёнка",
          t2._build_command()[1:] == ["tunnel", "--no-autoupdate", "run"], t2._build_command()[1:])

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

    # Второй образец — текст cloudflared 2026.5.0 (`TUNNEL_TOKEN=мусор cloudflared tunnel run`, код 255): прежний набор
    # знал только первый, и отказ установленной версии выходил без причины.
    for образец in ('ERR Couldn\'t start tunnel error="provided Tunnel token is invalid"\n',
                    "Provided Tunnel token is not valid.\n"):
        t3 = CloudflaredTunnel(port=8080); t3.mode = "named"; t3.tunnel_token = "BAD"
        try:
            t3._await_ready(iter([образец]), exited)
            check(f"named: bad token -> adequate error ({образец[:24]}…)", False)
        except TunnelError as e:
            check(f"named: bad token -> adequate error ({образец[:24]}…)",
                  "отказал" in str(e) and "token is" in str(e).lower(), str(e)[:60])

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
    check("декларация туннеля — копия прогона, а не config/ дерева (машинный файл владельца)",
          not CFG.resolve().is_relative_to((ROOT / "config").resolve()), CFG)
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
    t._binary_available = lambda: True               # попытка подменена — бинарь не порождается, его наличие не судим
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
    t2._sleep_interruptible = lambda _: None; t2.stop = lambda: None; t2._binary_available = lambda: True
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
    t3._sleep_interruptible = lambda _: None; t3.stop = lambda: None; t3._binary_available = lambda: True
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
    with tempfile.TemporaryDirectory() as tmp:
        подложка = Path(tmp) / "секрет.txt"
        подложка.write_text("СОДЕРЖИМОЕ ДИСКА", encoding="utf-8")
        t = CloudflaredTunnel(port=8080); t.mode = "quick"; t._public_url = подложка.as_uri()
        улика = t.probe()
    check("чужая схема — «нет ответа», а не ответ сети",
          улика["дошло"] is False and улика["кто"] == "нет ответа", улика)
    check("содержимое диска не выдаётся за ответ сети",
          "СОДЕРЖИМОЕ ДИСКА" not in str(улика), улика)


def test_probe_through_door():
    """Прозвонка — дверью загрузчика (`Fetcher.status`, политика tunnel_diagnostics.yaml → прозвонка): хост судится
    разобранным и целиком, соединение — на судимый IP, тело не читается, срок — подставными часами."""
    import httpx

    class Край:
        """Подставной публичный край: журнал запросов (IP, Host, SNI), вытянутые куски тела, часы события. Без ответа —
        молчит: соединение и ответ съедают каждый свою фазу, которую дверь отдала транспорту."""

        def __init__(self, ответ=None, имена=None):
            self.ответ, self.имена, self.журнал, self.кусков, self.часы = ответ, имена or {}, [], 0, 0.0

        def resolve(self, host, port, *a, **k):
            return [(2, 1, 6, "", (self.имена.get(host, "104.16.230.132"), port))]

        def тело(self):
            for _ in range(4):
                self.кусков += 1
                yield b"x" * 1024

        def handle(self, request):
            self.журнал.append((request.url.host, request.headers.get("host"), request.extensions.get("sni_hostname")))
            if self.ответ is None:
                фаза = request.extensions["timeout"]
                self.часы += фаза["connect"] + фаза["read"]
                raise httpx.ReadTimeout("молчит")
            код, сервер = self.ответ
            return httpx.Response(код, headers={"Server": сервер}, content=self.тело())

    прежние = tuple(CloudflaredTunnel.__dict__[шов] for шов in ("transport", "resolve", "clock", "sleep"))

    def прозвонить(адрес, край, режим="quick", hostname="", **kw):
        CloudflaredTunnel.transport = httpx.MockTransport(край.handle)
        CloudflaredTunnel.resolve = staticmethod(край.resolve)
        CloudflaredTunnel.clock = staticmethod(lambda: край.часы)
        CloudflaredTunnel.sleep = staticmethod(lambda s: setattr(край, "часы", край.часы + s))
        t = CloudflaredTunnel(port=8080); t.mode = режим; t.hostname = hostname; t._public_url = адрес
        return t.probe(**kw)

    try:
        край = Край((530, "cloudflare"))
        улика = прозвонить("https://abc-1.trycloudflare.com", край)
        check("край отвечает 530 — «не дошло», кто — край; соединение на судимый IP, имя в Host и SNI; тело не вытянуто",
              улика["дошло"] is False and улика["кто"] == "cloudflare" and край.кусков == 0
              and край.журнал == [("104.16.230.132", "abc-1.trycloudflare.com", "abc-1.trycloudflare.com")],
              (улика, край.журнал, край.кусков))
        край = Край((405, "Python/3.14 aiohttp/3.9"))
        улика = прозвонить("https://abc-1.trycloudflare.com", край)
        check("наш процесс ответил 405 — «дошло»: код — улика, а не отказ двери; тело не вытянуто",
              улика["дошло"] is True and улика["код"] == 405 and край.кусков == 0, (улика, край.кусков))
        край = Край((405, "aiohttp"), имена={"abc-1.trycloudflare.com": "10.0.0.9"})
        улика = прозвонить("https://abc-1.trycloudflare.com", край)
        check("публичное имя разрешилось во внутреннюю сеть — «не дошло» с отказом двери, запроса нет",
              улика["дошло"] is False and "DOWNLOAD_FORBIDDEN" in улика["текст"] and not край.журнал,
              (улика, край.журнал))
        for случай, адрес in (
                ("адрес зоны в строке запроса чужого хоста", "https://evil.example/?x=https://abc.trycloudflare.com"),
                ("зона quick в начале чужого хоста", "https://abc.trycloudflare.com.evil.example/"),
                ("имя зоны как имя пользователя", "https://abc.trycloudflare.com@evil.example/")):
            край = Край((405, "aiohttp"))
            улика = прозвонить(адрес, край)
            check(f"quick: {случай} — хост судится разобранным и целиком, туда ни запроса",
                  улика["дошло"] is False and not край.журнал, (улика, край.журнал))
        край = Край((405, "aiohttp"))
        улика = прозвонить(None, край, режим="named", hostname="mcp.example.com")
        check("named: прозвонка идёт ровно на объявленный hostname",
              улика["дошло"] is True and [h for _, h, _ in край.журнал] == ["mcp.example.com"], (улика, край.журнал))
        край = Край((405, "aiohttp"))
        улика = прозвонить("https://other.example", край, режим="named", hostname="mcp.example.com")
        check("named: адрес не объявленного hostname — не спрашиваем", улика["дошло"] is False and not край.журнал,
              (улика, край.журнал))
        край = Край()
        прозвонить("https://abc-1.trycloudflare.com", край)
        по_умолчанию = край.часы
        край = Край()
        прозвонить("https://abc-1.trycloudflare.com", край, timeout=60)
        check(f"срок: молчащий край — прозвонка в срок вызывающего ({по_умолчанию:g} с при timeout 8) и не дольше "
              f"deadline_sec политики ({край.часы:g} с при timeout 60); прежде фаза 8 с × 2 и тело без предела",
              по_умолчанию == 8 and край.часы == 16, (по_умолчанию, край.часы))
    finally:
        CloudflaredTunnel.transport, CloudflaredTunnel.resolve, CloudflaredTunnel.clock, CloudflaredTunnel.sleep = прежние


class _Живой:
    """Двойник поднятого туннеля: приёмка спрашивает у него ровно status() и probe()."""

    mode, port = "named", 8080

    def status(self):
        return {"running": True, "connected": True, "connections": 2, "last_error": None}

    def probe(self):
        return {"дошло": True, "текст": "ответил наш процесс (405)"}


def _нет_остатков():
    return not list(Path(tempfile.gettempdir()).glob("vpm-диагностика-*/поток.json"))


def test_приёмка_не_считает_непроверенное_пройденным():
    """Бинарь клиентского конца — двойник по пути каталога (`бинари.cloudflared.в_дереве`) в копии деклараций: вердикт
    приёмки не зависит от того, стоит ли cloudflared на машине прогона (CI его не ставит)."""
    from core.transport.diagnostics import НЕПОЛНО, ОТКАЗ, ПРОШЛО, быстрая, полная
    двойники = Path(tempfile.mkdtemp(prefix="vpm-bin-"))
    копия = declarations_copy({"tunnel_diagnostics.yaml": {"бинари.cloudflared.в_дереве": _бинарь_версии(двойники)}})
    прежний = os.environ.get("MCP_CONFIG")
    os.environ["MCP_CONFIG"] = str(копия)
    try:
        без = быстрая(конец="client")
        check("нет туннеля → НЕПОЛНО, а не ПРОШЛО", без.вердикт == НЕПОЛНО, без.вердикт)
        check("непройденное названо и получило следующий шаг",
              any(и.исход == "нечем" and и.дальше for и in без.итоги))
        с_туннелем = полная(_Живой(), конец="client")
        check("живой туннель + поток → ПРОШЛО", с_туннелем.вердикт == ПРОШЛО, с_туннелем.вердикт)

        class Край(_Живой):
            def probe(self):
                return {"дошло": False, "текст": "край Cloudflare отвечает 530"}

        край = быстрая(Край(), конец="client")
        check("край отвечает вместо нас → ОТКАЗ", край.вердикт == ОТКАЗ, край.вердикт)
        соединение = [и for и in край.итоги if и.проверка.id == "соединение"][0]
        check("отказ называет ТОЧКУ и следующий шаг",
              "530" in соединение.причина and bool(соединение.дальше), соединение)

        from core.transport.diagnostics import Диагностика, путь_бинаря
        двойник = str(двойники / "cloudflared")
        туннель, диагностика = CloudflaredTunnel(port=8080).binary, Диагностика(_Живой())._бинарь("cloudflared")
        check("путь бинаря — один узел каталога: туннель и диагностика называют путь `в_дереве`",
              туннель == диагностика == двойник, (туннель, диагностика))
        check("пустой `в_дереве` не делает бинарём каталог проекта",
              путь_бинаря({"бинари": {"нет-такого": {}}}, "нет-такого") is None,
              путь_бинаря({"бинари": {"нет-такого": {}}}, "нет-такого"))
    finally:
        _вернуть({"MCP_CONFIG": прежний})
        shutil.rmtree(копия.parent, ignore_errors=True)
        shutil.rmtree(двойники, ignore_errors=True)


def _бинарь_версии(каталог: Path) -> str:
    """Исполняемый двойник бинаря туннеля для проверки «компоненты»: отвечает строкой версии и выходит."""
    путь = каталог / "cloudflared"
    путь.write_text(f"#!{sys.executable}\nprint('cloudflared version двойник')\n", encoding="utf-8")
    путь.chmod(0o700)
    return str(путь)


def test_приёмка_поток_и_уборка():
    from core.transport.diagnostics import ОТКАЗ, ПРОШЛО, Диагностика
    д = Диагностика(_Живой(), конец="client")
    отчёт = д.прогнать("полная")
    поток = [и for и in отчёт.итоги if и.проверка.id == "поток_после"][0]
    check("сумма потока сходится на приёме", поток.исход == ПРОШЛО, поток.причина)
    check("путь потока назван, а не подразумевается", "петля" in отчёт.путь, отчёт.путь)
    check("уборка снесла временные", not д._временные and _нет_остатков())
    # Мутация: подменённая сумма ОБЯЗАНА покраснеть, иначе проверка целостности декоративна.
    д2 = Диагностика(_Живой(), конец="client")
    д2._поток_до()
    д2._поток["сумма"] = "подменено"
    исход, причина, _ = д2._поток_после()
    д2._уборка()
    check("подменённая сумма → ОТКАЗ", исход == ОТКАЗ and "сумма" in причина, причина)


def test_приёмка_каталог_и_исполнители():
    from core.contracts import ContractError
    from core.transport.diagnostics import Диагностика
    д = Диагностика(_Живой(), конец="client")
    быстрые = {п.id for п in д.проверки("быстрая")}
    полные = {п.id for п in д.проверки("полная")}
    check("полный режим включает быстрый", быстрые < полные, (быстрые, полные))
    check("у каждой объявленной проверки есть исполнитель",
          полные <= set(д._таблица()), полные - set(д._таблица()))
    д._объявление["проверки"] = [{"id": "призрак", "что": "Призрак",
                                  "доказывает": "нечего", "режим": "быстрая"}]
    try:
        д.прогнать("быстрая")
        check("объявленная без исполнителя → громкий отказ", False, "отказа не было")
    except ContractError as beda:
        check("объявленная без исполнителя → громкий отказ", "призрак" in str(beda), beda)


def test_приёмка_концы_и_бинари():
    from core.transport.diagnostics import НЕЧЕМ, ОТКАЗ, Диагностика
    исход, причина, дальше = Диагностика(_Живой(), конец="server")._компоненты()
    check("серверный конец требует xray и называет его",
          исход == ОТКАЗ and "xray" in причина and "xray" in дальше, (исход, причина))
    исход, _, _ = Диагностика(_Живой(), конец="none")._компоненты()
    check("конец none — нечем проверять, а не «прошло»", исход == НЕЧЕМ, исход)


def test_каталог_одна_дверь():
    """Диагностика и прозвонка читают каталог проверок одной дверью (`каталог_проверок`): каталог деклараций прогона
    (`MCP_CONFIG`) судит обеих; прежде диагностика читала `config/` рядом с кодом мимо него."""
    import os
    import shutil

    import httpx

    from core.transport.diagnostics import Диагностика
    from tests.harness.live_server import declarations_copy

    копия = declarations_copy({"tunnel_diagnostics.yaml": {
        "проверки": [{"id": "конфигурация", "что": "Конфигурация", "доказывает": "копия", "режим": "быстрая"}],
        "прозвонка.codes.failed": "PROVIDER_TIMEOUT"}})
    прежний = os.environ.get("MCP_CONFIG")
    прежние = tuple(CloudflaredTunnel.__dict__[шов] for шов in ("transport", "resolve"))

    def молчит(request):
        raise httpx.ConnectError("порт молчит")

    os.environ["MCP_CONFIG"] = str(копия)
    try:
        проверки = {п.id for п in Диагностика(_Живой(), конец="client").проверки("полная")}
        CloudflaredTunnel.transport = httpx.MockTransport(молчит)
        CloudflaredTunnel.resolve = staticmethod(lambda host, port, *a, **k: [(2, 1, 6, "", ("104.16.230.132", port))])
        t = CloudflaredTunnel(port=8080); t.mode = "quick"; t._public_url = "https://abc-1.trycloudflare.com"
        улика = t.probe()
    finally:
        CloudflaredTunnel.transport, CloudflaredTunnel.resolve = прежние
        if прежний is None:
            os.environ.pop("MCP_CONFIG", None)
        else:
            os.environ["MCP_CONFIG"] = прежний
        shutil.rmtree(копия.parent, ignore_errors=True)
    check("MCP_CONFIG: диагностика читает каталог деклараций прогона, а не config/ рядом с кодом",
          проверки == {"конфигурация"}, проверки)
    check("MCP_CONFIG: прозвонка — тот же каталог: код отказа «не дошло» из копии",
          улика["дошло"] is False and улика["текст"].startswith("PROVIDER_TIMEOUT"), улика)


def test_secret_reaches_child_by_env():
    """Токен туннеля уходит ребёнку средой, а не аргументом; унаследованная личность туннеля ребёнку не идёт.

    Оракул — ядро: `/proc/<pid>/cmdline` и `/proc/<pid>/environ` живого ребёнка-двойника. В режиме credentials
    cloudflared 2026.5.0 ставит унаследованные TUNNEL_TOKEN/TUNNEL_TOKEN_FILE выше объявленного файла (`tunnel run
    --help`, проба без сети) и молча поднимает другой туннель."""
    if not Path("/proc/self/environ").exists():
        print("── не судится: в ОС нет /proc — argv и среду ребёнка спросить не у кого")
        return
    секрет, чужой = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    наследство = {"TUNNEL_TOKEN": чужой, "TUNNEL_TOKEN_FILE": "/нет/токена", "TUNNEL_CRED_FILE": "/нет/учётных.json",
                  "TUNNEL_CRED_CONTENTS": "{}"}
    случаи = (
        ("токен: в argv его нет, в среде ребёнка — свой, а не унаследованный", "named",
         {"MCP_TUNNEL_TOKEN": секрет, **наследство}, {}, ["tunnel", "--no-autoupdate", "run"], {"TUNNEL_TOKEN": секрет}),
        ("credentials: унаследованная личность туннеля ребёнку не идёт", "named", наследство,
         {"tunnel_id": "uuid-123", "credentials_file": "/x/cred.json"},
         ["tunnel", "--no-autoupdate", "--credentials-file", "/x/cred.json", "run", "--url", "http://127.0.0.1:8080",
          "uuid-123"], {}),
        ("quick: унаследованная личность туннеля ребёнку не идёт", "quick", наследство, {},
         ["tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8080"], {}),
    )
    прежние = {имя: os.environ.get(имя) for имя in ("MCP_TUNNEL_TOKEN", *наследство)}
    with tempfile.TemporaryDirectory() as tmp:
        двойник = _двойник(Path(tmp))
        for случай, режим, среда, поля, хвост, ждём in случаи:
            _вернуть(dict.fromkeys(прежние))
            os.environ.update(среда)
            t = CloudflaredTunnel(port=8080); t.mode = режим; t.binary = двойник
            for поле, значение in поля.items():
                setattr(t, поле, значение)
            try:
                t.start()
                argv, у_ребёнка = _глазами_ядра(t._proc.pid)
            except (TunnelError, RuntimeError, OSError) as сбой:
                check(случай, False, f"двойник не поднялся: {сбой}")
                continue
            finally:
                t.stop()
                _вернуть(прежние)
            личность = {k: v for k, v in у_ребёнка.items() if k in наследство}
            видно = [a.replace(секрет, "<секрет>").replace(чужой, "<чужой>") for a in argv[2:]]
            check(f"{случай} (argv и среда — /proc ребёнка)",
                  argv[-len(хвост):] == хвост and not any(секрет in a or чужой in a for a in argv) and личность == ждём,
                  {"argv": видно, "личность": {k: "<секрет>" if v == секрет else "<чужое>" for k, v in личность.items()}})

        # Бинаря нет — отказ до порождения: процесса, которому ушёл бы токен, нет вовсе.
        os.environ["MCP_TUNNEL_TOKEN"] = секрет
        try:
            t = CloudflaredTunnel(port=8080); t.mode = "named"; t.binary = str(Path(tmp) / "нет-бинаря")
            t.start()
            check("бинаря нет → отказ до порождения", False, "туннель поднялся")
        except RuntimeError as отказ:
            check("бинаря нет → отказ до порождения", t._proc is None and секрет not in str(отказ), str(отказ)[:60])
        finally:
            _вернуть(прежние)


def _двойник(каталог: Path) -> str:
    """Исполняемый двойник cloudflared: печатает адрес quick и событие соединения, затем ждёт сигнала остановки."""
    путь = каталог / "cloudflared"
    путь.write_text(f"#!{sys.executable}\nimport signal\n"
                    "print('INF |  https://dvoinik.trycloudflare.com  |', flush=True)\n"
                    "print('INF Registered tunnel connection connIndex=0', flush=True)\n"
                    "signal.pause()\n", encoding="utf-8")
    путь.chmod(0o700)
    return str(путь)


def _глазами_ядра(pid: int) -> tuple[list[str], dict[str, str]]:
    """argv и среда живого процесса из /proc: argv (права 444) видит `ps` любого пользователя, среду — только владелец."""
    argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1]
    пары = (x.split(b"=", 1) for x in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if b"=" in x)
    return [a.decode() for a in argv], {k.decode(): v.decode() for k, v in пары}


def _вернуть(прежние: dict[str, str | None]) -> None:
    for имя, значение in прежние.items():
        if значение is None:
            os.environ.pop(имя, None)
        else:
            os.environ[имя] = значение


def test_secret_in_declaration_refused():
    """Токен в tunnel.yaml (файл с правами чтения для всех) — отказ двери с путём ключа и без значения; пустой ключ (так
    он лежит в машинном файле владельца) проходит. `declared_hostname` — та же дверь: старт сервера (`_allowed_hosts`)
    получает тот же отказ."""
    from core.contracts import ContractError
    from core.transport.tunnel import declared_hostname
    секрет = secrets.token_urlsafe(24)
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "tunnel.yaml"
        cfg.write_text(f"mode: named\nhostname: mcp.example.com\ntunnel_token: {секрет}\n", encoding="utf-8")
        for кто, вызов in (("туннель", lambda: CloudflaredTunnel(port=8080, config_path=cfg)),
                           ("declared_hostname — старт сервера", lambda: declared_hostname(cfg))):
            try:
                вызов()
                check(f"секрет в tunnel.yaml → отказ двери: {кто}", False, "отказа нет")
            except ContractError as отказ:
                check(f"секрет в tunnel.yaml → отказ двери: {кто}",
                      отказ.code == "DECLARATION_INVALID"
                      and отказ.facts.get("details") == "tunnel_token (secret_in_declaration)",
                      (отказ.code, отказ.facts.get("details")))
                след = repr((str(отказ), отказ.message, отказ.reason, отказ.facts, отказ.errors))
                check(f"значения секрета в отказе нет: {кто}", секрет not in след, "текст, message, reason, facts, errors")
        cfg.write_text("mode: named\nhostname: mcp.example.com\ntunnel_token: ''\n", encoding="utf-8")
        try:
            CloudflaredTunnel(port=8080, config_path=cfg)
            check("пустой tunnel_token проходит дверь (машинный файл владельца)",
                  declared_hostname(cfg) == "mcp.example.com", declared_hostname(cfg))
        except ContractError as отказ:
            check("пустой tunnel_token проходит дверь (машинный файл владельца)", False, (отказ.code, отказ.facts))


def test_real_binary_takes_token_from_env():
    """Настоящий cloudflared, если стоит: неверный токен из среды ребёнка — его собственный отказ, непоправимый с первой
    попытки. Сети нет: токен разбирается до соединения с краем. Без токена в среде бинарь сказал бы другое («requires
    the ID or name»), поэтому текст отказа и есть улика, что среда дошла."""
    бинарь = shutil.which("cloudflared")
    if not бинарь:
        print("── не судится: cloudflared не установлен — отказ настоящего бинаря спросить не у кого")
        return
    t = CloudflaredTunnel(port=8080); t.mode = "named"; t.binary = бинарь; t.first_attempts = 2
    t.tunnel_token = "не-токен-" + secrets.token_hex(4)
    t._sleep_interruptible = lambda _: None
    порождено = []
    порождать = t._spawn

    def считая():
        порождено.append(1)
        return порождать()
    t._spawn = считая
    try:
        t.start()
        check("настоящий cloudflared: неверный токен из среды — отказ", False, "туннель поднялся")
    except TunnelError as e:
        check("настоящий cloudflared прочёл токен из среды ребёнка и отказал своим текстом",
              "отказал" in str(e) and "token is" in str(e).lower(), str(e)[:90])
        check("неверный токен непоправим: одна попытка из двух", len(порождено) == 1, len(порождено))
    finally:
        t.stop()


def main():
    test_command_building()
    test_readiness_event_driven()
    test_supervisor_backoff_and_status()
    test_cancel_is_not_failure()
    test_ready_has_a_limit()
    test_first_attempt_retries()
    test_probe_names_the_responder()
    test_probe_refuses_foreign_scheme()
    test_probe_through_door()
    test_приёмка_не_считает_непроверенное_пройденным()
    test_приёмка_поток_и_уборка()
    test_приёмка_каталог_и_исполнители()
    test_приёмка_концы_и_бинари()
    test_каталог_одна_дверь()
    test_secret_reaches_child_by_env()
    test_secret_in_declaration_refused()
    test_real_binary_takes_token_from_env()
    print()
    passed, total = sum(results), len(results)
    print(f"ИТОГО: {passed}/{total} проверок пройдено")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
