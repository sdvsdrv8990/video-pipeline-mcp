"""core/search/fs_searcher.py — поиск по рабочей области.

Типы сущностей и раскладка каталогов не зашиты: их даёт инжектируемая таксономия, без неё тип
остаётся неизвестным, а не угадывается. Личность файла берётся из реестра, а не из имени — имя
переименуют. Закрытые каталоги отсекаются ДО чтения содержимого, иначе поиск по содержимому стал бы
дверью к ключам.
"""

import re
from pathlib import Path, PurePosixPath
from dataclasses import dataclass, field
import concurrent.futures
from datetime import datetime

from core.paths import Files  # обход через дверь: секреты и ссылки наружу вне выдачи
from core.contracts import ContractError


@dataclass
class FileResult:
    """Результат поиска файла."""
    path: str
    name: str
    size: int
    modified: str
    entity_type: str = ""
    entity_id: str = ""      # собственный ID файла (если ему присвоен)
    parent_path: str = ""
    owner_id: str = ""       # ближайшая зарегистрированная сущность-владелец
    chain: str = "" # цепочка владельцев сверху вниз


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
    # Что не удалось прочитать — каталог при обходе, файл при фильтре по содержимому ({path, reason}). Молчаливый
    # пропуск неотличим от «не совпало», и клиент считает поиск полным, а он неполный.
    unreadable: list[dict] = field(default_factory=list)


class FsSearchError(ContractError):
    """Ошибка поиска файловой системы."""


class FsSearcher:
    """Умный поиск по файловой системе.

    Attributes:
        workspace: путь к workspace/
    """

    def __init__(self, workspace: str | Path, registry=None, taxonomy=None, *, read_bytes: int):
        self.workspace = Path(workspace)
        # Личность файлов живёт в реестре, а не в их именах. Без реестра поиск
        # продолжает работать, но ID/владельца отдать неоткуда.
        self.registry = registry
        self.taxonomy = taxonomy
        # Сколько байт файла читать ради совпадения по содержимому (`firewall.yaml → file_limits.read_bytes`).
        self.read_bytes = read_bytes

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
            # task.root под контролем клиента → обход слоя: каждый элемент через дверь; пусто — сам корень.
            files = Files(self.workspace)
            walk = files.walk(task.root or "")
            if not walk.start.exists():
                raise FsSearchError("PATH_NOT_FOUND", f"Каталог не найден: {task.root}")

            index = self._path_index()
            results = []
            for entry in walk:
                if entry.kind != "file":
                    continue
                # Имя — то, что видит клиент; размер, время и содержимое — настоящего файла.
                named, item = PurePosixPath(entry.path), entry.real

                # Фильтр по расширению
                if task.extensions and named.suffix not in task.extensions:
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
                entity_type = self._detect_entity_type(self.workspace / entry.path)

                # Фильтр по типу сущности
                if task.entity_types and entity_type not in task.entity_types:
                    continue

                # Фильтр по имени (regex)
                if task.name_pattern:
                    if not re.search(task.name_pattern, named.name):
                        continue

                # Фильтр по содержимому
                if task.content_keywords:
                    try:
                        content = files.read(entry.path, self.read_bytes, whole=True, errors="ignore").text
                    except OSError as e:
                        # Не прочитали ≠ не совпало: считаем потерю поимённо и отдаём наверх.
                        task.unreadable.append(files.unread(entry.path, e))
                        continue
                    except ContractError as e:  # больше предела — тоже не прочитан, а не «не совпал»
                        task.unreadable.append({"path": entry.path, "reason": e.code})
                        continue
                    if not all(kw.lower() in content.lower() for kw in task.content_keywords):
                        continue

                # Личность берётся из РЕЕСТРА по вместимости, а не из имени файла.
                rel = entry.path
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
                    name=named.name,
                    size=size,
                    modified=mtime.isoformat(),
                    entity_type=entity_type,
                    entity_id=entity_id,
                    parent_path=str(named.parent),
                    owner_id=owner_id,
                    chain=chain,
                ))

                if len(results) >= task.limit:
                    break

            task.unreadable.extend(walk.unreadable)
            task.status = "done"
            task.result = results
            return results

        except Exception as e:
            task.status = "error"
            task.error = str(e)
            raise

    def search_parallel(self, tasks: list[FsSearchTask]) -> dict:
        """Параллельный поиск по нескольким задачам; места даёт узел ёмкости (класс `search`), вызывающий их не решает."""
        from core.capacity import Capacity

        results = {}
        errors = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=Capacity().places("search")) as executor:
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
