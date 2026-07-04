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

## Инструменты (4 production)
    fs_get_directory_tree, fs_read_file, fs_create_file, json_read_snapshot

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
from core.paths import safe_resolve


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


def _safe_resolve(path: str) -> Path:
    """D1+D29: разрешение пути с containment внутри workspace/.

    Делегирует в core/paths.safe_resolve (единая точка, G17).
    Сохранён как обёртка для обратной совместимости с fs_* хендлерами.

    Args:
        path: Путь относительно workspace/

    Returns:
        Абсолютный Path внутри workspace/

    Raises:
        ValueError: если путь выходит за пределы workspace/
    """
    return safe_resolve(path, WORKSPACE_PATH)


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
    """Регистрация базовых инструментов.

    Args:
        engine: Движок инструментов
        id_generator: Генератор ID
        state_manager: Менеджер состояния
    """

    # ═══ ХЕНДЛЕРЫ ФАЙЛОВОЙ СИСТЕМЫ ═══

    async def fs_get_directory_tree(path: str = ".") -> "ToolResult":
        """Получение дерева каталогов."""
        from core.contracts import ToolResult, ErrorDetail, Recovery, Fact
        try:
            target = _safe_resolve(path)
        except ValueError:
            return ToolResult(status="error", error=ErrorDetail(code="PATH_ESCAPE", message=f"Path escapes workspace: {path}", recovery=Recovery(reason="Используй путь внутри workspace")))
        if not target.exists():
            return ToolResult(status="error", error=ErrorDetail(code="PATH_NOT_FOUND", message=f"Path not found: {path}", recovery=Recovery(reason="Проверь путь")))
        def build_tree(p: Path) -> dict:
            tree = {}
            for item in sorted(p.iterdir()):
                if item.is_dir():
                    tree[item.name + "/"] = build_tree(item)
                else:
                    tree[item.name] = {"size": item.stat().st_size}
            return tree
        return ToolResult(status="success", data=build_tree(target), facts=[Fact(type="DirectoryTree", data={"path": path})])

    async def fs_read_file(path: str) -> "ToolResult":
        """Чтение файла."""
        from core.contracts import ToolResult, ErrorDetail, Recovery, Fact
        try:
            target = _safe_resolve(path)
        except ValueError:
            return ToolResult(status="error", error=ErrorDetail(code="PATH_ESCAPE", message=f"Path escapes workspace: {path}", recovery=Recovery(reason="Используй путь внутри workspace")))
        if not target.exists():
            return ToolResult(status="error", error=ErrorDetail(code="FILE_NOT_FOUND", message=f"File not found: {path}", recovery=Recovery(reason="Создай файл через fs_create_file")))
        content = target.read_text(encoding="utf-8")
        return ToolResult(status="success", data={"content": content, "size": len(content)}, facts=[Fact(type="FileRead", data={"path": path, "size": len(content)})])

    async def fs_create_file(path: str, content: str = "") -> "ToolResult":
        """Создание файла."""
        from core.contracts import ToolResult, ErrorDetail, Recovery, Fact
        try:
            target = _safe_resolve(path)
        except ValueError:
            return ToolResult(status="error", error=ErrorDetail(code="PATH_ESCAPE", message=f"Path escapes workspace: {path}", recovery=Recovery(reason="Используй путь внутри workspace")))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(status="success", data={"created": path, "size": len(content)}, facts=[Fact(type="FileCreated", data={"path": path, "size": len(content)})])

    async def fs_write_file(path: str, content: str) -> "ToolResult":
        """Полная перезапись файла."""
        from core.contracts import ToolResult, ErrorDetail, Recovery, Fact
        try:
            target = _safe_resolve(path)
        except ValueError:
            return ToolResult(status="error", error=ErrorDetail(code="PATH_ESCAPE", message=f"Path escapes workspace: {path}", recovery=Recovery(reason="Используй путь внутри workspace")))
        target.parent.mkdir(parents=True, exist_ok=True)
        old_size = target.stat().st_size if target.exists() else 0
        target.write_text(content, encoding="utf-8")
        return ToolResult(status="success", data={"written": path, "size": len(content), "old_size": old_size}, facts=[Fact(type="FileWritten", data={"path": path, "size": len(content)})])

    async def fs_move(source: str, destination: str) -> "ToolResult":
        """Перемещение файла или каталога."""
        from core.contracts import ToolResult, ErrorDetail, Recovery, Fact
        try:
            src, dst = _safe_resolve(source), _safe_resolve(destination)
        except ValueError:
            return ToolResult(status="error", error=ErrorDetail(code="PATH_ESCAPE", message="Path escapes workspace", recovery=Recovery(reason="Используй пути внутри workspace")))
        if not src.exists():
            return ToolResult(status="error", error=ErrorDetail(code="FILE_NOT_FOUND", message=f"Source not found: {source}", recovery=Recovery(reason="Проверь исходный путь")))
        if dst.exists():
            return ToolResult(status="error", error=ErrorDetail(code="FILE_EXISTS", message=f"Destination exists: {destination}", recovery=Recovery(reason="Удали назначение или используй другое имя")))
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return ToolResult(status="success", data={"source": source, "destination": destination}, facts=[Fact(type="FileMoved", data={"source": source, "destination": destination})])

    async def fs_rename(path: str, new_name: str) -> "ToolResult":
        """Переименование файла или каталога."""
        from core.contracts import ToolResult, ErrorDetail, Recovery, Fact
        try:
            src = _safe_resolve(path)
        except ValueError:
            return ToolResult(status="error", error=ErrorDetail(code="PATH_ESCAPE", message=f"Path escapes workspace: {path}", recovery=Recovery(reason="Используй путь внутри workspace")))
        if not src.exists():
            return ToolResult(status="error", error=ErrorDetail(code="FILE_NOT_FOUND", message=f"Not found: {path}", recovery=Recovery(reason="Проверь путь")))
        dst = src.parent / new_name
        try:
            dst = _safe_resolve(str(dst.relative_to(WORKSPACE_PATH)))
        except ValueError:
            return ToolResult(status="error", error=ErrorDetail(code="PATH_ESCAPE", message=f"New name escapes workspace: {new_name}", recovery=Recovery(reason="Используй имя внутри workspace")))
        if dst.exists():
            return ToolResult(status="error", error=ErrorDetail(code="FILE_EXISTS", message=f"Name exists: {new_name}", recovery=Recovery(reason="Выбери другое имя")))
        src.rename(dst)
        return ToolResult(status="success", data={"old_path": path, "new_path": str(dst.relative_to(WORKSPACE_PATH))}, facts=[Fact(type="FileRenamed", data={"old": path, "new": new_name})])

    async def fs_delete(path: str, force: bool = False) -> "ToolResult":
        """Удаление файла или каталога."""
        from core.contracts import ToolResult, ErrorDetail, Recovery, Fact
        import shutil
        try:
            target = _safe_resolve(path)
        except ValueError:
            return ToolResult(status="error", error=ErrorDetail(code="PATH_ESCAPE", message=f"Path escapes workspace: {path}", recovery=Recovery(reason="Используй путь внутри workspace")))
        if not target.exists():
            return ToolResult(status="error", error=ErrorDetail(code="FILE_NOT_FOUND", message=f"Not found: {path}", recovery=Recovery(reason="Проверь путь")))
        if target.is_dir() and not force:
            contents = list(target.iterdir())
            if contents:
                return ToolResult(status="error", error=ErrorDetail(code="DIRECTORY_NOT_EMPTY", message=f"Directory not empty: {path} ({len(contents)} items)", recovery=Recovery(reason="Используй force=true для удаления с содержимым")))
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return ToolResult(status="success", data={"deleted": path}, facts=[Fact(type="FileDeleted", data={"path": path})])

    async def fs_smart_search(directory: str = ".", extension: str = "", keyword: str = "") -> "ToolResult":
        """Поиск файлов по каталогу, расширению и ключевому слову."""
        from core.contracts import ToolResult, ErrorDetail, Recovery, Fact
        try:
            root = _safe_resolve(directory)
        except ValueError:
            return ToolResult(status="error", error=ErrorDetail(code="PATH_ESCAPE", message=f"Path escapes workspace: {directory}", recovery=Recovery(reason="Используй путь внутри workspace")))
        if not root.exists():
            return ToolResult(status="error", error=ErrorDetail(code="FILE_NOT_FOUND", message=f"Directory not found: {directory}", recovery=Recovery(reason="Проверь путь")))
        results = []
        for item in root.rglob("*"):
            if not item.is_file():
                continue
            if extension and not item.name.endswith(extension):
                continue
            if keyword:
                try:
                    content = item.read_text(encoding="utf-8", errors="ignore")
                    if keyword.lower() not in content.lower():
                        continue
                except Exception:
                    continue
            results.append({"path": str(item.relative_to(WORKSPACE_PATH)), "size": item.stat().st_size, "name": item.name})
            if len(results) >= 100:
                break
        return ToolResult(status="success", data={"results": results, "count": len(results)}, facts=[Fact(type="FileSearch", data={"directory": directory, "extension": extension, "keyword": keyword, "count": len(results)})])

    async def fs_create_python_script(path: str, description: str = "") -> "ToolResult":
        """Создание Python-скрипта с каркасом."""
        from core.contracts import ToolResult, ErrorDetail, Recovery, Fact
        try:
            target = _safe_resolve(path)
        except ValueError:
            return ToolResult(status="error", error=ErrorDetail(code="PATH_ESCAPE", message=f"Path escapes workspace: {path}", recovery=Recovery(reason="Используй путь внутри workspace")))
        if not path.endswith(".py"):
            return ToolResult(status="error", error=ErrorDetail(code="INVALID_EXTENSION", message=f"Not a Python file: {path}", recovery=Recovery(reason="Используй расширение .py")))
        desc = description or target.stem
        skeleton = f'"""\n{desc}\n"""\n\nimport sys\nfrom pathlib import Path\n\n\ndef main():\n    """Main entry point."""\n    print(f"Running {{__file__}}")\n    # TODO: implement\n    pass\n\n\nif __name__ == "__main__":\n    main()\n'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skeleton, encoding="utf-8")
        return ToolResult(status="success", data={"created": path, "size": len(skeleton)}, facts=[Fact(type="FileCreated", data={"path": path, "type": "python_script"})])

    async def fs_create_project_structure(template: str = "", fragments: list[dict] | None = None) -> "ToolResult":
        """Материализация структуры по шаблону или список фрагментов."""
        from core.contracts import ToolResult, ErrorDetail, Recovery, Fact
        created, skipped = [], []
        if template:
            template_path = CONFIG_PATH / "templates" / "workspace" / f"{template}.yaml"
            if not template_path.exists():
                return ToolResult(status="error", error=ErrorDetail(code="TEMPLATE_NOT_FOUND", message=f"Template not found: {template}", recovery=Recovery(reason="Проверь имя шаблона в config/templates/workspace/")))
            import yaml
            tpl = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
            fragments = tpl.get("fragments", [])
        if not fragments:
            return ToolResult(status="error", error=ErrorDetail(code="NO_FRAGMENTS", message="No fragments to create", recovery=Recovery(reason="Укажи template или fragments")))
        for frag in fragments:
            name = frag.get("name", "")
            if not name:
                skipped.append({"reason": "no name", "fragment": frag})
                continue
            try:
                p = _safe_resolve(name)
                if frag.get("type") == "directory":
                    p.mkdir(parents=True, exist_ok=True)
                    created.append({"name": name, "type": "directory"})
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(frag.get("content", ""), encoding="utf-8")
                    created.append({"name": name, "type": "file"})
            except ValueError:
                skipped.append({"reason": "path escape", "name": name})
        return ToolResult(status="success", data={"created": created, "skipped": skipped}, facts=[Fact(type="StructureCreated", data={"template": template, "created": len(created), "skipped": len(skipped)})])

    async def json_read_snapshot(table: str) -> "ToolResult":
        """Чтение снапшота таблицы."""
        from core.contracts import ToolResult, ErrorDetail, Recovery, Fact
        try:
            snapshot = state_manager.read_snapshot(table)
        except ValueError:
            return ToolResult(status="error", error=ErrorDetail(code="PATH_ESCAPE", message=f"Path escapes workspace: {table}", recovery=Recovery(reason="Используй путь внутри workspace")))
        if snapshot is None:
            return ToolResult(status="error", error=ErrorDetail(code="TABLE_NOT_FOUND", message=f"Table not found: {table}", recovery=Recovery(reason="Создай структуру через fs_create_project_structure")))
        return ToolResult(status="success", data=snapshot, facts=[Fact(type="SnapshotRead", data={"table": table})])

    # ═══ РЕГИСТРАЦИЯ (все хендлеры определены выше) ═══

    # Аннотации MCP для инструментов (помогают клиенту определить уровень доступа)
    ANNOTATIONS_READONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
    ANNOTATIONS_MODIFY = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
    ANNOTATIONS_DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}  # шаблон-резерв: не назначен ни одному инструменту (см. history_server.md v2.6 — destructiveHint триггерит auth-гейт коннектора Claude.ai)

    fs_tools = [
        ("fs_get_directory_tree", "Получение дерева каталогов", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь относительно workspace"}}}, fs_get_directory_tree, ANNOTATIONS_READONLY),
        ("fs_read_file", "Чтение файла", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к файлу"}}, "required": ["path"]}, fs_read_file, ANNOTATIONS_READONLY),
        ("fs_create_file", "Создание файла", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к файлу"}, "content": {"type": "string", "description": "Содержимое файла"}}, "required": ["path"]}, fs_create_file, ANNOTATIONS_MODIFY),
        ("fs_write_file", "Полная перезапись файла", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к файлу"}, "content": {"type": "string", "description": "Новое содержимое файла"}}, "required": ["path", "content"]}, fs_write_file, ANNOTATIONS_MODIFY),
        ("fs_move", "Перемещение файла или каталога", {"type": "object", "properties": {"source": {"type": "string", "description": "Исходный путь"}, "destination": {"type": "string", "description": "Путь назначения"}}, "required": ["source", "destination"]}, fs_move, ANNOTATIONS_MODIFY),
        ("fs_rename", "Переименование файла или каталога", {"type": "object", "properties": {"path": {"type": "string", "description": "Текущий путь"}, "new_name": {"type": "string", "description": "Новое имя (без пути)"}}, "required": ["path", "new_name"]}, fs_rename, ANNOTATIONS_MODIFY),
        ("fs_delete", "Удаление файла или каталога", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к файлу/каталогу"}, "force": {"type": "boolean", "description": "Принудительное удаление каталога с содержимым", "default": False}}, "required": ["path"]}, fs_delete, ANNOTATIONS_MODIFY),
        ("fs_smart_search", "Поиск файлов по каталогу, расширению и ключевому слову", {"type": "object", "properties": {"directory": {"type": "string", "description": "Каталог для поиска", "default": "."}, "extension": {"type": "string", "description": "Фильтр по расширению (например .py)"}, "keyword": {"type": "string", "description": "Ключевое слово для поиска в содержимом"}}}, fs_smart_search, ANNOTATIONS_READONLY),
        ("fs_create_python_script", "Создание Python-скрипта с каркасом", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к .py файлу"}, "description": {"type": "string", "description": "Описание модуля"}}, "required": ["path"]}, fs_create_python_script, ANNOTATIONS_MODIFY),
        ("fs_create_project_structure", "Материализация структуры каталогов/файлов по шаблону или списку фрагментов", {"type": "object", "properties": {"template": {"type": "string", "description": "Имя шаблона из config/templates/workspace/"}, "fragments": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "type": {"type": "string", "enum": ["directory", "file"]}, "content": {"type": "string"}}}, "description": "Список фрагментов для создания"}}}, fs_create_project_structure, ANNOTATIONS_MODIFY),
    ]
    for name, desc, schema, handler, annot in fs_tools:
        engine.register(name=name, description=desc, input_schema=schema, handler=handler, group="filesystem", annotations=annot)

    engine.register(
        name="json_read_snapshot", description="Чтение снапшота таблицы (read.json)",
        input_schema={"type": "object", "properties": {"table": {"type": "string", "description": "Имя таблицы"}}, "required": ["table"]},
        handler=json_read_snapshot, group="tables", annotations=ANNOTATIONS_READONLY
    )


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

    print(f"=== MCP-сервер видеопайплайна ===")
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
                tunnel_status_str = f"процесс жив, но соединение НЕ установлено"
                print()
                print(f"⚠️  Туннель запущен, но соединение не установлено.")
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
            print("   Сервер работает локально. См. docs/dev/audit/v2/files/core_transport_tunnel.md")
            tunnel = None

    # Статус готовности (по спецификации MCP SDK).
    print()
    print(f"Статус: ГОТОВ | Туннель: {tunnel_status_str}")
    print("Для остановки: Ctrl+C")

    try:
        # Мониторинг туннеля: печатаем ТОЛЬКО изменения статуса, а не шум каждые N сек.
        # Восстановление соединения выполняет супервизор в CloudflaredTunnel сам —
        # здесь только наблюдаем его status() и сообщаем переходы в консоль.
        prev = tunnel.status() if tunnel else None

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
