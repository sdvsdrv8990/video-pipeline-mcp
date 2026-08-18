"""core/search/fs_searcher.py — умный поиск по файловой системе.

## Назначение
Обход `workspace/` по задаче `FsSearchTask` (её же собирает `load_query` из YAML) с фильтрами
по типу сущности, ID/владельцу, имени, расширению, содержимому, размеру и дате модификации.

## Границы
Типы сущностей и раскладка каталогов не зашиты — их даёт инжектируемая таксономия
(`core/ids/taxonomy.py` поверх `config/templates/workspace/*.tpl.yaml`); без неё тип "unknown".
Личность файла берётся из реестра, а не из имени (F60). Закрытые каталоги отсекаются
до чтения содержимого (S23).
"""

import re
from pathlib import Path, PurePosixPath
from dataclasses import dataclass, field
import concurrent.futures
from datetime import datetime

from core.paths import is_secret_path, safe_resolve  # D1/G17 + S23: секреты вне выдачи


@dataclass
class FileResult:
    """Результат поиска файла."""
    path: str
    name: str
    size: int
    modified: str
    entity_type: str = ""
    entity_id: str = ""      # собственный ID файла (если ему присвоен), F60
    parent_path: str = ""
    owner_id: str = ""       # ближайшая зарегистрированная сущность-владелец
    chain: str = ""          # цепочка владельцев сверху вниз (S18-g)


@dataclass
class FsSearchTask:
    """Задача на поиск в файловой системе."""
    id: str
    root: str
    entity_types: list[str] = field(default_factory=list)
    id_pattern: str = ""
    name_pattern: str = ""
    extensions: list[str] = field(default_factory=list)
    content_keywords: list[str] = field(default_factory=list)
    size_min: int = 0
    size_max: int = 0
    modified_after: str = ""
    modified_before: str = ""
    owner_id: str = ""       # всё, что принадлежит сущности (её файлы и файлы её потомков)
    chain_prefix: str = ""   # всё поддерево: префикс цепочки владельцев
    limit: int = 100
    status: str = "pending"
    result: list = field(default_factory=list)
    error: str | None = None


