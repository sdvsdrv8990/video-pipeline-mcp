"""
server.py — Точка входа MCP-сервера видеопайплайна

## Назначение
Принимает JSON-RPC запросы от Claude через туннель.
Обрабатывает через Auth → Firewall → Engine → Tools.

## Запуск
    python server.py            # сервер (127.0.0.1:8080)
    python server.py --tunnel   # сервер + Cloudflare-туннель одной командой

## Порт
    8080 (по умолчанию), слушает 127.0.0.1 — наружу смотрит только туннель.

## Инструменты (52, шесть групп — A2)
    Хендлеры живут в tools/<group>/: filesystem (12) · memory (2) · tables (13) ·
    excel (17) · structure (5) · search (3). Общие зависимости — tools/_context.py,
    состав инвентаря зафиксирован tests/quick/tools_inventory.golden.json.

## Изменения аудита
- D1: safe-join путей fs_* (containment внутри workspace/)
- D2: загрузка config/firewall.yaml в Firewall(cfg)
- D3: bearer-аутентификация (MCP_AUTH_TOKEN) ДО файрвола
- D4: реестр реакций (server_reactions.yaml) подключён в Engine
- D10: fail-closed при ошибке парсинга/сбое firewall
- D12: bind 127.0.0.1 + валидация Origin
- D11: запуск туннеля вместе с сервером (--tunnel)
"""

import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path

import yaml

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent))

from core.engine import Engine
from core.firewall import Firewall, FirewallRequest, FirewallDecision
from core.transport import Transport
from core.reactions import Reactions
from core.ids import IDGenerator
from core.state import StateManager
# A2: движки, маппинг исключений ядра и аннотации MCP живут в контексте групп.
from tools._context import build_context
from tools import excel, filesystem, memory, search, structure, tables


# ═══ КОНФИГУРАЦИЯ ═══

# D12: по умолчанию слушаем localhost — публичный доступ идёт только через туннель.
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "8080"))

BASE_PATH = Path(__file__).parent
WORKSPACE_PATH = BASE_PATH / "workspace"
CONFIG_PATH = BASE_PATH / "config"

# D12: если задан — валидируем заголовок Origin (анти-DNS-rebinding).
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]

# D3: bearer-токен для аутентификации. Если не задан — auth отключена (локальная разработка).
MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")


# ═══ ХЕЛПЕРЫ ═══

def _load_yaml(path: Path) -> dict:
    """Безопасное чтение YAML-конфига (пустой dict, если файла нет)."""
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def create_server():
    """Создание и настройка сервера.

    Returns:
        Tuple[Engine, Transport, Firewall]
    """
    # D2: реально загружаем конфиг файрвола (раньше игнорировался).
    firewall_config = _load_yaml(CONFIG_PATH / "firewall.yaml")

    # D4: реестр реакций подключаем к движку (раньше висел мёртвым объектом).
    reactions = Reactions(CONFIG_PATH / "server_reactions.yaml")

    firewall = Firewall(firewall_config)
    id_generator = IDGenerator()
    state_manager = StateManager(WORKSPACE_PATH)

    # D24: state_manager передаётся в engine для логирования facts в _SESSION_LOG.
    engine = Engine(reactions=reactions, state_manager=state_manager)

    # Создаём workspace если нет
    WORKSPACE_PATH.mkdir(parents=True, exist_ok=True)

    # Регистрация базовых инструментов
    register_basic_tools(engine, id_generator, state_manager)

    # Транспорт
    transport = Transport(engine=engine, firewall=firewall)

    return engine, transport, firewall


def register_basic_tools(engine: Engine, id_generator: IDGenerator, state_manager: StateManager):
    """Регистрация базовых инструментов: контекст + шесть групп (A2).

    Сами хендлеры живут в tools/<group>/; здесь только сборка общих зависимостей
    и порядок регистрации (он же порядок инструментов в tools/list).

    Args:
        engine: Движок инструментов
        id_generator: Генератор ID
        state_manager: Менеджер состояния
    """
    ctx = build_context(engine, id_generator, state_manager, CONFIG_PATH)

    # Порядок = порядок инструментов в tools/list у клиента; менять без нужды не стоит.
    filesystem.register(engine, ctx)
    memory.register(engine, ctx)
    tables.register(engine, ctx)
    excel.register(engine, ctx)
    structure.register(engine, ctx)
    search.register(engine, ctx)


