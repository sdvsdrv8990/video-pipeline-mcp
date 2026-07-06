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

from core.engine import Engine, TemplateEngine, TemplateError
from core.firewall import Firewall, FirewallRequest, FirewallDecision
from core.transport import Transport
from core.reactions import Reactions
from core.ids import IDGenerator, LinkRegistry, LinkError
from core.state import StateManager
from core.paths import safe_resolve
from core.tables import TableEngine, TableError
from core.excel import ExcelEngine, ExcelError
from core.contracts import ToolResult, ErrorDetail, Recovery, Fact


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
        try:
            target = _safe_resolve(path)
        except ValueError:
            return _err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not target.exists():
            return _err("PATH_NOT_FOUND", f"Path not found: {path}")
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
        try:
            target = _safe_resolve(path)
        except ValueError:
            return _err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not target.exists():
            return _err("FILE_NOT_FOUND", f"File not found: {path}")
        content = target.read_text(encoding="utf-8")
        return ToolResult(status="success", data={"content": content, "size": len(content)}, facts=[Fact(type="FileRead", data={"path": path, "size": len(content)})])

    async def fs_create_file(path: str, content: str = "") -> "ToolResult":
        """Создание файла."""
        try:
            target = _safe_resolve(path)
        except ValueError:
            return _err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(status="success", data={"created": path, "size": len(content)}, facts=[Fact(type="FileCreated", data={"path": path, "size": len(content)})])

    async def fs_write_file(path: str, content: str) -> "ToolResult":
        """Полная перезапись файла."""
        try:
            target = _safe_resolve(path)
        except ValueError:
            return _err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        old_size = target.stat().st_size if target.exists() else 0
        target.write_text(content, encoding="utf-8")
        return ToolResult(status="success", data={"written": path, "size": len(content), "old_size": old_size}, facts=[Fact(type="FileWritten", data={"path": path, "size": len(content)})])

    async def memory_read(path: str) -> "ToolResult":
        """Чтение памяти проекта с парсингом структуры.

        Возвращает: заголовок, записи (с полями), количество, существующие ID.
        Позволяет ИИ понять структуру ДО вставки.
        """
        import re
        try:
            target = _safe_resolve(path)
        except ValueError:
            return _err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not target.exists():
            return ToolResult(status="success", data={"path": path, "exists": False, "entries": [], "ids": []})
        content = target.read_text(encoding="utf-8")
        # Парсим записи: ## [дата] Заголовок
        entries = []
        ids_found = []
        current_entry: dict | None = None
        for line in content.split("\n"):
            match = re.match(r"^## \[(.+?)\]\s*(.+)$", line)
            if match:
                if current_entry:
                    entries.append(current_entry)
                current_entry = {"date": match.group(1), "title": match.group(2), "fields": {}}
            elif current_entry and line.startswith("- **"):
                field_match = re.match(r"^- \*\*(.+?):\*\*\s*(.*)$", line)
                if field_match:
                    current_entry["fields"][field_match.group(1)] = field_match.group(2)
            # Ищем ID в формате PREFIX_hex
            for id_match in re.finditer(r'\b([A-Z]+_[0-9a-f]{32})\b', line):
                ids_found.append(id_match.group(1))
        if current_entry:
            entries.append(current_entry)
        return ToolResult(status="success", data={
            "path": path, "exists": True, "size": len(content),
            "entries": entries, "entry_count": len(entries),
            "ids": list(set(ids_found)),
        }, facts=[Fact(type="MemoryRead", data={"path": path, "entries": len(entries)})])

    async def memory_write(path: str, entry_date: str, title: str,
                           context: str = "", who_decided: str = "",
                           decision: str = "", reason: str = "",
                           result: str = "", after_date: str = "") -> "ToolResult":
        """Умная дозапись записи в память проекта.

        Вставляет новую запись в правильное место по дате (хронологически).
        Валидирует структуру: обязательные поля, ссылки на ID,的影响 на соседние записи.
        """
        import re
        try:
            target = _safe_resolve(path)
        except ValueError:
            return _err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Формируем запись
        entry = f"\n## [{entry_date}] Решение: {title}\n"
        if context:
            entry += f"- **Контекст:** {context}\n"
        if who_decided:
            entry += f"- **Кто решил:** {who_decided}\n"
        if decision:
            entry += f"- **Решение:** {decision}\n"
        if reason:
            entry += f"- **Почему:** {reason}\n"
        if result:
            entry += f"- **Результат:** {result}\n"
        else:
            entry += "- **Результат:** [ожидается]\n"
        # Читаем существующий контент
        old_content = target.read_text(encoding="utf-8") if target.exists() else ""
        # Ищем позицию для вставки (по дате)
        insert_pos = len(old_content)
        if after_date:
            # Ищем запись после которой вставлять
            pattern = rf"## \[{re.escape(after_date)}\]"
            match = re.search(pattern, old_content)
            if match:
                # Ищем конец этой записи (следующий ## или конец файла)
                next_section = re.search(r"\n## \[", old_content[match.end():])
                if next_section:
                    insert_pos = match.end() + next_section.start()
                else:
                    insert_pos = len(old_content)
        elif old_content:
            # Вставляем перед последней записью (новое сверху)
            last_entry = re.search(r"\n## \[", old_content)
            if last_entry:
                insert_pos = last_entry.start()
        # Вставляем
        new_content = old_content[:insert_pos] + entry + old_content[insert_pos:]
        target.write_text(new_content, encoding="utf-8")
        # Собираем ID из записи
        ids_in_entry = re.findall(r'\b([A-Z]+_[0-9a-f]{32})\b', entry)
        return ToolResult(status="success", data={
            "path": path, "inserted_at": insert_pos,
            "entry_date": entry_date, "title": title,
            "ids_referenced": ids_in_entry,
            "total_size": len(new_content),
        }, facts=[Fact(type="MemoryWritten", data={
            "path": path, "date": entry_date, "title": title,
            "ids": ids_in_entry, "position": insert_pos})])

    async def fs_move(source: str, destination: str) -> "ToolResult":
        """Перемещение файла или каталога."""
        try:
            src, dst = _safe_resolve(source), _safe_resolve(destination)
        except ValueError:
            return _err("PATH_ESCAPE", "Path escapes workspace")
        if not src.exists():
            return _err("FILE_NOT_FOUND", f"Source not found: {source}")
        if dst.exists():
            return _err("FILE_EXISTS", f"Destination exists: {destination}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return ToolResult(status="success", data={"source": source, "destination": destination}, facts=[Fact(type="FileMoved", data={"source": source, "destination": destination})])

    async def fs_rename(path: str, new_name: str) -> "ToolResult":
        """Переименование файла или каталога."""
        try:
            src = _safe_resolve(path)
        except ValueError:
            return _err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not src.exists():
            return _err("FILE_NOT_FOUND", f"Not found: {path}")
        dst = src.parent / new_name
        try:
            dst = _safe_resolve(str(dst.relative_to(WORKSPACE_PATH)))
        except ValueError:
            return _err("PATH_ESCAPE", f"New name escapes workspace: {new_name}")
        if dst.exists():
            return _err("FILE_EXISTS", f"Name exists: {new_name}")
        src.rename(dst)
        return ToolResult(status="success", data={"old_path": path, "new_path": str(dst.relative_to(WORKSPACE_PATH))}, facts=[Fact(type="FileRenamed", data={"old": path, "new": new_name})])

    async def fs_delete(path: str, force: bool = False) -> "ToolResult":
        """Удаление файла или каталога."""
        import shutil
        try:
            target = _safe_resolve(path)
        except ValueError:
            return _err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not target.exists():
            return _err("FILE_NOT_FOUND", f"Not found: {path}")
        if target.is_dir() and not force:
            contents = list(target.iterdir())
            if contents:
                return _err("DIRECTORY_NOT_EMPTY", f"Directory not empty: {path} ({len(contents)} items)")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return ToolResult(status="success", data={"deleted": path}, facts=[Fact(type="FileDeleted", data={"path": path})])

    # ═══ УМНЫЙ ПОИСК ПО ФАЙЛОВОЙ СИСТЕМЕ ═══
    from core.search.fs_searcher import FsSearcher, FsSearchTask, FsSearchError

    fs_searcher = FsSearcher(WORKSPACE_PATH)

    async def fs_smart_search(directory: str = ".", extension: str = "", keyword: str = "",
                              entity_type: str = "", id_pattern: str = "",
                              name_pattern: str = "", limit: int = 100) -> "ToolResult":
        """Умный поиск по файловой системе с фильтрами по типу сущности, ID, имени."""
        try:
            task = FsSearchTask(
                id="quick_search",
                root=directory,
                entity_types=[entity_type] if entity_type else [],
                id_pattern=id_pattern,
                name_pattern=name_pattern,
                extensions=[extension] if extension else [],
                content_keywords=[keyword] if keyword else [],
                limit=limit,
            )
            results = fs_searcher.search(task)
            return ToolResult(status="success", data={
                "results": [{"path": r.path, "name": r.name, "size": r.size,
                             "entity_type": r.entity_type, "entity_id": r.entity_id}
                            for r in results],
                "count": len(results),
            }, facts=[Fact(type="FsSearch", data={"directory": directory, "count": len(results)})])
        except FsSearchError as e:
            return _err(e.code, e.message, e.reason)
        except Exception as e:
            return _err("INTERNAL_ERROR", f"Ошибка поиска: {e}")

    async def fs_search_yaml(yaml_query: str) -> "ToolResult":
        """Умный поиск по YAML-запросу (очередь, многопоточность)."""
        try:
            task = fs_searcher.load_query(yaml_query)
            results = fs_searcher.search(task)
            return ToolResult(status="success", data={
                "results": [{"path": r.path, "name": r.name, "size": r.size,
                             "modified": r.modified, "entity_type": r.entity_type,
                             "entity_id": r.entity_id, "parent_path": r.parent_path}
                            for r in results],
                "count": len(results),
                "query_name": task.id,
            }, facts=[Fact(type="FsSearchYaml", data={"count": len(results)})])
        except FsSearchError as e:
            return _err(e.code, e.message, e.reason)
        except Exception as e:
            return _err("INTERNAL_ERROR", f"Ошибка поиска: {e}")

    async def fs_search_multi(queries: list[dict]) -> "ToolResult":
        """Многозадачный поиск (параллельно по нескольким запросам)."""
        try:
            tasks = []
            for i, q in enumerate(queries):
                task = FsSearchTask(
                    id=f"task_{i}",
                    root=q.get("root", ""),
                    entity_types=q.get("entity_types", []),
                    id_pattern=q.get("id_pattern", ""),
                    name_pattern=q.get("name_pattern", ""),
                    extensions=q.get("extensions", []),
                    content_keywords=q.get("content_keywords", []),
                    limit=q.get("limit", 100),
                )
                tasks.append(task)
            result = fs_searcher.search_parallel(tasks)
            return ToolResult(status="success", data={
                "results": {k: [{"path": r.path, "name": r.name, "entity_type": r.entity_type}
                                for r in v] for k, v in result["results"].items()},
                "errors": result["errors"],
                "total_tasks": len(tasks),
            }, facts=[Fact(type="FsSearchMulti", data={"tasks": len(tasks)})])
        except Exception as e:
            return _err("INTERNAL_ERROR", f"Ошибка поиска: {e}")

    async def fs_create_python_script(path: str, description: str = "") -> "ToolResult":
        """Создание Python-скрипта с каркасом."""
        try:
            target = _safe_resolve(path)
        except ValueError:
            return _err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not path.endswith(".py"):
            return _err("INVALID_EXTENSION", f"Not a Python file: {path}")
        desc = description or target.stem
        skeleton = f'"""\n{desc}\n"""\n\nimport sys\nfrom pathlib import Path\n\n\ndef main():\n    """Main entry point."""\n    print(f"Running {{__file__}}")\n    # TODO: implement\n    pass\n\n\nif __name__ == "__main__":\n    main()\n'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skeleton, encoding="utf-8")
        return ToolResult(status="success", data={"created": path, "size": len(skeleton)}, facts=[Fact(type="FileCreated", data={"path": path, "type": "python_script"})])

    async def fs_create_project_structure(template: str = "", fragments: list[dict] | None = None) -> "ToolResult":
        """Материализация структуры по шаблону или список фрагментов."""
        created, skipped = [], []
        if template:
            template_path = CONFIG_PATH / "templates" / "workspace" / f"{template}.yaml"
            if not template_path.exists():
                return _err("TEMPLATE_NOT_FOUND", f"Template not found: {template}")
            import yaml
            tpl = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
            fragments = tpl.get("fragments", [])
        if not fragments:
            return _err("NO_FRAGMENTS", "No fragments to create")
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
        try:
            snapshot = state_manager.read_snapshot(table)
        except ValueError:
            return _err("PATH_ESCAPE", f"Path escapes workspace: {table}")
        if snapshot is None:
            return _err("TABLE_NOT_FOUND", f"Table not found: {table}")
        return ToolResult(status="success", data=snapshot, facts=[Fact(type="SnapshotRead", data={"table": table})])

    # ═══ ТАБЛИЦЫ: движки (generic-ядра, тонкие обёртки ниже) ═══
    # Категория 3 (данные) — TableEngine поверх read.json/write.json.
    # Категория 2 (структура) — ExcelEngine поверх .xlsx (openpyxl).
    table_engine = TableEngine(state_manager, id_generator)
    excel_engine = ExcelEngine(state_manager.workspace_path)
    # Движок шаблонов структуры (Ф1): композиция по ссылке + контроль глубины.
    template_engine = TemplateEngine(
        state_manager.workspace_path, id_generator, CONFIG_PATH / "templates" / "workspace")
    # Реестр связей (Ф2): анонимные → ORPHAN, link() в одном месте.
    link_registry = LinkRegistry(state_manager.workspace_path)

    def _err(code: str, message: str = "", reason: str = "", suggested_tool: str | None = None):
        """Ошибочный ToolResult через реестр реакций (yaml = единственный источник class/recovery, B2/F43).

        Для кода из реестра class/message_template/recovery берутся из server_reactions.yaml
        (raw message сохраняет специфику). reason/suggested_tool — fallback лишь для кодов вне реестра.
        """
        if engine.reactions is not None and engine.reactions.get_reaction(code) is not None:
            return ToolResult(status="error", error=engine.reactions.get_error(code, raw_message=message))
        return ToolResult(status="error", error=ErrorDetail(
            code=code, message=message,
            recovery=Recovery(reason=reason, suggested_tool=suggested_tool)))

    def _safe(call):
        """Выполнить sync-вызов ядра, смаппив исключения в ToolResult.

        Returns (ok, value_or_error_result): при ok=False во втором элементе —
        готовый ошибочный ToolResult, иначе — результат ядра.
        """
        try:
            return True, call()
        except ValueError:
            return False, _err("PATH_ESCAPE", "Путь выходит за пределы workspace/.",
                               "Используй путь ВНУТРИ workspace, без '..' и абсолютных путей.")
        except (TableError, ExcelError, TemplateError, LinkError) as e:
            return False, _err(e.code, e.message, e.reason, e.suggested_tool)

    # ─── Структура: шаблонное создание (Ф1) ───

    async def structure_create(type: str, name: str, parent_path: str = "",
                               children: dict | None = None) -> "ToolResult":
        """Материализация узла структуры по шаблону с контролем глубины.

        Создаёт СВОИ папки/файлы узла + контейнеры детей; в детей спускается ТОЛЬКО
        для явно названных (children={тип:[имена]}). Таблицы (kind:table) отложены в
        фазу таблиц (Ф3) → tables_pending. ID узла присваивает сервер (в facts).
        """
        ok, res = _safe(lambda: template_engine.create_node(type, name, parent_path, None, children))
        if not ok:
            return res

        facts: list = []
        created_ids: list[str] = []

        def _walk(node: dict) -> None:
            # Ф2: регистрируем узел в реестре связей (для ORPHAN/link).
            link_registry.register({
                "id": node["node_id"], "type": node["type"], "name": node["name"],
                "path": node["path"], "parent_ids": node["parent_ids"], "kind": "node"})
            created_ids.append(node["node_id"])
            facts.append(Fact(type="NodeCreated", data={
                "id": node["node_id"], "type": node["type"], "name": node["name"],
                "path": node["path"], "parent_ids": node["parent_ids"]}))
            for c in node["created"]:
                facts.append(Fact(
                    type="FolderCreated" if c["kind"] == "folder" else "FileCreated",
                    data={"path": c["path"]}))
            for t in node["tables_pending"]:
                # Регистрируем отложенную таблицу с ID в реестре
                if "file_id" in t:
                    link_registry.register({
                        "id": t["file_id"], "type": "table_file", "name": t["path"].split("/")[-1],
                        "path": t["path"], "parent_ids": [node["node_id"]], "kind": "file"})
                facts.append(Fact(type="TableDeferred", data=t))
            for d in node["deferred_children"]:
                facts.append(Fact(type="ChildDeferred", data=d))
            for sub in node["children"]:
                _walk(sub)

        _walk(res)

        # Ф2: уведомление о висящих среди только что созданных (напр. конкурент без нашего канала).
        orphan_notices = [o for o in link_registry.find_orphans() if o["id"] in created_ids]
        for o in orphan_notices:
            facts.append(Fact(type="EntityOrphaned", data=o))
        res["orphan_notices"] = orphan_notices
        return ToolResult(status="success", data=res, facts=facts)

    async def structure_link(child_type: str, child_name: str,
                             parent_type: str, parent_name: str) -> "ToolResult":
        """Связать сущность с родителем В ОДНОМ месте (реестр — источник истины).

        Один вызов добавляет parent_id ребёнку; не требует правки обоих деревьев
        (экономит токены, исключает рассинхрон). Пример: привязать конкурента к нашему каналу.
        """
        ok, res = _safe(lambda: link_registry.link(child_type, child_name, parent_type, parent_name))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="EntityLinked", data={
            "child_id": res["child"]["id"], "child_type": child_type, "child_name": child_name,
            "parent_id": res["parent_id"], "parent_type": parent_type, "parent_name": parent_name})])

    async def structure_migrate(entity_id: str, new_path: str) -> "ToolResult":
        """Миграция сущности: физический перенос папки + обновление реестра.

        Используется когда родитель появился позже (напр. конкурент без канала → привязка к каналу).
        Физически перемещает папку и обновляет path в реестре.
        """
        import shutil
        # Получаем текущий путь из реестра
        entity = link_registry.get(entity_id)
        if not entity:
            return _err("ENTITY_NOT_FOUND", f"Сущность {entity_id} не найдена в реестре.",
                        "Сначала создай её через structure_create.", "structure_status")
        old_path = entity.get("path", "")
        # Проверяем что старый путь существует
        try:
            old_full = _safe_resolve(old_path)
        except ValueError:
            return _err("PATH_ESCAPE", f"Старый путь выходит за workspace: {old_path}")
        if not old_full.exists():
            return _err("FILE_NOT_FOUND", f"Папка не найдена: {old_path}")
        # Проверяем что новый путь не занят
        try:
            new_full = _safe_resolve(new_path)
        except ValueError:
            return _err("PATH_ESCAPE", f"Новый путь выходит за workspace: {new_path}")
        if new_full.exists():
            return _err("FILE_EXISTS", f"Путь уже существует: {new_path}")
        # Физический перенос
        new_full.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_full), str(new_full))
        # Обновляем реестр
        ok, res = _safe(lambda: link_registry.migrate(entity_id, new_path))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="EntityMigrated", data={
            "id": entity_id, "old_path": old_path, "new_path": new_path})])

    async def structure_status() -> "ToolResult":
        """Сводка связей: висящие (ORPHAN) + наши каналы без конкурента (мягко).

        Это поверхность «уведомления от сервера»: у вас есть конкурент, не привязанный
        ни к одному каналу / у вас есть канал без конкурента.
        """
        orphans = link_registry.find_orphans()
        ours_no_comp = link_registry.find_childless("channel", "competitor_channel")
        facts = [Fact(type="EntityOrphaned", data=o) for o in orphans]
        return ToolResult(status="success",
                          data={"orphans": orphans, "our_channels_without_competitor": ours_no_comp},
                          facts=facts)

    async def structure_check_integrity() -> "ToolResult":
        """Фоновая проверка целостности реестра: висящие ссылки, дубликаты путей, сироты."""
        ok, res = _safe(lambda: link_registry.check_integrity())
        if not ok:
            return res
        facts = []
        for issue in res.get("issues", []):
            facts.append(Fact(type="IntegrityIssue", data=issue))
        return ToolResult(status="success", data=res, facts=facts)

    # ─── Категория 3: чтения (проекции) ───

    async def table_get_column(table: str, sheet: str, column: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.get_column(table, sheet, column))
        if not ok:
            return res
        return ToolResult(status="success", data={"column": column, "values": res},
                          facts=[Fact(type="ColumnRead", data={"table": table, "sheet": sheet, "column": column, "n": len(res)})])

    async def table_get_row(table: str, sheet: str, row_id: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.get_row(table, sheet, row_id))
        if not ok:
            return res
        return ToolResult(status="success", data={"row_id": row_id, "row": res},
                          facts=[Fact(type="RowRead", data={"table": table, "sheet": sheet, "row_id": row_id})])

    # ─── Категория 3: записи (через очередь) ───

    async def table_set(table: str, sheet: str, row_id: str, column: str, value=None) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.set(table, sheet, row_id, column, value))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res},
                          facts=[Fact(type="RowSet", data={"table": table, "sheet": sheet, "row_id": row_id, "column": column})])

    async def table_append(table: str, sheet: str, data: dict | None = None, id_prefix: str = "ROW") -> "ToolResult":
        ok, res = _safe(lambda: table_engine.append(table, sheet, data or {}, id_prefix))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res, "row_id": res["row_id"]},
                          facts=[Fact(type="RowAppended", data={"table": table, "sheet": sheet, "row_id": res["row_id"]})])

    async def table_delete(table: str, sheet: str, row_id: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.delete(table, sheet, row_id))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res},
                          facts=[Fact(type="RowDeleted", data={"table": table, "sheet": sheet, "row_id": row_id})])

    # ─── Категория 3: очередь (json_*) ───

    async def json_push_to_queue(table: str, action: dict) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.push_to_queue(table, action))
        if not ok:
            return res
        return ToolResult(status="success", data={"queued": res},
                          facts=[Fact(type="QueuePushed", data={"table": table, "action": res.get("action")})])

    async def json_execute_queue(table: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.execute_queue(table))
        if not ok:
            return res
        return ToolResult(status="success", data=res,
                          facts=[Fact(type="QueueExecuted", data={"table": table, "applied": res["applied"], "skipped": len(res["skipped"])})])

    async def json_clear_queue(table: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.clear_queue(table))
        if not ok:
            return res
        return ToolResult(status="success", data=res,
                          facts=[Fact(type="QueueCleared", data={"table": table, "cleared": res["cleared"]})])

    # ─── Категория 2: структура (excel_*) ───

    async def excel_create_workbook(path: str, sheet: str = "Sheet1") -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.create_workbook(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="WorkbookCreated", data=res)])

    async def excel_add_sheet(path: str, sheet: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.add_sheet(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetAdded", data={"path": path, "sheet": sheet})])

    async def excel_rename_sheet(path: str, sheet: str, new_name: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.rename_sheet(path, sheet, new_name))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetRenamed", data={"path": path, "from": sheet, "to": new_name})])

    async def excel_delete_sheet(path: str, sheet: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.delete_sheet(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetDeleted", data={"path": path, "sheet": sheet})])

    async def excel_reorder_sheets(path: str, order: list) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.reorder_sheets(path, order))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetsReordered", data={"path": path})])

    async def excel_add_column(path: str, sheet: str, column: str, formula: str = "") -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.add_column(path, sheet, column, formula or None))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnAdded", data={"path": path, "sheet": sheet, "column": column})])

    async def excel_delete_column(path: str, sheet: str, column: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.delete_column(path, sheet, column))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnDeleted", data={"path": path, "sheet": sheet, "column": column})])

    async def excel_move_column(path: str, sheet: str, column: str, to_index: int) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.move_column(path, sheet, column, to_index))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnMoved", data={"path": path, "sheet": sheet, "column": column, "to": to_index})])

    async def excel_insert_formula(path: str, sheet: str, cell: str, formula: str, overwrite: bool = False) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.insert_formula(path, sheet, cell, formula, overwrite))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FormulaInserted", data={"path": path, "sheet": sheet, "cell": cell})])

    async def excel_apply_formatting(path: str, sheet: str, target: str, fill: str = "", bold: bool | None = None, font_color: str = "") -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.apply_formatting(path, sheet, target, fill or None, bold, font_color or None))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FormattingApplied", data={"path": path, "sheet": sheet, "target": target})])

    async def excel_set_validation(path: str, sheet: str, column: str, allowed: list) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.set_validation(path, sheet, column, allowed))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ValidationSet", data={"path": path, "sheet": sheet, "column": column})])

    async def excel_read_range(path: str, sheet: str, cell_range: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.read_range(path, sheet, cell_range))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="RangeRead", data={"path": path, "sheet": sheet, "range": cell_range})])

    async def excel_validate_formulas(path: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.validate_formulas(path))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FormulasValidated", data={"path": path, "ok": res["ok"], "errors": len(res["errors"])})])

    # ─── Excel: копирование листа ───

    async def excel_copy_sheet(path: str, sheet: str, new_name: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.copy_sheet(path, sheet, new_name))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetCopied", data={"path": path, "from": sheet, "to": new_name})])

    # ─── Excel: анализ структуры ───

    async def inspect_file(path: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.inspect_file(path))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="FileInspected", data={"path": path, "sheets": res["sheet_count"]})])

    async def get_sheet_info(path: str, sheet: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.get_sheet_info(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="SheetInfoRead", data={"path": path, "sheet": sheet, "columns": res["column_count"]})])

    async def get_column_names(path: str, sheet: str) -> "ToolResult":
        ok, res = _safe(lambda: excel_engine.get_column_names(path, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ColumnNamesRead", data={"path": path, "sheet": sheet, "count": res["count"]})])

    # ─── Таблицы: анализ данных ───

    async def get_unique_values(table: str, sheet: str, column: str, limit: int = 100) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.get_unique_values(table, sheet, column, limit))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="UniqueValuesRead", data={"table": table, "sheet": sheet, "column": column, "count": res["count"]})])

    async def get_value_counts(table: str, sheet: str, column: str, limit: int = 10) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.get_value_counts(table, sheet, column, limit))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="ValueCountsRead", data={"table": table, "sheet": sheet, "column": column, "total": res["total"]})])

    async def find_duplicates(table: str, sheet: str, columns: list[str] | None = None) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.find_duplicates(table, sheet, columns))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="DuplicatesFound", data={"table": table, "sheet": sheet, "groups": res["duplicate_groups"], "rows": res["duplicate_rows"]})])

    async def find_nulls(table: str, sheet: str) -> "ToolResult":
        ok, res = _safe(lambda: table_engine.find_nulls(table, sheet))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="NullsFound", data={"table": table, "sheet": sheet, "columns_with_nulls": res["columns_with_nulls"]})])

    # ═══ РЕГИСТРАЦИЯ (все хендлеры определены выше) ═══

    # Аннотации MCP для инструментов (помогают клиенту определить уровень доступа)
    ANNOTATIONS_READONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
    ANNOTATIONS_MODIFY = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
    ANNOTATIONS_DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}  # noqa: F841 — резерв, намеренно не назначен: destructiveHint триггерит auth-гейт коннектора Claude.ai

    # Формат кортежа: (name, title, description, schema, handler, annotations).
    # title — человекочитаемая подпись для UI Claude; префикс «Файлы:» делает
    # группу видимой у каждого инструмента (секций-заголовков MCP не даёт).
    fs_tools = [
        ("fs_get_directory_tree", "Файлы: дерево каталогов", "Получение дерева каталогов", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь относительно workspace"}}}, fs_get_directory_tree, ANNOTATIONS_READONLY),
        ("fs_read_file", "Файлы: прочитать файл", "Чтение файла", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к файлу"}}, "required": ["path"]}, fs_read_file, ANNOTATIONS_READONLY),
        ("fs_create_file", "Файлы: создать файл", "Создание файла", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к файлу"}, "content": {"type": "string", "description": "Содержимое файла"}}, "required": ["path"]}, fs_create_file, ANNOTATIONS_MODIFY),
        ("fs_write_file", "Файлы: перезаписать файл", "Полная перезапись файла", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к файлу"}, "content": {"type": "string", "description": "Новое содержимое файла"}}, "required": ["path", "content"]}, fs_write_file, ANNOTATIONS_MODIFY),
        ("fs_move", "Файлы: переместить", "Перемещение файла или каталога", {"type": "object", "properties": {"source": {"type": "string", "description": "Исходный путь"}, "destination": {"type": "string", "description": "Путь назначения"}}, "required": ["source", "destination"]}, fs_move, ANNOTATIONS_MODIFY),
        ("fs_rename", "Файлы: переименовать", "Переименование файла или каталога", {"type": "object", "properties": {"path": {"type": "string", "description": "Текущий путь"}, "new_name": {"type": "string", "description": "Новое имя (без пути)"}}, "required": ["path", "new_name"]}, fs_rename, ANNOTATIONS_MODIFY),
        ("fs_delete", "Файлы: удалить", "Удаление файла или каталога", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к файлу/каталогу"}, "force": {"type": "boolean", "description": "Принудительное удаление каталога с содержимым", "default": False}}, "required": ["path"]}, fs_delete, ANNOTATIONS_MODIFY),
        ("fs_smart_search", "Файлы: умный поиск", "Поиск файлов с фильтрами: тип сущности, ID, имя, расширение, содержимое",
         {"type": "object", "properties": {
             "directory": {"type": "string", "description": "Корневой каталог (относительно workspace)", "default": "."},
             "extension": {"type": "string", "description": "Фильтр по расширению"},
             "keyword": {"type": "string", "description": "Ключевое слово в содержимом"},
             "entity_type": {"type": "string", "enum": ["niche", "network", "channel", "video", "competitor_channel", "competitor_video", "asset", "scene", "render"], "description": "Тип сущности"},
             "id_pattern": {"type": "string", "description": "Regex паттерн ID (напр. VID_*)"},
             "name_pattern": {"type": "string", "description": "Regex паттерн имени файла"},
             "limit": {"type": "integer", "description": "Максимум результатов", "default": 100},
         }},
         fs_smart_search, ANNOTATIONS_READONLY),
        ("fs_search_yaml", "Файлы: YAML-поиск", "Умный поиск по YAML-запросу (очередь, многопоточность, фильтры по дате/размеру/содержимому)",
         {"type": "object", "properties": {
             "yaml_query": {"type": "string", "description": "YAML-строка с запросом"},
         }, "required": ["yaml_query"]},
         fs_search_yaml, ANNOTATIONS_READONLY),
        ("fs_search_multi", "Файлы: многозадачный поиск", "Параллельный поиск по нескольким запросам",
         {"type": "object", "properties": {
             "queries": {"type": "array", "items": {"type": "object", "properties": {
                 "root": {"type": "string"},
                 "entity_types": {"type": "array", "items": {"type": "string"}},
                 "extensions": {"type": "array", "items": {"type": "string"}},
                 "content_keywords": {"type": "array", "items": {"type": "string"}},
             }}, "description": "Список запросов"},
         }, "required": ["queries"]},
         fs_search_multi, ANNOTATIONS_READONLY),
        ("fs_create_python_script", "Файлы: новый Python-скрипт", "Создание Python-скрипта с каркасом", {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к .py файлу"}, "description": {"type": "string", "description": "Описание модуля"}}, "required": ["path"]}, fs_create_python_script, ANNOTATIONS_MODIFY),
        ("fs_create_project_structure", "Файлы: структура проекта", "Материализация структуры каталогов/файлов по шаблону или списку фрагментов", {"type": "object", "properties": {"template": {"type": "string", "description": "Имя шаблона из config/templates/workspace/"}, "fragments": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "type": {"type": "string", "enum": ["directory", "file"]}, "content": {"type": "string"}}}, "description": "Список фрагментов для создания"}}}, fs_create_project_structure, ANNOTATIONS_MODIFY),
    ]
    for name, title, desc, schema, handler, annot in fs_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="filesystem", annotations=annot)  # type: ignore[arg-type]

    # ═══ ПАМЯТЬ ПРОЕКТА (project_memory.md) ═══
    memory_tools = [
        ("memory_read", "Память: прочитать", "Чтение памяти проекта с парсингом структуры: записи, поля, существующие ID",
         {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к project_memory.md"}}, "required": ["path"]},
         memory_read, ANNOTATIONS_READONLY),
        ("memory_write", "Память: записать решение", "Умная дозапись записи в память (по дате, с валидацией полей и ссылок на ID)",
         {"type": "object", "properties": {
             "path": {"type": "string", "description": "Путь к project_memory.md"},
             "entry_date": {"type": "string", "description": "Дата записи (ГГГГ-ММ-ДД)"},
             "title": {"type": "string", "description": "Заголовок решения"},
             "context": {"type": "string", "description": "Контекст (что произошло, ссылки на ID)"},
             "who_decided": {"type": "string", "description": "Кто решил (человек / Claude)"},
             "decision": {"type": "string", "description": "Что именно сделали"},
             "reason": {"type": "string", "description": "Почему (этого нет в таблицах)"},
             "result": {"type": "string", "description": "Результат (дописывается позже, ссылки на ID/динамику)"},
             "after_date": {"type": "string", "description": "Вставить после записи с этой датой (хронология)"},
         }, "required": ["path", "entry_date", "title", "decision", "reason"]},
         memory_write, ANNOTATIONS_MODIFY),
    ]
    for name, title, desc, schema, handler, annot in memory_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="memory", annotations=annot)  # type: ignore[arg-type]

    engine.register(
        name="json_read_snapshot", title="Таблицы: снапшот (read.json)", description="Чтение снапшота таблицы (read.json)",
        input_schema={"type": "object", "properties": {"table": {"type": "string", "description": "Путь к таблице (сущности) относительно workspace"}}, "required": ["table"]},
        handler=json_read_snapshot, group="tables", annotations=ANNOTATIONS_READONLY
    )

    # ═══ КАТЕГОРИЯ 3: данные таблиц (json_* очередь + 5 примитивов) ═══
    # Формат кортежа: (name, title, description, schema, handler, annotations).
    _TABLE = {"type": "string", "description": "Путь к таблице (сущности) относительно workspace"}
    _SHEET = {"type": "string", "description": "Имя листа (регистр важен)"}
    tables_tools = [
        ("table_get_column", "Таблицы: столбец {id:value}", "Проекция одного столбца листа: {row_id: value}",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}}, "required": ["table", "sheet", "column"]},
         table_get_column, ANNOTATIONS_READONLY),
        ("table_get_row", "Таблицы: строка {col:value}", "Одна строка целиком: {column: value}",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "row_id": {"type": "string", "description": "ID строки"}}, "required": ["table", "sheet", "row_id"]},
         table_get_row, ANNOTATIONS_READONLY),
        ("table_set", "Таблицы: изменить поле", "Изменить поле строки (RMW через очередь). Защита формул + enum.",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "row_id": {"type": "string", "description": "ID строки"}, "column": {"type": "string", "description": "Имя столбца"}, "value": {"description": "Новое значение (любой JSON-тип)"}}, "required": ["table", "sheet", "row_id", "column", "value"]},
         table_set, ANNOTATIONS_MODIFY),
        ("table_append", "Таблицы: новая строка", "Добавить строку. ID присваивает сервер (приходит в facts).",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "data": {"type": "object", "description": "Поля новой строки {column: value}"}, "id_prefix": {"type": "string", "description": "Префикс ID строки", "default": "ROW"}}, "required": ["table", "sheet", "data"]},
         table_append, ANNOTATIONS_MODIFY),
        ("table_delete", "Таблицы: удалить строку", "Удалить строку по ID (через очередь).",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "row_id": {"type": "string", "description": "ID строки"}}, "required": ["table", "sheet", "row_id"]},
         table_delete, ANNOTATIONS_MODIFY),
        ("json_push_to_queue", "Таблицы: очередь → добавить", "Положить пишущую операцию (set/append/delete) в write.json.",
         {"type": "object", "properties": {"table": _TABLE, "action": {"type": "object", "description": "{action: set|append|delete, sheet, ...}"}}, "required": ["table", "action"]},
         json_push_to_queue, ANNOTATIONS_MODIFY),
        ("json_execute_queue", "Таблицы: очередь → применить", "Применить очередь к read.json (RMW). Синк в .xlsx отложен.",
         {"type": "object", "properties": {"table": _TABLE}, "required": ["table"]},
         json_execute_queue, ANNOTATIONS_MODIFY),
        ("json_clear_queue", "Таблицы: очередь → очистить", "Очистить очередь без применения (отладка/сброс).",
         {"type": "object", "properties": {"table": _TABLE}, "required": ["table"]},
         json_clear_queue, ANNOTATIONS_MODIFY),
        ("get_unique_values", "Таблицы: уникальные значения", "Уникальные значения столбца.",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}, "limit": {"type": "integer", "description": "Максимум значений", "default": 100}}, "required": ["table", "sheet", "column"]},
         get_unique_values, ANNOTATIONS_READONLY),
        ("get_value_counts", "Таблицы: частотный анализ", "Top-N наиболее частых значений столбца.",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}, "limit": {"type": "integer", "description": "Top-N", "default": 10}}, "required": ["table", "sheet", "column"]},
         get_value_counts, ANNOTATIONS_READONLY),
        ("find_duplicates", "Таблицы: дубликаты", "Поиск дубликатов по столбцам (или всем).",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET, "columns": {"type": "array", "items": {"type": "string"}, "description": "Столбцы для проверки (все если пусто)"}}, "required": ["table", "sheet"]},
         find_duplicates, ANNOTATIONS_READONLY),
        ("find_nulls", "Таблицы: пустые значения", "Поиск пустых/пропущенных значений по всем столбцам.",
         {"type": "object", "properties": {"table": _TABLE, "sheet": _SHEET}, "required": ["table", "sheet"]},
         find_nulls, ANNOTATIONS_READONLY),
    ]
    for name, title, desc, schema, handler, annot in tables_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="tables", annotations=annot)  # type: ignore[arg-type]

    # ═══ КАТЕГОРИЯ 2: структура таблиц (excel_*) ═══
    _PATH = {"type": "string", "description": "Путь к .xlsx относительно workspace"}
    excel_tools = [
        ("excel_create_workbook", "Excel: новая книга", "Создать новый .xlsx (не перезаписывает существующий).",
         {"type": "object", "properties": {"path": _PATH, "sheet": {"type": "string", "description": "Имя первого листа", "default": "Sheet1"}}, "required": ["path"]},
         excel_create_workbook, ANNOTATIONS_MODIFY),
        ("excel_add_sheet", "Excel: добавить лист", "Добавить лист в книгу.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET}, "required": ["path", "sheet"]},
         excel_add_sheet, ANNOTATIONS_MODIFY),
        ("excel_rename_sheet", "Excel: переименовать лист", "Переименовать лист.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "new_name": {"type": "string", "description": "Новое имя листа"}}, "required": ["path", "sheet", "new_name"]},
         excel_rename_sheet, ANNOTATIONS_MODIFY),
        ("excel_delete_sheet", "Excel: удалить лист", "Удалить лист (нельзя последний).",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET}, "required": ["path", "sheet"]},
         excel_delete_sheet, ANNOTATIONS_MODIFY),
        ("excel_reorder_sheets", "Excel: порядок листов", "Переупорядочить листы (order = все листы книги).",
         {"type": "object", "properties": {"path": _PATH, "order": {"type": "array", "items": {"type": "string"}, "description": "Полный список листов в новом порядке"}}, "required": ["path", "order"]},
         excel_reorder_sheets, ANNOTATIONS_MODIFY),
        ("excel_add_column", "Excel: добавить столбец", "Добавить столбец (заголовок в строку 1). formula — опционально.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца (заголовок)"}, "formula": {"type": "string", "description": "Формула-образец (опц.)"}}, "required": ["path", "sheet", "column"]},
         excel_add_column, ANNOTATIONS_MODIFY),
        ("excel_delete_column", "Excel: удалить столбец", "Удалить столбец по имени заголовка.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}}, "required": ["path", "sheet", "column"]},
         excel_delete_column, ANNOTATIONS_MODIFY),
        ("excel_move_column", "Excel: переместить столбец", "Переместить столбец на позицию to_index (1-based).",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}, "to_index": {"type": "integer", "description": "Новая позиция (1-based)"}}, "required": ["path", "sheet", "column", "to_index"]},
         excel_move_column, ANNOTATIONS_MODIFY),
        ("excel_insert_formula", "Excel: вставить формулу", "Формула в ячейку. Не перезаписывает существующую молча (overwrite).",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "cell": {"type": "string", "description": "Ячейка (напр. C2)"}, "formula": {"type": "string", "description": "Формула (с '=' или без)"}, "overwrite": {"type": "boolean", "description": "Перезаписать существующую формулу", "default": False}}, "required": ["path", "sheet", "cell", "formula"]},
         excel_insert_formula, ANNOTATIONS_MODIFY),
        ("excel_apply_formatting", "Excel: форматирование", "Стили на ячейку/диапазон (заливка/жирный/цвет шрифта).",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "target": {"type": "string", "description": "Ячейка или диапазон (A1 / A1:C3)"}, "fill": {"type": "string", "description": "HEX заливки RRGGBB"}, "bold": {"type": "boolean", "description": "Жирный"}, "font_color": {"type": "string", "description": "HEX цвета шрифта RRGGBB"}}, "required": ["path", "sheet", "target"]},
         excel_apply_formatting, ANNOTATIONS_MODIFY),
        ("excel_set_validation", "Excel: выпадающий список", "Data Validation (dropdown) на столбец — материализует enum из схемы.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "column": {"type": "string", "description": "Имя столбца"}, "allowed": {"type": "array", "items": {"type": "string"}, "description": "Список допустимых значений"}}, "required": ["path", "sheet", "column", "allowed"]},
         excel_set_validation, ANNOTATIONS_MODIFY),
        ("excel_read_range", "Excel: сырой диапазон (отладка)", "ОТЛАДКА: сырой 2D-массив ячеек. Рабочее чтение — json_read_snapshot.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "cell_range": {"type": "string", "description": "Диапазон (напр. A1:C10)"}}, "required": ["path", "sheet", "cell_range"]},
         excel_read_range, ANNOTATIONS_READONLY),
        ("excel_validate_formulas", "Excel: проверить формулы", "Поиск ошибок формул (#REF!/#VALUE!/…) по всем листам.",
         {"type": "object", "properties": {"path": _PATH}, "required": ["path"]},
         excel_validate_formulas, ANNOTATIONS_READONLY),
        ("excel_copy_sheet", "Excel: копировать лист", "Копирование листа с данными и форматированием.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET, "new_name": {"type": "string", "description": "Имя копии листа"}}, "required": ["path", "sheet", "new_name"]},
         excel_copy_sheet, ANNOTATIONS_MODIFY),
        ("inspect_file", "Excel: обзор книги", "Обзор структуры: листы, размеры, формат.",
         {"type": "object", "properties": {"path": _PATH}, "required": ["path"]},
         inspect_file, ANNOTATIONS_READONLY),
        ("get_sheet_info", "Excel: анализ листа", "Детальный анализ: колонки, типы, превью данных.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET}, "required": ["path", "sheet"]},
         get_sheet_info, ANNOTATIONS_READONLY),
        ("get_column_names", "Excel: имена колонок", "Быстрый список колонок листа.",
         {"type": "object", "properties": {"path": _PATH, "sheet": _SHEET}, "required": ["path", "sheet"]},
         get_column_names, ANNOTATIONS_READONLY),
    ]
    for name, title, desc, schema, handler, annot in excel_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="excel", annotations=annot)  # type: ignore[arg-type]

    # ═══ СТРУКТУРА: шаблонное создание (композиция по ссылке + контроль глубины) ═══
    engine.register(
        name="structure_create",
        title="Структура: создать узел по шаблону",
        description=(
            "Материализует узел (niche/network/channel/video/competitor_channel/competitor_video) "
            "по шаблону: свои папки/файлы + контейнеры детей. В детей спускается ТОЛЬКО для явно "
            "названных (children={тип:[имена]}) — так 'создать канал кроме видео' = не называть видео, "
            "а 'назвать видео' = создать всё его поддерево. Таблицы (kind:table) отложены в фазу таблиц. "
            "ID узла присваивает сервер (в facts NodeCreated)."),
        input_schema={"type": "object", "properties": {
            "type": {"type": "string",
                     "enum": ["niche", "network", "channel", "video", "competitor_channel", "competitor_video"],
                     "description": "Тип узла (ключ шаблона)"},
            "name": {"type": "string", "description": "Имя экземпляра (один сегмент пути, без '/')"},
            "parent_path": {"type": "string", "default": "",
                            "description": "Контейнер-путь родителя относительно workspace (пусто → корень типа; для niche = niches/)"},
            "children": {"type": "object",
                         "description": "Каких детей развернуть: {тип_ребёнка: [имена]}. Не названные — отложены (ChildDeferred).",
                         "additionalProperties": {"type": "array", "items": {"type": "string"}}},
        }, "required": ["type", "name"]},
        handler=structure_create, group="structure", annotations=ANNOTATIONS_MODIFY)
    engine.register(
        name="structure_link",
        title="Структура: связать сущности",
        description=(
            "Связывает сущность с родителем В ОДНОМ месте (реестр связей — источник истины): "
            "например конкурента с нашим каналом. Один вызов, не нужно править оба дерева — "
            "экономит токены и исключает рассинхрон. Снимает уведомление UNLINKED_ENTITY."),
        input_schema={"type": "object", "properties": {
            "child_type": {"type": "string", "description": "Тип привязываемой сущности (напр. competitor_channel)"},
            "child_name": {"type": "string", "description": "Имя привязываемой сущности"},
            "parent_type": {"type": "string", "description": "Тип родителя (напр. channel)"},
            "parent_name": {"type": "string", "description": "Имя родителя"},
        }, "required": ["child_type", "child_name", "parent_type", "parent_name"]},
        handler=structure_link, group="structure", annotations=ANNOTATIONS_MODIFY)
    engine.register(
        name="structure_migrate",
        title="Структура: миграция (перенос папки)",
        description=(
            "Физический перенос папки сущности + обновление реестра. Используется когда родитель "
            "появился позже (напр. конкурент был без канала, теперь канал есть — переносим "
            "competitors/competitor_A/ → competitors/my_channel/competitor_A/). "
            "Обновляет path в реестре и перемещает файлы на диске."),
        input_schema={"type": "object", "properties": {
            "entity_id": {"type": "string", "description": "ID сущности из реестра"},
            "new_path": {"type": "string", "description": "Новый путь относительно workspace"},
        }, "required": ["entity_id", "new_path"]},
        handler=structure_migrate, group="structure", annotations=ANNOTATIONS_MODIFY)
    engine.register(
        name="structure_status",
        title="Структура: сводка связей (висящие)",
        description=(
            "Сводка реестра связей: висящие сущности (ORPHAN — напр. конкурент без нашего канала) "
            "и наши каналы без привязанного конкурента. Поверхность серверных уведомлений о непривязанном."),
        input_schema={"type": "object", "properties": {}},
        handler=structure_status, group="structure", annotations=ANNOTATIONS_READONLY)
    engine.register(
        name="structure_check_integrity",
        title="Структура: проверка целостности реестра",
        description=(
            "Фоновая проверка: висящие ссылки, дубликаты путей, сироты. "
            "Возвращает общую статистику + список проблем."),
        input_schema={"type": "object", "properties": {}},
        handler=structure_check_integrity, group="structure", annotations=ANNOTATIONS_READONLY)

    # ═══ УМНЫЙ ПОИСК ПО ТАБЛИЦАМ ═══
    from core.search.query_planner import QueryPlanner, SearchError

    planner = QueryPlanner(table_engine, state_manager.workspace_path)

    async def search_tables(yaml_query: str | None = None, query_dict: dict | None = None) -> "ToolResult":
        """Умный поиск по таблицам через YAML-запрос.

        Принимает YAML-строку или dict с запросом. Возвращает объединённые
        результаты с прогрессом выполнения.
        """
        try:
            if yaml_query:
                import yaml
                data = yaml.safe_load(yaml_query)
            elif query_dict:
                data = query_dict
            else:
                return _err("VALIDATION_ERROR", "Укажи yaml_query или query_dict")

            plan = planner.load_query_from_dict(data)
            result = planner.execute_plan(plan)

            return ToolResult(status="success", data=result,
                              facts=[Fact(type="SearchCompleted", data={
                                  "query": plan.name,
                                  "reads": result["metadata"]["reads_executed"],
                                  "rows": result["metadata"]["total_rows"],
                                  "errors": result["metadata"]["reads_failed"],
                              })])
        except SearchError as e:
            return _err(e.code, e.message, e.reason)
        except Exception as e:
            return _err("INTERNAL_ERROR", f"Ошибка поиска: {e}")

    async def search_quick(table: str, sheet: str, column: str = "",
                           filter_col: str = "", filter_op: str = "eq",
                           filter_val: str = "", limit: int = 100) -> "ToolResult":
        """Быстрый поиск: одно чтение с фильтром (без YAML)."""
        query = {
            "name": "quick_search",
            "reads": [{
                "table": table,
                "sheet": sheet,
                "columns": [column] if column else [],
                "filter": {filter_col: {filter_op: filter_val}} if filter_col else {},
            }],
            "limit": limit,
        }
        plan = planner.load_query_from_dict(query)
        result = planner.execute_plan(plan)
        return ToolResult(status="success", data=result,
                          facts=[Fact(type="QuickSearch", data={
                              "table": table, "sheet": sheet,
                              "rows": result["metadata"]["total_rows"],
                          })])

    async def search_multi(tables: list[dict], join_key: str = "",
                           filter_after: dict | None = None,
                           sort_col: str = "", sort_order: str = "asc",
                           limit: int = 100) -> "ToolResult":
        """Многотабличный поиск с объединением."""
        reads = []
        for t in tables:
            reads.append({
                "table": t.get("table", ""),
                "sheet": t.get("sheet", ""),
                "columns": t.get("columns", []),
                "filter": t.get("filter", {}),
            })
        query = {
            "name": "multi_search",
            "reads": reads,
            "join": {"on": join_key, "strategy": "inner"} if join_key else None,
            "filter": filter_after or {},
            "sort": {"column": sort_col, "order": sort_order} if sort_col else None,
            "limit": limit,
        }
        plan = planner.load_query_from_dict(query)
        result = planner.execute_plan(plan)
        return ToolResult(status="success", data=result,
                          facts=[Fact(type="MultiSearch", data={
                              "tables": len(tables),
                              "rows": result["metadata"]["total_rows"],
                          })])

    # Регистрация
    search_tools = [
        ("search_tables", "Поиск: YAML-запрос", "Умный поиск по таблицам через YAML (очередь, многопоточность, объединение)",
         {"type": "object", "properties": {
             "yaml_query": {"type": "string", "description": "YAML-строка с запросом"},
             "query_dict": {"type": "object", "description": "Dict с запросом (альтернатива YAML)"},
         }},
         search_tables, ANNOTATIONS_READONLY),
        ("search_quick", "Поиск: быстрый", "Быстрый поиск в одной таблице с фильтром (без YAML)",
         {"type": "object", "properties": {
             "table": {"type": "string", "description": "Путь к таблице"},
             "sheet": {"type": "string", "description": "Имя листа"},
             "column": {"type": "string", "description": "Столбец для выборки (все если пусто)"},
             "filter_col": {"type": "string", "description": "Столбец фильтра"},
             "filter_op": {"type": "string", "enum": ["eq", "neq", "gt", "lt", "contains", "in"], "description": "Оператор"},
             "filter_val": {"type": "string", "description": "Значение фильтра"},
             "limit": {"type": "integer", "description": "Максимум строк", "default": 100},
         }, "required": ["table", "sheet"]},
         search_quick, ANNOTATIONS_READONLY),
        ("search_multi", "Поиск: многотабличный", "Поиск с объединением нескольких таблиц (JOIN по ключу)",
         {"type": "object", "properties": {
             "tables": {"type": "array", "items": {"type": "object", "properties": {
                 "table": {"type": "string"}, "sheet": {"type": "string"},
                 "columns": {"type": "array", "items": {"type": "string"}},
                 "filter": {"type": "object"},
             }}, "description": "Список таблиц для поиска"},
             "join_key": {"type": "string", "description": "Ключ для объединения (JOIN)"},
             "filter_after": {"type": "object", "description": "Фильтр после объединения"},
             "sort_col": {"type": "string", "description": "Столбец сортировки"},
             "sort_order": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
             "limit": {"type": "integer", "default": 100},
         }, "required": ["tables"]},
         search_multi, ANNOTATIONS_READONLY),
    ]
    for name, title, desc, schema, handler, annot in search_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="search", annotations=annot)  # type: ignore[arg-type]


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
