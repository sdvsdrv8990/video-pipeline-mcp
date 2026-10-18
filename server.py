"""
server.py — точка входа MCP-сервера: JSON-RPC от Claude AI Web через туннель.

Проверки запроса идут в порядке «дешёвые и отбивающие браузер — раньше разбора тела»: их задача
отсечь класс угроз, который не должен доехать до движка. Переставить их местами значит впустить
чужую вкладку глубже, чем она вправе дойти.
"""

import asyncio
import contextlib
import errno
import re
import subprocess
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent))

from core.contracts import ContractError
from core.declaration import Declaration, Declared, declarations_root, instance_home, workspace_root
from core.declaration import contents as declaration_contents
from core.declaration_schemas import RESOURCES, Resources
from core.engine import Engine
from core.environment import child_spawn
from core.observability import Logs, wire
from core.firewall import Firewall, FirewallRequest, FirewallDecision
from core.transport import Transport
from core.transport.transport import declared_transport
from core.transport.tunnel import declared_hostname
from core.reactions import Reactions
from core.auth import (ENV_FILE, check_auth, digest_fingerprint, ensure_digest, rotate_token,
                       token_file_mode, token_fingerprint)
from core.ids import IDGenerator
from core.owner_only import superuser
from core.state import StateManager
# A2: движки, маппинг исключений ядра и аннотации MCP живут в контексте групп.
from tools._context import build_context
from tools import excel, filesystem, media, memory, montage, search, structure, tables, tasks, uniqueness


# ═══ КОНФИГУРАЦИЯ ═══

# По умолчанию слушаем localhost — публичный доступ идёт только через туннель.
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "8080"))

BASE_PATH = Path(__file__).parent
# Рабочая область переопределяется переменной среды по той же причине, что хост и порт: иначе
# поднять сервер рядом с боевым нечем, и тест против ЖИВОГО сервера правил бы настоящие данные
# канала. Значение резолвится и создаётся — «указал и не создал» иначе падало бы позже и глуше.
WORKSPACE_PATH = workspace_root()
# Каталог деклараций переопределяется по той же причине, что и рабочая область: конформанс-прогон
# обязан идти по БОЕВОМУ конфигу, но со своим порогом частоты — иначе сторонний клиент, честно
# долбящий 30 сценариев подряд, банит сам себя, и «несоответствие спеке» оказывается блокировкой.
CONFIG_PATH = declarations_root()
# Корень журналов переопределяется средой по той же причине: тестовый сервер рядом с боевым пишет свой след и журнал
# фактов, а не дописывает журнал владельца. Нет переменной — каталог журналов пользователя этой ОС (observability.yaml).
LOGS_OVERRIDE = os.environ.get("MCP_LOGS")

# Спека транспорта MCP требует валидировать Origin на ВСЕХ соединениях. Пустой список
# больше не значит «не проверять»: браузер прикладывает Origin к любому кросс-доменному запросу,
# поэтому запрос С чужим Origin — чужая вкладка. Список РАСШИРЯЕТ разрешённые браузерные источники
# сверх петли, но не отключает клиента-бэкенда: тот Origin не шлёт вовсе (решение владельца).
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]

# Тело принимаем только этим типом. Три «простых» типа (text/plain, form-urlencoded,
# multipart) браузер шлёт БЕЗ preflight — на них и держится CSRF из чужой вкладки.
JSON_CONTENT_TYPE = "application/json"

# Заголовки ответа не-UI сервера: сниффинг запрещён, страница из ответа ничего не грузит и
# не встраивается, промежуточные кэши ответ не хранят, версия сервера наружу не объявляется.
RESPONSE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Server": "mcp",
}

# Host валидируется ВСЕГДА (анти-DNS-rebinding) — петля + hostname туннеля + MCP_ALLOWED_HOSTS.
# 0.0.0.0 не входит намеренно: это имя браузерного обхода localhost («0.0.0.0 Day»), не bind раннера.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _allowed_hosts() -> set:
    """Кому разрешено называться нашим хостом: петля + объявленный туннель + явный список. Объявления туннеля нет —
    машина без туннеля, его hostname нет; объявление битое — отказ двери туннеля с файлом и местом. Hostname нужен и без
    `--tunnel`: туннель поднимают и отдельно (deploy_tunnel_server.sh)."""
    hosts = set(_LOOPBACK_HOSTS)
    hosts |= {h.strip().lower() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()}
    tunnel_cfg = CONFIG_PATH / "tunnel.yaml"
    declared = declared_hostname(tunnel_cfg if tunnel_cfg.exists() else None)
    if declared:
        hosts.add(str(declared).strip().lower())
    return hosts