def _jsonrpc_error(request_id, code: int, message: str) -> dict:
    """Сборка JSON-RPC ошибки (для транспортного уровня)."""
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def run_server(host: str = HOST, port: int = PORT, use_tunnel: bool = False):
    """Запуск сервера.

    Args:
        host: Хост
        port: Порт
        use_tunnel: Поднять Cloudflare-туннель вместе с сервером (D11)
    """
    engine, transport, firewall = create_server()

    print("=== MCP-сервер видеопайплайна ===")
    print(f"Хост: {host}")
    print(f"Порт: {port}")
    print(f"Workspace: {WORKSPACE_PATH}")
    print(f"Инструментов: {len(engine.tools)}")
    print(f"Файрвол: активен (config: {'загружен' if (CONFIG_PATH / 'firewall.yaml').exists() else 'дефолт'})")
    print(f"Аутентификация: {'активна (bearer-токен)' if MCP_AUTH_TOKEN else 'отключена (MCP_AUTH_TOKEN не задан)'}")
    print()

    from aiohttp import web

    async def handle_jsonrpc(request: "web.Request") -> "web.Response":
        """Обработка JSON-RPC запросов: Origin → Auth → Firewall → Transport."""
        # D12: валидация Origin (если сконфигурирован allowlist).
        # D12: fail-closed — запрос БЕЗ Origin при заданном allowlist = блок.
        origin = request.headers.get("Origin")
        if ALLOWED_ORIGINS:
            if not origin or origin not in ALLOWED_ORIGINS:
                return web.json_response(_jsonrpc_error(None, -32002, "Forbidden origin"), status=403)

        try:
            raw_request = await request.text()
        except Exception:
            return web.json_response(_jsonrpc_error(None, -32700, "Cannot read body"), status=400)

        # D10: fail-closed — не можем распарсить/проверить → блокируем, а не пропускаем.
        try:
            req_data = json.loads(raw_request)
        except json.JSONDecodeError as e:
            return web.json_response(_jsonrpc_error(None, -32700, f"Parse error: {e}"), status=400)

        # D3: bearer-аутентификация ДО файрвола. Если MCP_AUTH_TOKEN не задан — пропускаем (локальная разработка).
        if MCP_AUTH_TOKEN:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32001, "AUTH_REQUIRED: Требуется заголовок Authorization: Bearer <token>"),
                    status=401
                )
            token = auth_header[7:]  # strip "Bearer "
            if not secrets.compare_digest(token, MCP_AUTH_TOKEN):
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32001, "AUTH_FAILED: Неверный токен аутентификации"),
                    status=401
                )

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
                # D10: любой сбой firewall = блокировка (fail-closed), не пропуск.
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32000, f"Firewall error (blocked): {e}"),
                    status=403
                )

            # D21: RATE_LIMIT и BLOCK — разные HTTP-коды, чтобы Claude различал.
            if fw_result.decision == FirewallDecision.BLOCK:
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32000, f"Blocked: {fw_result.reason}"),
                    status=403
                )
            if fw_result.decision == FirewallDecision.RATE_LIMIT:
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32001, f"Rate limit exceeded: {fw_result.reason}"),
                    status=429,
                    headers={"Retry-After": "5"}
                )
            if fw_result.decision != FirewallDecision.ALLOW:
                return web.json_response(
                    _jsonrpc_error(req_data.get("id") if isinstance(req_data, dict) else None,
                                   -32000, f"Blocked: {fw_result.reason}"),
                    status=403
                )

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

        # Обработка запроса. None → это была нотификация → HTTP 202 без тела (D13).
        response_text = await transport.handle_request(raw_request)
        if response_text is None:
            return web.Response(status=202)
        return web.Response(text=response_text, content_type="application/json")

    app = web.Application()
    app.router.add_post("/", handle_jsonrpc)
    app.router.add_post("/mcp", handle_jsonrpc)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    print(f"Сервер запущен на http://{host}:{port}")
    print(f"JSON-RPC endpoint: http://{host}:{port}/mcp")

    # D11: поднимаем туннель вместе с сервером (одной командой).
    tunnel = None
    tunnel_status_str = "нет"
    if use_tunnel:
        from core.transport.tunnel import CloudflaredTunnel
        tunnel = CloudflaredTunnel(port=port, config_path=CONFIG_PATH / "tunnel.yaml")
        try:
            public_url = tunnel.start()
            # Проверяем реальный статус соединения (не просто "процесс запущен").
            st = tunnel.status()
            if st["connected"]:
                tunnel_status_str = f"поднят → {public_url}/mcp"
                print()
                print(f"🌐 Публичный URL (вставь в коннектор Claude): {public_url}/mcp")
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
        except Exception as e:
            tunnel_status_str = f"ошибка: {e}"
            print(f"⚠️  Туннель не поднят: {e}")
            print("   Сервер работает локально.")
            tunnel = None

    # Статус готовности (по спецификации MCP SDK).
    print()
    print(f"Статус: ГОТОВ | Туннель: {tunnel_status_str}")
    print("Для остановки: Ctrl+C")

    try:
        # Мониторинг туннеля: печатаем ТОЛЬКО изменения статуса, а не шум каждые N сек.
        # Восстановление соединения выполняет супервизор в CloudflaredTunnel сам —
        # здесь только наблюдаем его status() и сообщаем переходы в консоль.
        prev = tunnel.status() if tunnel else {}  # dict: блок туннеля ниже под `if not tunnel: continue`

        # Хот-релоад декларативного config без рестарта: следим за mtime файлов.
        # firewall.yaml → firewall.reload() (fail-closed), server_reactions.yaml →
        # reactions.load(). tunnel.yaml НЕ входит: смена режима/порта требует
        # рестарта cloudflared (честно). Код handlers/core тоже требует рестарта.
        reactions = getattr(engine, "reactions", None)
        watched = {
            CONFIG_PATH / "firewall.yaml": "firewall",
            CONFIG_PATH / "server_reactions.yaml": "reactions",
        }
        def _mtime(p: "Path") -> float:
            try:
                return p.stat().st_mtime if p.exists() else 0.0
            except OSError:
                return 0.0
        cfg_mtime = {p: _mtime(p) for p in watched}

        while True:
            await asyncio.sleep(10)

            # 0) Хот-релоад config по изменению mtime (работает и без туннеля).
            for cfg_path, kind in watched.items():
                m = _mtime(cfg_path)
                if m == cfg_mtime[cfg_path]:
                    continue
                cfg_mtime[cfg_path] = m  # фиксируем сразу → битый конфиг не ретрайдим каждые 10с
                try:
                    if kind == "firewall":
                        if firewall and firewall.reload(_load_yaml(cfg_path)):
                            print("♻️  [config] firewall.yaml перезагружен без рестарта")
                        else:
                            print("⚠️  [config] firewall.yaml НЕ применён (битый конфиг) — держим прежние правила (fail-closed)")
                    elif kind == "reactions" and reactions is not None:
                        reactions.load(cfg_path)
                        print("♻️  [config] server_reactions.yaml перезагружен без рестарта")
                except Exception as e:
                    print(f"⚠️  [config] {cfg_path.name} НЕ применён: {e} — держим прежнее")

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
        if tunnel:
            tunnel.stop()
        await runner.cleanup()


def main():
    """Главная функция."""
    import argparse

    # Построчная буферизация stdout/stderr: при выводе в файл/пайп (не tty) Python
    # по умолчанию БЛОЧНО буферизует stdout — статусные сообщения (URL туннеля,
    # подключение Claude) зависают в буфере и не видны. line_buffering=True флашит
    # на каждой строке: буфер сохраняется (быстрый вывод), но сообщения не теряются.
    # Не трогаем при отсутствии reconfigure (заглушки stdout в тестах/встраивании).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="MCP-сервер видеопайплайна")
    parser.add_argument("--host", default=HOST, help="Хост (по умолчанию: %(default)s)")
    parser.add_argument("--port", type=int, default=PORT, help="Порт (по умолчанию: %(default)s)")
    parser.add_argument("--tunnel", action="store_true", help="Поднять Cloudflare-туннель вместе с сервером (D11)")
    args = parser.parse_args()

    asyncio.run(run_server(args.host, args.port, use_tunnel=args.tunnel))


if __name__ == "__main__":
    main()
