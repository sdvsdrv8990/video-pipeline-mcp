"""
tools/filesystem — группа инструментов файловой системы (12 штук).

Тонкие обёртки: containment путей делает `core/paths` (через `ctx.resolve`), поиск —
`core/search/fs_searcher`, маппинг ошибок — реестр реакций (через `ctx.err`).
Здесь только разбор параметров и упаковка в `ToolResult`.

Контракт (имена/группа/схемы/аннотации) зафиксирован эталоном
`tests/quick/tools_inventory.golden.json` — менять только осознанно.
"""

import shutil
from pathlib import Path

import yaml

from core.contracts import Fact, ToolResult
from core.engine import Engine
from core.search.fs_searcher import FsSearcher, FsSearchError, FsSearchTask
from tools._context import ANNOTATIONS_MODIFY, ANNOTATIONS_READONLY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы filesystem в движке."""

    fs_searcher = FsSearcher(ctx.workspace_path)

    async def fs_get_directory_tree(path: str = ".") -> "ToolResult":
        """Получение дерева каталогов."""
        try:
            target = ctx.resolve(path)
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not target.exists():
            return ctx.err("PATH_NOT_FOUND", f"Path not found: {path}")
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
            target = ctx.resolve(path)
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not target.exists():
            return ctx.err("FILE_NOT_FOUND", f"File not found: {path}")
        content = target.read_text(encoding="utf-8")
        return ToolResult(status="success", data={"content": content, "size": len(content)}, facts=[Fact(type="FileRead", data={"path": path, "size": len(content)})])

    async def fs_create_file(path: str, content: str = "") -> "ToolResult":
        """Создание файла."""
        try:
            target = ctx.resolve(path)
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(status="success", data={"created": path, "size": len(content)}, facts=[Fact(type="FileCreated", data={"path": path, "size": len(content)})])

    async def fs_write_file(path: str, content: str) -> "ToolResult":
        """Полная перезапись файла."""
        try:
            target = ctx.resolve(path)
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        old_size = target.stat().st_size if target.exists() else 0
        target.write_text(content, encoding="utf-8")
        return ToolResult(status="success", data={"written": path, "size": len(content), "old_size": old_size}, facts=[Fact(type="FileWritten", data={"path": path, "size": len(content)})])

    async def fs_move(source: str, destination: str) -> "ToolResult":
        """Перемещение файла или каталога."""
        try:
            src, dst = ctx.resolve(source), ctx.resolve(destination)
        except ValueError:
            return ctx.err("PATH_ESCAPE", "Path escapes workspace")
        if not src.exists():
            return ctx.err("FILE_NOT_FOUND", f"Source not found: {source}")
        if dst.exists():
            return ctx.err("FILE_EXISTS", f"Destination exists: {destination}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return ToolResult(status="success", data={"source": source, "destination": destination}, facts=[Fact(type="FileMoved", data={"source": source, "destination": destination})])

    async def fs_rename(path: str, new_name: str) -> "ToolResult":
        """Переименование файла или каталога."""
        try:
            src = ctx.resolve(path)
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not src.exists():
            return ctx.err("FILE_NOT_FOUND", f"Not found: {path}")
        dst = src.parent / new_name
        try:
            dst = ctx.resolve(str(dst.relative_to(ctx.workspace_path)))
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"New name escapes workspace: {new_name}")
        if dst.exists():
            return ctx.err("FILE_EXISTS", f"Name exists: {new_name}")
        src.rename(dst)
        return ToolResult(status="success", data={"old_path": path, "new_path": str(dst.relative_to(ctx.workspace_path))}, facts=[Fact(type="FileRenamed", data={"old": path, "new": new_name})])

    async def fs_delete(path: str, force: bool = False) -> "ToolResult":
        """Удаление файла или каталога."""
        try:
            target = ctx.resolve(path)
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not target.exists():
            return ctx.err("FILE_NOT_FOUND", f"Not found: {path}")
        if target.is_dir() and not force:
            contents = list(target.iterdir())
            if contents:
                return ctx.err("DIRECTORY_NOT_EMPTY", f"Directory not empty: {path} ({len(contents)} items)")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return ToolResult(status="success", data={"deleted": path}, facts=[Fact(type="FileDeleted", data={"path": path})])

    # ─── Умный поиск по файловой системе ───

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
            return ctx.err(e.code, e.message, e.reason)
        except Exception as e:
            return ctx.err("INTERNAL_ERROR", f"Ошибка поиска: {e}")

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
            return ctx.err(e.code, e.message, e.reason)
        except Exception as e:
            return ctx.err("INTERNAL_ERROR", f"Ошибка поиска: {e}")

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
            return ctx.err("INTERNAL_ERROR", f"Ошибка поиска: {e}")

    async def fs_create_python_script(path: str, description: str = "") -> "ToolResult":
        """Создание Python-скрипта с каркасом."""
        try:
            target = ctx.resolve(path)
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not path.endswith(".py"):
            return ctx.err("INVALID_EXTENSION", f"Not a Python file: {path}")
        desc = description or target.stem
        # F45: каркас захардкожен здесь — должен переехать в config/templates/ (отдельный воркстрим).
        skeleton = f'"""\n{desc}\n"""\n\nimport sys\nfrom pathlib import Path\n\n\ndef main():\n    """Main entry point."""\n    print(f"Running {{__file__}}")\n    # TODO: implement\n    pass\n\n\nif __name__ == "__main__":\n    main()\n'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skeleton, encoding="utf-8")
        return ToolResult(status="success", data={"created": path, "size": len(skeleton)}, facts=[Fact(type="FileCreated", data={"path": path, "type": "python_script"})])

    async def fs_create_project_structure(template: str = "", fragments: list[dict] | None = None) -> "ToolResult":
        """Материализация структуры по шаблону или список фрагментов."""
        created, skipped = [], []
        if template:
            template_path = ctx.config_path / "templates" / "workspace" / f"{template}.yaml"
            if not template_path.exists():
                return ctx.err("TEMPLATE_NOT_FOUND", f"Template not found: {template}")
            tpl = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
            fragments = tpl.get("fragments", [])
        if not fragments:
            return ctx.err("NO_FRAGMENTS", "No fragments to create")
        for frag in fragments:
            name = frag.get("name", "")
            if not name:
                skipped.append({"reason": "no name", "fragment": frag})
                continue
            try:
                p = ctx.resolve(name)
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