# Значения, о которых уже сообщили: чужая вкладка в цикле иначе забьёт консоль владельца.
_REPORTED_ORIGINS: set = set()

# Ключ сервер выдаёт себе сам (см. core/auth). Пустой токен больше НЕ означает
# «auth отключена» — это fail-closed: без ключа сервер не стартует, если явно не разрешено.
ENV_PATH = BASE_PATH / ENV_FILE
# Сервер держит ОТПЕЧАТОК ключа, не значение — сравнение идёт по хэшу.
MCP_AUTH_DIGEST = ""
# Явная и громкая калитка для локальной разработки и тестов (по умолчанию закрыта).
ALLOW_NO_AUTH = os.environ.get("MCP_ALLOW_NO_AUTH", "") == "1"
# Суперпользователь превращает любую дыру инструмента в захват машины; калитка — только для одноразовой машины.
ALLOW_ROOT = os.environ.get("MCP_ALLOW_ROOT", "") == "1"


# ═══ ХЕЛПЕРЫ ═══

def _load_yaml(path: Path) -> dict:
    """Объявление сервера. Нет файла — отказ: пустой dict молча заменял бы декларацию дефолтами,
    и сервер поднимался бы не тем, чем объявлен."""
    return Declaration(path, ContractError, f"«{path.stem}»").data


def _load_resources(path: Path) -> Resources:
    """Классы ресурсов и класс каждого инструмента — по схеме у двери. Битая декларация — отказ при старте, а не
    работа без пределов: тяжёлое без мест и снятия с цикла морозит сервер целиком."""
    return Declared(path, ContractError, "«resources»", schema=RESOURCES.schema).model


def create_server() -> tuple[Engine, Transport, Firewall]:
    """Создание и настройка сервера."""
    # Реестр реакций подключается к движку — иначе он лишь объект в памяти,
    # а ошибки уходят наружу без class/recovery.
    reactions = Reactions(CONFIG_PATH / "server_reactions.yaml")

    # Файрвол — по декларации через дверь: битый раздел лимита — отказ старта с путём ключа, а не правила по умолчанию.
    firewall = Firewall.declared(CONFIG_PATH / "firewall.yaml")
    id_generator = IDGenerator()
    # Журналы под одним объявленным корнем: след вызовов (без него отказ в живой сессии не оставляет условий, по которым
    # его повторяют) и журнал фактов — его state_manager пишет по фактам, которые ему передаёт движок.
    logs = Logs.from_declaration(_load_yaml(CONFIG_PATH / "observability.yaml"), LOGS_OVERRIDE)
    logs.hear()     # причина отказа с номером события и предупреждения — в журнал фактов, куда отсылает ответ клиенту
    print(f"📓 Журналы: {logs.root}")
    state_manager = StateManager(WORKSPACE_PATH, journal=logs.session)

    # Объявление ресурсов: какие вызовы уходят с цикла событий и сколько их идёт разом. Битое — отказ при старте.
    engine = Engine(reactions=reactions, state_manager=state_manager,
                    resources=_load_resources(CONFIG_PATH / RESOURCES.file), trail=logs.trail)

    # Создаём workspace если нет
    WORKSPACE_PATH.mkdir(parents=True, exist_ok=True)

    # Регистрация базовых инструментов
    register_basic_tools(engine, id_generator, state_manager)

    # Транспорт: бюджет ответа клиента — декларация провода в том же каталоге деклараций.
    transport = Transport(engine=engine, firewall=firewall, config_path=CONFIG_PATH)

    return engine, transport, firewall


def register_basic_tools(engine: Engine, id_generator: IDGenerator, state_manager: StateManager):
    """Сборка общих зависимостей групп; сами хендлеры — в tools/<group>/."""
    ctx = build_context(engine, id_generator, state_manager, CONFIG_PATH)

    # Порядок = порядок инструментов в tools/list у клиента; менять без нужды не стоит.
    filesystem.register(engine, ctx)
    memory.register(engine, ctx)
    tables.register(engine, ctx)
    excel.register(engine, ctx)
    structure.register(engine, ctx)
    uniqueness.register(engine, ctx)
    media.register(engine, ctx)
    montage.register(engine, ctx)
    search.register(engine, ctx)
    tasks.register(engine, ctx)