class FsSearchError(Exception):
    """Ошибка поиска файловой системы."""
    def __init__(self, code: str, message: str, reason: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason


# Маппинг расширений на типы файлов
FILE_TYPE_MAP = {
    ".xlsx": "table",
    ".json": "data",
    ".md": "memory",
    ".yaml": "config",
    ".tpl.yaml": "template",
}


class FsSearcher:
    """Умный поиск по файловой системе.

    Attributes:
        workspace: путь к workspace/
    """

    def __init__(self, workspace: str | Path, registry=None, taxonomy=None):
        self.workspace = Path(workspace)
        # F60: личность файлов живёт в реестре, а не в их именах. Без реестра поиск
        # продолжает работать, но ID/владельца отдать неоткуда.
        self.registry = registry
        self.taxonomy = taxonomy

    def _path_index(self) -> dict:
        """Снимок реестра «путь → запись» один раз на поиск (а не на каждый файл)."""
        if self.registry is None:
            return {}
        return {str(e.get("path", "")).strip("/"): e for e in self.registry.all()}

    @staticmethod
    def _lineage(rel_path: str, index: dict) -> tuple[str, list]:
        """Собственный ID файла (если зарегистрирован) и цепочка владельцев сверху вниз."""
        parts = PurePosixPath(rel_path).parts
        chain = []
        own = ""
        for i in range(len(parts)):
            rec = index.get("/".join(parts[: i + 1]))
            if not rec:
                continue
            if i == len(parts) - 1:
                own = rec["id"]
            else:
                chain.append(rec)
        return own, chain

    def search(self, task: FsSearchTask) -> list[FileResult]:
        """Выполнение задачи поиска."""
        task.status = "running"
        try:
            # task.root под контролем клиента → containment внутри workspace/ (анти-traversal).
            try:
                root = safe_resolve(str(task.root), self.workspace) if task.root else self.workspace.resolve()
            except ValueError:
                raise FsSearchError("PATH_ESCAPE", f"root вне workspace: {task.root}")
            if not root.exists():
                raise FsSearchError("PATH_NOT_FOUND", f"Каталог не найден: {task.root}")

            index = self._path_index()
            results = []
            for item in root.rglob("*"):
                if not item.is_file():
                    continue
                # S23: обход идёт по диску, а не через резолвер — закрытый каталог не попадает
                # ни в имена, ни в поиск по содержимому.
                if is_secret_path(item.relative_to(self.workspace.resolve())):
                    continue

                # Фильтр по расширению
                if task.extensions and item.suffix not in task.extensions:
                    continue

                # Фильтр по размеру
                size = item.stat().st_size
                if task.size_min and size < task.size_min:
                    continue
                if task.size_max and size > task.size_max:
                    continue

                # Фильтр по дате модификации
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                if task.modified_after:
                    after = datetime.fromisoformat(task.modified_after)
                    if mtime < after:
                        continue
                if task.modified_before:
                    before = datetime.fromisoformat(task.modified_before)
                    if mtime > before:
                        continue

                # Определяем тип сущности по пути
                entity_type = self._detect_entity_type(item)

                # Фильтр по типу сущности
                if task.entity_types and entity_type not in task.entity_types:
                    continue

                # Фильтр по имени (regex)
                if task.name_pattern:
                    if not re.search(task.name_pattern, item.name):
                        continue

                # Фильтр по содержимому
                if task.content_keywords:
                    try:
                        content = item.read_text(encoding="utf-8", errors="ignore")
                        if not all(kw.lower() in content.lower() for kw in task.content_keywords):
                            continue
                    except Exception:
                        continue

                # F60: личность берётся из РЕЕСТРА по вместимости, а не из имени файла.
                rel = str(item.relative_to(self.workspace))
                entity_id, lineage = self._lineage(rel, index)
                owner_id = lineage[-1]["id"] if lineage else ""
                chain = "/".join(e["id"] for e in lineage)

                # Фильтр по ID: собственный ID файла ИЛИ его владельцы (иначе фильтр
                # находил бы только файлы с личностью, а их меньшинство).
                if task.id_pattern:
                    haystack = "/".join(filter(None, [chain, entity_id]))
                    if not re.search(task.id_pattern, haystack):
                        continue

                # Всё, что принадлежит сущности (сама сущность или её потомки)
                if task.owner_id and task.owner_id not in ([e["id"] for e in lineage] + [entity_id]):
                    continue

                # Всё поддерево: цепочка начинается с указанного префикса
                if task.chain_prefix and not chain.startswith(task.chain_prefix.strip("/")):
                    continue

                results.append(FileResult(
                    path=rel,
                    name=item.name,
                    size=size,
                    modified=mtime.isoformat(),
                    entity_type=entity_type,
                    entity_id=entity_id,
                    parent_path=str(item.parent.relative_to(self.workspace)),
                    owner_id=owner_id,
                    chain=chain,
                ))

                if len(results) >= task.limit:
                    break

            task.status = "done"
            task.result = results
            return results

        except Exception as e:
            task.status = "error"
            task.error = str(e)
            raise

    def search_parallel(self, tasks: list[FsSearchTask], max_workers: int = 4) -> dict:
        """Параллельный поиск по нескольким задачам."""
        results = {}
        errors = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for task in tasks:
                future = executor.submit(self.search, task)
                futures[future] = task

            for future in concurrent.futures.as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                    results[task.id] = result
                except Exception as e:
                    errors.append({"task_id": task.id, "error": str(e)})

        return {"results": results, "errors": errors}

    def _detect_entity_type(self, path: Path) -> str:
        """Тип сущности по ОБЪЯВЛЕННОЙ раскладке (таксономия), а не по карте путей в коде.

        Спуск по контейнерам: корневой контейнер даёт тип, дальше — `children[].container`
        родителя. Контейнер с токеном `{parent:<тип>}` может занимать один сегмент или два
        (наш канал подставлен или опущен, §4), поэтому разбор перебирает варианты и берёт тот,
        что уходит глубже. Без таксономии — "unknown": молча не гадаем.
        """
        if self.taxonomy is None:
            return "unknown"
        try:
            parts = list(path.relative_to(self.workspace).parts)
        except ValueError:
            return "unknown"

        def walk(current: str, i: int) -> tuple[str, int]:
            if i >= len(parts) - 1:          # нужен хотя бы сегмент имени после контейнера
                return current, i
            if not current:
                root_type = self.taxonomy.type_for_root_container(parts[i])
                return walk(root_type, i + 2) if root_type else (current, i)
            best = (current, i)
            for child_type, consumed in self.taxonomy.child_matches(current, parts[i:]):
                deeper = walk(child_type, i + consumed + 1)
                if deeper[1] > best[1]:
                    best = deeper
            return best

        found, _ = walk("", 0)
        return found or "unknown"

    def load_query(self, yaml_str: str) -> FsSearchTask:
        """Загрузка YAML-запроса."""
        import yaml
        data = yaml.safe_load(yaml_str) or {}
        return FsSearchTask(
            id="task_0",
            root=data.get("root", ""),
            entity_types=data.get("entity_types", []),
            id_pattern=data.get("id_pattern", ""),
            owner_id=data.get("owner_id", ""),
            chain_prefix=data.get("chain_prefix", ""),
            name_pattern=data.get("name_pattern", ""),
            extensions=data.get("extensions", []),
            content_keywords=data.get("content_keywords", []),
            size_min=data.get("size", {}).get("min", 0),
            size_max=data.get("size", {}).get("max", 0),
            modified_after=data.get("modified", {}).get("after", ""),
            modified_before=data.get("modified", {}).get("before", ""),
            limit=data.get("limit", 100),
        )
