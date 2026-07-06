"""
core/search/fs_searcher.py — Умный поиск по файловой системе

## Назначение
Поиск файлов по типам сущностей (video, channel, competitor, niche, network, scene, asset),
ID, датам, содержимому. Работает через YAML-запросы с очередью задач и многопоточностью.

## Формат YAML-запроса
```yaml
name: "Поиск видео канала"
description: "Найти все видео в канале my_channel"

# Корневой каталог (относительно workspace)
root: "channels/my_channel"

# Фильтры по типу сущности
entity_types:
  - "video"
  - "competitor_video"

# Фильтры по ID (regex)
id_pattern: "VID_*"

# Фильтры по имени (regex)
name_pattern: ".*intro.*"

# Фильтры по расширению
extensions:
  - ".xlsx"
  - ".json"
  - ".md"

# Фильтр по содержимому (ключевые слова)
content_keywords:
  - "UNIQUENESS"
  - "status"

# Фильтр по размеру файла
size:
  min: 100
  max: 1000000

# Фильтр по дате модификации
modified:
  after: "2026-07-01"
  before: "2026-07-05"

# Максимум результатов
limit: 100

# Сортировка
sort:
  field: "modified"
  order: "desc"
```

## Структура workspace
```
workspace/
├── niches/
│   └── {niche_name}/
│       ├── networks/
│       │   └── {network_name}/
│       │       ├── channels/
│       │       │   └── {channel_name}/
│       │       │       ├── videos/
│       │       │       │   └── {video_name}/
│       │       │       │       ├── assets/
│       │       │       │       │   ├── svg/
│       │       │       │       │   ├── scenes/
│       │       │       │       │   ├── audio/
│       │       │       │       │   └── transitions/
│       │       │       │       ├── renders/
│       │       │       │       ├── video_data.xlsx
│       │       │       │       ├── read.json
│       │       │       │       └── project_memory.md
│       │       │       ├── competitors/
│       │       │       │   └── {competitor_name}/
│       │       │       │       ├── videos/
│       │       │       │       │   └── {comp_video_name}/
│       │       │       │       ├── competitor_channel_data.xlsx
│       │       │       │       └── read.json
│       │       │       ├── channel_data.xlsx
│       │       │       └── project_memory.md
│       │       ├── network_config.xlsx
│       │       └── _NETWORK_INDEX.md
│       ├── niche_read.json
│       └── _NICHE_INDEX.md
```
"""

import re
from pathlib import Path
from dataclasses import dataclass, field
import concurrent.futures
from datetime import datetime

from core.paths import safe_resolve  # D1/G17: единый containment внутри workspace/


@dataclass
class FileResult:
    """Результат поиска файла."""
    path: str
    name: str
    size: int
    modified: str
    entity_type: str = ""
    entity_id: str = ""
    parent_path: str = ""


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


# Маппинг типов сущностей на паттерны путей
ENTITY_PATH_PATTERNS = {
    "niche": ["niches/*/"],
    "network": ["niches/*/networks/*/"],
    "channel": ["niches/*/networks/*/channels/*/"],
    "video": ["niches/*/networks/*/channels/*/videos/*/"],
    "competitor_channel": ["niches/*/networks/*/channels/*/competitors/*/"],
    "competitor_video": ["niches/*/networks/*/channels/*/competitors/*/videos/*/"],
    "asset": ["*/assets/*"],
    "scene": ["*/assets/scenes/*"],
    "render": ["*/renders/*"],
}

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

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace)

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

            results = []
            for item in root.rglob("*"):
                if not item.is_file():
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

                # Фильтр по ID (из имени файла)
                entity_id = self._extract_id(item)
                if task.id_pattern:
                    if not re.search(task.id_pattern, entity_id):
                        continue

                results.append(FileResult(
                    path=str(item.relative_to(self.workspace)),
                    name=item.name,
                    size=size,
                    modified=mtime.isoformat(),
                    entity_type=entity_type,
                    entity_id=entity_id,
                    parent_path=str(item.parent.relative_to(self.workspace)),
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
        """Тип сущности по контейнер-маркерам пути (устойчиво к именам ниши/сети/канала).

        Иерархия: niches/<n>/networks/<net>/channels/<ch>/videos/<v> и
        networks/<net>/competitors/<comp>/videos/<v>. Классификация — по самому
        глубокому известному контейнеру.
        """
        try:
            parts = path.relative_to(self.workspace).parts
        except ValueError:
            return "unknown"
        if "niches" not in parts:
            return "unknown"

        def has(marker: str) -> bool:
            return marker in parts

        # Ветка конкурентов (competitors — ребёнок network, содержит competitor_video)
        if has("competitors"):
            ci = parts.index("competitors")
            if has("videos") and parts.index("videos") > ci:
                return "competitor_video"
            return "competitor_channel"
        # Наша ветка: channels → videos
        if has("videos"):
            return "video"
        if has("channels"):
            return "channel"
        if has("networks"):
            return "network"
        # Что-то прямо под niches/<имя_ниши> (без networks) — уровень ниши
        if len(parts) > parts.index("niches") + 1:
            return "niche"
        return "unknown"

    def _extract_id(self, path: Path) -> str:
        """Извлечение ID из имени файла (PREFIX_hex)."""
        match = re.search(r'([A-Z]+_[0-9a-f]{32})', path.stem)
        return match.group(1) if match else ""

    def load_query(self, yaml_str: str) -> FsSearchTask:
        """Загрузка YAML-запроса."""
        import yaml
        data = yaml.safe_load(yaml_str) or {}
        return FsSearchTask(
            id="task_0",
            root=data.get("root", ""),
            entity_types=data.get("entity_types", []),
            id_pattern=data.get("id_pattern", ""),
            name_pattern=data.get("name_pattern", ""),
            extensions=data.get("extensions", []),
            content_keywords=data.get("content_keywords", []),
            size_min=data.get("size", {}).get("min", 0),
            size_max=data.get("size", {}).get("max", 0),
            modified_after=data.get("modified", {}).get("after", ""),
            modified_before=data.get("modified", {}).get("before", ""),
            limit=data.get("limit", 100),
        )