def _origin_admitted(origin: str, allowed: tuple[str, ...]) -> bool:
    """Источник страницы: петля всегда, `allowed` — вдобавок к ней (объявленные в transport.yaml и MCP_ALLOWED_ORIGINS).

    Петлевой Origin удалённый сайт подделать не может — браузер ставит его по адресу самой
    страницы, и DNS-rebinding оставляет там имя атакующего. Спека требует петлю принимать.
    """
    if origin in allowed:
        return True
    return (urlsplit(origin).hostname or "\x00") in {h.strip("[]") for h in _LOOPBACK_HOSTS}


def _report_origin(origin: str) -> None:
    """Отказ по Origin — в консоль владельца: иначе отрезанный легитимный клиент молчит."""
    if origin not in _REPORTED_ORIGINS and len(_REPORTED_ORIGINS) < 20:
        _REPORTED_ORIGINS.add(origin)
        print(f"🔎 [origin] запрос из браузера отклонён: {origin[:100]} "
              f"(разрешить — MCP_ALLOWED_ORIGINS)")


def _jsonrpc_refusal(request_id, code: int, detail: dict) -> dict:
    """Отказ до диспетчера в конверте JSON-RPC: число — протоколу, `data` — ErrorDetail реестра (код, класс, рецепт,
    факты), та же форма, что `error` в ответе инструмента."""
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": detail["message"], "data": detail}}



def _port_holder(port: int) -> str:
    """Кто слушает порт — именем и pid. Не удалось узнать — молчим, а не выдумываем."""
    try:
        готово = subprocess.run(["ss", "-ltnp"], capture_output=True, timeout=5, **child_spawn("replace"))
    except (OSError, subprocess.SubprocessError):
        return ""
    for строка in готово.stdout.splitlines():
        if f":{port} " not in строка:
            continue
        имя = re.search(r'users:\(\("([^"]+)",pid=(\d+)', строка)
        return f" процессом {имя.group(1)} (pid {имя.group(2)})" if имя else " другим процессом"
    return ""


async def run_server(host: str = HOST, port: int = PORT, use_tunnel: bool = False):
    """Запуск сервера.

    Args:
        host: Хост
        port: Порт
        use_tunnel: Поднять туннель вместе с сервером
    """
    global MCP_AUTH_DIGEST
    if not ALLOW_NO_AUTH:
        MCP_AUTH_DIGEST, issued = ensure_digest(ENV_PATH)
        if not MCP_AUTH_DIGEST:
            print(f"ОТКАЗ СТАРТА: не удалось получить ключ доступа ({ENV_PATH}).", file=sys.stderr)
            print("Задай MCP_AUTH_TOKEN или дай серверу право создать .env; "
                  "работа без ключа — только с MCP_ALLOW_NO_AUTH=1.", file=sys.stderr)
            raise SystemExit(2)
        if issued:
            print("─" * 60)
            print(f"Выпущен ключ доступа (в {ENV_FILE} — только хэш, права 0600), "
                  f"отпечаток {token_fingerprint(issued)}")
            if sys.stdout.isatty():
                # Единственный момент, когда значение вообще существует: дальше его негде взять.
                print(f"  {issued}")
                print("Вставь в коннекторе Claude AI Web: Request headers →")
                print("  authorization: Bearer <ключ>    (или x-api-key: <ключ>)")
                print("СОХРАНИ СЕЙЧАС: восстановить нельзя, только перевыпустить.")
            else:
                print("Запуск не из терминала — значение не печатаем, чтобы не осело в логе.")
                print("Выпусти ключ заново в терминале: server.py --rotate-key")
            print("─" * 60)

    # Битое объявление туннеля — отказ старта: сервер, отвечающий 403 на каждый запрос через туннель, хуже неподнятого.
    allowed_hosts = _allowed_hosts()
    # Кто может звать из браузера: объявленные источники провода и добавка машины из среды.
    origins = (*declared_transport(CONFIG_PATH).origins.allowed, *ALLOWED_ORIGINS)
    engine, transport, firewall = create_server()

    print("=== MCP-сервер видеопайплайна ===")
    print(f"Хост: {host}")
    print(f"Порт: {port}")
    print(f"Workspace: {WORKSPACE_PATH}")
    print(f"Инструментов: {len(engine.tools)}")
    print(f"Файрвол: активен (config: {'загружен' if (CONFIG_PATH / 'firewall.yaml').exists() else 'дефолт'})")
    print(f"Браузер: Origin петлевой{' + ' + ', '.join(origins) if origins else ''}"
          f" (transport.yaml, MCP_ALLOWED_ORIGINS), без Origin — пускаем, тело только {JSON_CONTENT_TYPE}")
    if ALLOW_NO_AUTH:
        print("Аутентификация: ⚠ ОТКЛЮЧЕНА (MCP_ALLOW_NO_AUTH=1) — только локальная разработка")
    else:
        print(f"Аутентификация: активна (Authorization: Bearer / X-Api-Key), "
              f"отпечаток {digest_fingerprint(MCP_AUTH_DIGEST)} в {ENV_FILE} (0600, значения нет)")
    print()

    from aiohttp import web

    def _refuse(level: str, code: str, rpc_code: int, status: int, request: "web.Request", facts: dict | None = None,
                note: str = "", request_id=None, rpc: str = "", headers: dict | None = None) -> "web.Response":
        """Отказ до диспетчера: пишем в след и отвечаем. Одна точка на все уровни.

        Веток отказа тут почти десяток, и запись в каждой держалась бы дисциплиной — десятую
        допишут без неё. Уровень записывается явно: на периметре сервер ещё не понял запрос как
        MCP, и воспроизводится такой отказ формой соединения, а не именем инструмента. Текст, класс и
        рецепт — реестр по коду, тем же переводом, что у инструмента; число JSON-RPC остаётся протоколу.
        `note` — причина для следа владельца (в ней адрес клиента), в ответ она не идёт.
        """
        detail = engine.failure(level, ContractError(code, facts=facts)).error
        assert detail is not None
        if engine.trail is not None:
            engine.trail.refusal(level, code, note or detail.message, rpc=rpc, args={
                "Host": request.headers.get("Host", ""),
                "Origin": request.headers.get("Origin", ""),
                "Content-Type": request.headers.get("Content-Type", ""),
                "Authorization": request.headers.get("Authorization", ""),
                "X-Api-Key": request.headers.get("X-Api-Key", ""),
                "ip": request.remote or "",
            })
        data = {key: value for key, value in detail.model_dump().items() if value is not None}
        return web.json_response(_jsonrpc_refusal(request_id, rpc_code, data), status=status, headers=headers or {})

    async def handle_jsonrpc(request: "web.Request") -> "web.Response":
        """Обработка JSON-RPC запросов: Origin → Auth → Firewall → Transport."""
        # Host — первым, до всего остального. Чужое имя хоста означает, что до нас
        # достучались через браузер жертвы (DNS-rebinding), и разбирать такой запрос незачем.
        host = (request.headers.get("Host") or "").strip().lower()
        # Запятая в Host — склейка двух заголовков посредником; разбирать первое значение
        # значит верить отправителю в выборе, какое из имён считать нашим.
        if "," in host or (host.rsplit(":", 1)[0].strip("[]") not in {h.strip("[]") for h in allowed_hosts}
                           and host not in allowed_hosts):
            return _refuse("perimeter", "HOST_FORBIDDEN", -32002, 403, request)

        # Origin проверяется всегда, когда он есть. Отсутствие заголовка = не браузер:
        # кросс-доменный запрос без Origin браузер отправить не может, а CSRF простым запросом
        # отбивает требование application/json ниже.
        origin = request.headers.get("Origin")
        if origin is not None and not _origin_admitted(origin, origins):
            _report_origin(origin)
            return _refuse("perimeter", "ORIGIN_FORBIDDEN", -32002, 403, request)

        # Чужой тип тела = запрос, который браузер отправляет без preflight.
        if request.content_type != JSON_CONTENT_TYPE:
            return _refuse("perimeter", "MEDIA_TYPE_UNSUPPORTED", -32600, 415, request,
                           facts={"required": JSON_CONTENT_TYPE})

        try:
            raw_request = await request.text()
        except Exception:
            return _refuse("perimeter", "REQUEST_UNREADABLE", -32700, 400, request)

        # Fail-closed — не можем распарсить/проверить → блокируем, а не пропускаем.
        # Наружу — только место разбора: текст исключения цитирует тело.
        try:
            req_data = json.loads(raw_request)
        except json.JSONDecodeError as e:
            return _refuse("perimeter", "REQUEST_MALFORMED", -32700, 400, request,
                           facts={"line": e.lineno, "column": e.colno})

        # Аутентификация ДО файрвола. Принимаем оба заголовка из allowlist коннектора
        # Claude AI Web (Authorization: Bearer / X-Api-Key) — иначе клиент не сможет прислать ключ.
        if not ALLOW_NO_AUTH:
            deny = check_auth(request.headers, MCP_AUTH_DIGEST)
            if deny:
                return _refuse("identity", deny, -32001, 401, request,
                               request_id=req_data.get("id") if isinstance(req_data, dict) else None,
                               rpc=req_data.get("method", "") if isinstance(req_data, dict) else "")

        if firewall:
            try:
                fw_request = FirewallRequest(
                    ip=request.remote or "127.0.0.1",
                    method=req_data.get("method", "") if isinstance(req_data, dict) else "",
                    params=req_data.get("params", {}) if isinstance(req_data, dict) else {},
                    timestamp=time.time()
                )
                fw_result = firewall.check(fw_request)
            except Exception as e:
                # Любой сбой firewall = блокировка (fail-closed), не пропуск.
                # Причина сбоя — в консоль владельца; наружу только факт отказа, иначе
                # клиент получает текст внутреннего исключения (имена типов, значения полей).
                print(f"⛔ [firewall] сбой проверки, запрос отклонён: {e!r}")
                return _refuse("firewall", "FIREWALL_UNAVAILABLE", -32000, 403, request,
                               request_id=req_data.get("id") if isinstance(req_data, dict) else None,
                               rpc=req_data.get("method", "") if isinstance(req_data, dict) else "")

            # RATE_LIMIT и BLOCK — разные HTTP-коды и коды реестра; число JSON-RPC лимита то же, что у ключа.
            if fw_result.decision == FirewallDecision.BLOCK:
                return _refuse("firewall", "REQUEST_BLOCKED", -32000, 403, request, facts={"rule": fw_result.rule},
                               note=fw_result.reason,
                               request_id=req_data.get("id") if isinstance(req_data, dict) else None,
                               rpc=fw_request.method)
            if fw_result.decision == FirewallDecision.RATE_LIMIT:
                return _refuse("firewall", "RATE_LIMITED", -32001, 429, request,
                               facts={"retry_after_sec": fw_result.retry_after_sec}, note=fw_result.reason,
                               request_id=req_data.get("id") if isinstance(req_data, dict) else None,
                               rpc=fw_request.method, headers={"Retry-After": str(fw_result.retry_after_sec)})
            if fw_result.decision != FirewallDecision.ALLOW:
                return _refuse("firewall", "REQUEST_BLOCKED", -32000, 403, request, facts={"rule": fw_result.rule},
                               note=fw_result.reason,
                               request_id=req_data.get("id") if isinstance(req_data, dict) else None,
                               rpc=fw_request.method)
            # Пропуск С причиной = сигнал log-only (деструктивный инструмент). Печатаем его:
            # иначе сигнал живёт только в счётчике, который в проде не читает никто.
            if fw_result.reason:
                print(f"🔎 [firewall] подозрительно: {fw_result.reason}")

        # Лог факта подключения клиента: MCP-метод `initialize` = новый сеанс.
        # Это авторитетный сигнал, что Claude AI Web достучался до сервера через туннель.
        if isinstance(req_data, dict) and req_data.get("method") == "initialize":
            params = req_data.get("params") or {}
            client = params.get("clientInfo") or {}
            print(
                f"✅ Claude AI Web подключился: {client.get('name', 'unknown')} "
                f"{client.get('version', '?')} "
                f"(MCP protocol {params.get('protocolVersion', '?')}, ip={request.remote or '?'})"
            )

        # Обработка запроса. None → это была нотификация → HTTP 202 без тела.
        response_text = await transport.handle_request(raw_request)
        if response_text is None:
            return web.Response(status=202)
        return web.Response(text=response_text, content_type="application/json")

    @web.middleware
    async def security_headers(request: "web.Request", handler):
        """Заголовки навешиваются и на отказы — 405/403 тоже уходят в браузер."""
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            exc.headers.update(RESPONSE_HEADERS)
            raise
        response.headers.update(RESPONSE_HEADERS)
        return response

    # Только POST: GET/OPTIONS отвечают 405 без CORS-заголовков, и preflight чужой страницы
    # проваливается — это и запрещает браузеру читать наши ответы (спека транспорта MCP).
    # Отказ маршрутизатора (чужой метод, неизвестный путь) наш обработчик не видит вовсе —
    # замер показал, что такие запросы уходили бесследно. Слушатель разбора протокола ловит то,
    # что отвергнуто ещё раньше: битый HTTP, разбухший заголовок.
    wire.attach(engine.trail)
    app = web.Application(middlewares=[security_headers, wire.route_watch(engine.trail)])
    app.router.add_post("/", handle_jsonrpc)
    app.router.add_post("/mcp", handle_jsonrpc)

    # Обрыв клиента отменяет его хендлер (aiohttp по умолчанию — нет): брошенное ожидание `task_status(wait)` не держит
    # слушателя узла; вызов, снятый с цикла в поток, держит место до конца потока (Engine._invoke).
    runner = web.AppRunner(app, handler_cancellation=True)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
    except OSError as beda:
        if beda.errno != errno.EADDRINUSE:
            raise
        # Трейс называет наши строки, а не ВИНОВНИКА: без имени держателя оператор идёт читать
        # код вместо того, чтобы остановить прошлый запуск.
        держит = _port_holder(port)
        print(f"\nПорт {port} занят{держит} — сервер не поднялся.", file=sys.stderr)
        print("Останови прошлый запуск: ./run.sh --stop\n", file=sys.stderr)
        raise SystemExit(1) from None
    socket_task = wire.start_socket_watch(runner, engine.trail, _load_yaml(CONFIG_PATH / "observability.yaml"))

    print(f"Сервер запущен на http://{host}:{port}")
    print(f"JSON-RPC endpoint: http://{host}:{port}/mcp")

    # Поднимаем туннель вместе с сервером (одной командой).
    tunnel = None
    tunnel_status_str = "нет"
    if use_tunnel:
        from core.transport.tunnel import CloudflaredTunnel, TunnelCancelled
        tunnel = CloudflaredTunnel(port=port, config_path=CONFIG_PATH / "tunnel.yaml")
        try:
            # В потоке: подъём длится попытками по сроку, а цикл событий должен услышать Ctrl+C сразу (отмена задачи).
            public_url = await asyncio.to_thread(tunnel.start)
            # Статус спрашивается ДВАЖДЫ и у разных свидетелей: лог туннеля говорит, что ребро
            # поднялось, а проба — доходит ли запрос снаружи до нашего процесса. Одного лога мало:
            # край поставщика отдаёт 530 при живом с виду туннеле.
            st = tunnel.status()
            улика = tunnel.probe()
            if st["connected"] and улика["дошло"]:
                tunnel_status_str = f"поднят → {public_url}/mcp"
                print()
                print(f"🌐 Публичный URL (вставь в коннектор Claude): {public_url}/mcp")
                print(f"   проверено снаружи: {улика['текст']}")
            elif st["connected"]:
                tunnel_status_str = f"ребро есть, снаружи НЕ отвечает ({улика['текст']})"
                print()
                print("⚠️  ПУБЛИЧНОГО АДРЕСА НЕТ: туннель соединился, но снаружи запрос не "
                      "доходит.")
                print(f"   проверено: {улика['текст']}")
                print(f"   локально сервер работает: http://{host}:{port}/mcp")
                # Рекомендация: quick → named для продакшена.
                if "trycloudflare.com" in (public_url or ""):
                    print()
                    print("💡 Рекомендация: quick-режим подходит для разработки.")
                    print("   Для продакшена используй named-режим:")
                    print("   • токен: экспорт MCP_TUNNEL_TOKEN из дашборда Cloudflare")
                    print("   • credentials: домен + файл credentials (см. tunnel.py)")
            else:
                tunnel_status_str = "процесс жив, но соединение НЕ установлено"
                print()
                print("⚠️  Туннель запущен, но соединение не установлено.")
                if st["last_error"]:
                    print(f"   Причина: {st['last_error']}")
                print(f"   Uptime: {st['uptime_sec']}s | Попыток перезапуска: {st['attempts']}")
                print()
                print("   Режимы работы Cloudflare Tunnel:")
                print("   • quick (без аккаунта): работает сразу, URL эфемерный (*.trycloudflare.com)")
                print("   • named + token: нужен токен из дашборда (env MCP_TUNNEL_TOKEN)")
                print("   • named + credentials: нужен домен + credentials файл")
        except (TunnelCancelled, asyncio.CancelledError) as e:
            # Отмена оператора, а не поломка: Ctrl+C ушёл всей группе процессов — первым его слышит процесс туннеля
            # (отказ подъёма) или сервер (отмена задачи). Флаг остановки снимает подъём в потоке на любой фазе, иначе
            # он повторял бы попытки и порождал cloudflared уже после Ctrl+C.
            tunnel.stop()
            print(f"\n⏹  Остановка во время подъёма туннеля{f' ({e})' if str(e) else ''}.")
            engine.stopping = True              # отменённые остановкой вызовы след пишет остановкой, а не обрывом клиента
            try:
                await runner.cleanup()
            except (OSError, RuntimeError) as сбой:
                print(f"   (сокет закрылся не чисто — порт {port} мог остаться занятым: {сбой})")
            raise SystemExit(0) from None
        except Exception as e:
            tunnel_status_str = f"ошибка: {e}"
            print(f"⚠️  Туннель не поднят: {e}")
            print("   ПУБЛИЧНОГО АДРЕСА НЕТ — Claude AI Web подключиться не сможет.")
            print(f"   Локально сервер работает: http://{host}:{port}/mcp")
            tunnel = None

    # Публичный адрес без ключа пускает любого, кто его узнает: Origin/Host и файрвол есть, идентификации нет.
    if ALLOW_NO_AUTH and tunnel:
        print()
        print("⚠ АУТЕНТИФИКАЦИЯ ОТКЛЮЧЕНА (MCP_ALLOW_NO_AUTH=1)")
        print("⚠ СЕРВЕР ОТКРЫТ ЧЕРЕЗ ТУННЕЛЬ — публичный адрес пустит любого, кто его узнает")
        print("⚠ ЭТОТ РЕЖИМ — ТОЛЬКО ДЛЯ РАЗРАБОТКИ: для работы запускай без --no-auth, с ключом в коннекторе")

    # Статус готовности (по спецификации MCP SDK).
    print()
    print(f"Статус: ГОТОВ | Туннель: {tunnel_status_str}")
    print("Для остановки: Ctrl+C")

    try:
        # Мониторинг туннеля: печатаем ТОЛЬКО изменения статуса, а не шум каждые N сек.
        # Восстановление соединения выполняет супервизор в CloudflaredTunnel сам —
        # здесь только наблюдаем его status() и сообщаем переходы в консоль.
        prev = tunnel.status() if tunnel else {}  # dict: блок туннеля ниже под `if not tunnel: continue`

        # Хот-релоад декларативного config без рестарта: «сменился ли файл» — по содержимому, тем же узлом, что у двери
        # деклараций. firewall.yaml → firewall.reload() (fail-closed), server_reactions.yaml →
        # reactions.load(). tunnel.yaml НЕ входит: смена режима/порта требует
        # рестарта туннеля (честно). Код handlers/core тоже требует рестарта.
        reactions = getattr(engine, "reactions", None)
        watched = {
            CONFIG_PATH / "firewall.yaml": "firewall",
            CONFIG_PATH / "server_reactions.yaml": "reactions",
        }
        cfg_seen = {p: declaration_contents(p) for p in watched}

        while True:
            await asyncio.sleep(10)

            # 0) Хот-релоад config по смене содержимого (работает и без туннеля).
            for cfg_path, kind in watched.items():
                seen = declaration_contents(cfg_path)
                if seen == cfg_seen[cfg_path]:
                    continue
                cfg_seen[cfg_path] = seen  # фиксируем сразу → битый конфиг не ретрайдим каждые 10с
                try:
                    if kind == "firewall" and firewall is not None:
                        firewall.reload_declared(cfg_path)   # битый — отказ двери с путём ключа, прежние правила целы
                        print("♻️  [config] firewall.yaml перезагружен без рестарта")
                    elif kind == "reactions" and reactions is not None:
                        reactions.load(cfg_path)
                        print("♻️  [config] server_reactions.yaml перезагружен без рестарта")
                except Exception as e:
                    # Отказ двери — текстом реестра (файл и путь ключа, без значений); чужое исключение — как есть.
                    refused = engine.refusal(e)
                    why = refused.error.message if refused is not None and refused.error is not None else e
                    print(f"⚠️  [config] {cfg_path.name} НЕ применён: {why} — держим прежнее")

            if not tunnel:
                continue
            st = tunnel.status()

            # 1) Публичный URL сменился (для quick-режима — норма при реконнекте процесса).
            #    Самое важное сообщение: старый адрес в коннекторе Claude уже мёртв.
            if st["public_url"] and st["public_url"] != prev["public_url"]:
                print()
                print(f"🌐 [tunnel] ПУБЛИЧНЫЙ URL ИЗМЕНИЛСЯ → {st['public_url']}/mcp")
                print("   ⚠️  Обнови адрес в коннекторе Claude AI Web — старый больше не отвечает.")
                print()
            # 2) Соединение потеряно.
            if prev["connected"] and not st["connected"]:
                reason = st["last_error"] or "нет соединения"
                print(f"🔴 [tunnel] соединение потеряно (uptime={st['uptime_sec']}s, попыток={st['attempts']}): {reason}")
            # 3) Соединение восстановлено.
            elif not prev["connected"] and st["connected"]:
                print(f"🟢 [tunnel] соединение восстановлено → {st['public_url']}/mcp")
            # 4) Новая ошибка без смены флага connected.
            elif st["last_error"] and st["last_error"] != prev["last_error"]:
                print(f"⚠️  [tunnel] {st['last_error']}")

            prev = st
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        engine.stopping = True                  # отменённые остановкой вызовы след пишет остановкой, а не обрывом клиента
        if tunnel:
            tunnel.stop()
        await wire.stop_socket_watch(socket_task)
        await runner.cleanup()


def main():
    """Главная функция."""
    import argparse

    # Построчная буферизация stdout/stderr: при выводе в файл/пайп (не tty) Python
    # по умолчанию БЛОЧНО буферизует stdout — статусные сообщения (URL туннеля,
    # подключение Claude) зависают в буфере и не видны. line_buffering=True флашит
    # на каждой строке: буфер сохраняется (быстрый вывод), но сообщения не теряются.
    # Кодировка — utf-8, а не локаль: у канала Windows она cp1252, и первый русский print ронял сервер.
    # Не трогаем при отсутствии reconfigure (заглушки stdout в тестах/встраивании).
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(line_buffering=True, encoding="utf-8")

    from core.environment import epilog
    parser = argparse.ArgumentParser(description="MCP-сервер видеопайплайна", epilog=epilog(),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=HOST, help="Хост (по умолчанию: %(default)s)")
    parser.add_argument("--port", type=int, default=PORT, help="Порт (по умолчанию: %(default)s)")
    parser.add_argument("--tunnel", action="store_true", help="Поднять туннель вместе с сервером (D11)")
    parser.add_argument("--rotate-key", action="store_true", help="Перевыпустить ключ доступа и выйти (S1)")
    parser.add_argument("--re-adopt", action="store_true", help="Присвоить рабочую область этому серверу после переноса (S9)")
    args = parser.parse_args()

    # До любой записи: ключ, присвоение области и сервер под суперпользователем оставят файлы, закрытые от обычного запуска.
    if superuser() and not ALLOW_ROOT:
        print("ОТКАЗ СТАРТА: сервер запущен с правами суперпользователя (root; на Windows — администратор с повышением).",
              file=sys.stderr)
        print("Запусти его от обычного пользователя; одноразовая машина (контейнер разработки, ВМ CI) — MCP_ALLOW_ROOT=1.",
              file=sys.stderr)
        raise SystemExit(2)

    if args.re_adopt:
        # Явное подтверждение владельца, что перенос/восстановление — его действие.
        from core.integrity import InstanceIdentity
        from core.ids import LinkRegistry
        ident = InstanceIdentity(instance_home(CONFIG_PATH), WORKSPACE_PATH)
        if not ident.ensure_key():
            print("Подпись недоступна (нет cryptography) — присваивать нечего.", file=sys.stderr)
            return
        reg = LinkRegistry(WORKSPACE_PATH, identity=ident)
        reg.re_adopt()
        print(f"Рабочая область присвоена этому серверу (instance_id {ident.instance_id[:16]}…).")
        print("Прежние подписи считаются недействительными; запись снова разрешена.")
        return

    if args.rotate_key:
        token = rotate_token(ENV_PATH)
        print(f"Новый ключ доступа (в {ENV_FILE} — только хэш, права {oct(token_file_mode(ENV_PATH))}), "
              f"отпечаток {token_fingerprint(token)}")
        if sys.stdout.isatty():
            print(f"  {token}")
            print("Обнови значение в коннекторе Claude AI Web — прежний ключ больше не действует.")
            print("СОХРАНИ СЕЙЧАС: сервер хранит только хэш, показать повторно неоткуда.")
        else:
            print("Запуск не из терминала — значение не печатаем. Повтори ротацию в терминале.")
        return

    try:
        asyncio.run(run_server(args.host, args.port, use_tunnel=args.tunnel))
    except ContractError as refused:
        # Объявленный отказ при старте — код, файл и место одной строкой вместо трассировки: чинить файл, а не код.
        said = (refused.facts.get("file"), refused.facts.get("details"), refused.message, refused.reason)
        print(" · ".join([f"ОТКАЗ СТАРТА: {refused.code}", *(str(part) for part in said if part)]), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
