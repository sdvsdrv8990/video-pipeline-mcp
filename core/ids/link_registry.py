"""
core/ids/link_registry.py — Реестр связей сущностей (анонимные → ORPHAN)

## Назначение
Спека предусматривает в core/ids «реестр связей: анонимные → ORPHAN»
(ИНСТРУКЦИЯ_структура_и_ядро.md). Здесь он и живёт.

Хранит созданные узлы структуры (niche/network/channel/video/competitor_*) с их
`parent_ids` и отвечает на вопросы:
- **Кто висит (ORPHAN)?** Сущность, которой по типу НУЖЕН родитель определённого типа,
  но его нет среди parent_ids. Пример: конкурент без нашего канала (§4 — конкуренты
  группируются по нашему каналу). → уведомление `UNLINKED_ENTITY`.
- **У кого нет ребёнка (мягко)?** Наш канал, на который не ссылается ни один конкурент.
- **Связать (в ОДНОМ месте).** `link(child, parent)` добавляет parent_id ребёнку. Один
  вызов — источник истины реестр; сервер сам выводит группировку. Не правим оба дерева
  (экономит токены, исключает рассинхрон).

## Границы
- Персист: `workspace/_id_registry.json`, атомарно (D9, переиспользуем _atomic_write_json).
- Не материализует файлы (это TemplateEngine, Ф1). Здесь только связи.
- Правило «нужного родителя» декларативно в REQUIRED_PARENT_TYPE — без `if type == ...` по коду.
"""

import json
import threading
from pathlib import Path

from core.state.state_manager import _atomic_write_json  # D9: единый атомарный писатель

REGISTRY_FILE = "_id_registry.json"

# Декларация: какому типу для «непровисания» ОБЯЗАТЕЛЕН родитель какого типа.
# Конкурент группируется по нашему каналу (ИНСТРУКЦИЯ_шаблоны.md §4).
REQUIRED_PARENT_TYPE = {
    "competitor_channel": "channel",
}


class LinkError(Exception):
    """Ошибка реестра связей в формате контракта (маппится обёрткой в ErrorDetail)."""

    def __init__(self, code: str, message: str, reason: str = "", suggested_tool: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool


class LinkRegistry:
    """Реестр сущностей + их связей. Персист в workspace/_id_registry.json."""

    def __init__(self, workspace_path):
        self.ws = Path(workspace_path)
        self.path = self.ws / REGISTRY_FILE
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("entities"), dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"entities": {}}

    def register(self, entity: dict, check_unique: bool = False) -> dict:
        """Upsert сущности по id. entity: {id,type,name,path,parent_ids}.
        parent_ids сливаются (не теряем ранее известных родителей).
        check_unique=True → проверяет что ID не занят другой сущностью (для файлов)."""
        with self._lock:
            data = self._load()
            eid = entity["id"]
            prev = data["entities"].get(eid, {})
            # Проверка уникальности: если ID занят другой сущностью — ошибка
            if check_unique and prev and prev.get("type") != entity.get("type"):
                raise LinkError(
                    "DUPLICATE_ID", f"ID '{eid}' уже занят сущностью типа '{prev.get('type')}'",
                    "Сгенерируй новый ID или проверь реестр.", "structure_status")
            merged = list(dict.fromkeys(
                (prev.get("parent_ids") or []) + list(entity.get("parent_ids") or [])))
            rec = {
                "id": eid,
                "type": entity["type"],
                "name": entity["name"],
                "path": entity.get("path", ""),
                "parent_ids": merged,
                "kind": entity.get("kind", "node"),
            }
            data["entities"][eid] = rec
            _atomic_write_json(self.path, data)
            return rec

    def get(self, entity_id: str) -> dict | None:
        return self._load()["entities"].get(entity_id)

    def find(self, type: str | None = None, name: str | None = None) -> list[dict]:
        out = []
        for e in self._load()["entities"].values():
            if type is not None and e["type"] != type:
                continue
            if name is not None and e["name"] != name:
                continue
            out.append(e)
        return out

    @staticmethod
    def _parent_types(entity: dict, entities: dict) -> set:
        return {entities[pid]["type"] for pid in entity.get("parent_ids", []) if pid in entities}

    def find_orphans(self) -> list[dict]:
        """Висящие: тип требует родителя REQUIRED_PARENT_TYPE, но его нет среди parent_ids."""
        entities = self._load()["entities"]
        orphans = []
        for e in entities.values():
            need = REQUIRED_PARENT_TYPE.get(e["type"])
            if not need:
                continue
            if need not in self._parent_types(e, entities):
                orphans.append({"id": e["id"], "type": e["type"], "name": e["name"],
                                "path": e["path"], "needs_parent_type": need})
        return orphans

    def find_childless(self, parent_type: str, child_type: str) -> list[dict]:
        """parent_type-сущности, на которые не ссылается ни один child_type (мягкое уведомление)."""
        entities = self._load()["entities"]
        referenced = set()
        for e in entities.values():
            if e["type"] == child_type:
                referenced.update(e.get("parent_ids", []))
        return [{"id": e["id"], "type": e["type"], "name": e["name"], "path": e["path"]}
                for e in entities.values()
                if e["type"] == parent_type and e["id"] not in referenced]

    def _resolve_one(self, etype: str, name: str) -> dict:
        hits = self.find(type=etype, name=name)
        if not hits:
            raise LinkError("ENTITY_NOT_FOUND", f"Нет сущности {etype}:{name} в реестре.",
                            "Сначала создай её через structure_create.", "structure_status")
        if len(hits) > 1:
            raise LinkError("VALIDATION_ERROR", f"Неоднозначно: {etype}:{name} встречается {len(hits)} раз.",
                            "Уточни через id (реестр содержит несколько).", "structure_status")
        return hits[0]

    def check_integrity(self) -> dict:
        """Проверка целостности реестра: висящие ссылки, дубликаты путей, сироты."""
        data = self._load()
        entities = data.get("entities", {})
        issues = []
        paths_seen = {}
        ids_by_type = {}
        for eid, e in entities.items():
            # Проверка висящих ссылок (parent_ids ведут на несуществующие ID)
            for pid in e.get("parent_ids", []):
                if pid not in entities:
                    issues.append({"type": "broken_reference", "id": eid, "missing_parent": pid})
            # Проверка дубликатов путей
            path = e.get("path", "")
            if path:
                if path in paths_seen:
                    issues.append({"type": "duplicate_path", "id": eid, "path": path,
                                   "also": paths_seen[path]})
                else:
                    paths_seen[path] = eid
            # Подсчёт по типам
            t = e.get("type", "unknown")
            ids_by_type[t] = ids_by_type.get(t, 0) + 1
        # Проверка сирот
        orphans = self.find_orphans()
        for o in orphans:
            issues.append({"type": "orphan", "id": o["id"], "type": o["type"],
                           "name": o["name"], "needs": o["needs_parent_type"]})
        return {
            "total_entities": len(entities),
            "by_type": ids_by_type,
            "issues_count": len(issues),
            "issues": issues,
        }

    def link(self, child_type: str, child_name: str, parent_type: str, parent_name: str) -> dict:
        """Связать ребёнка с родителем В ОДНОМ месте: добавить parent_id ребёнку."""
        with self._lock:
            child = self._resolve_one(child_type, child_name)
            parent = self._resolve_one(parent_type, parent_name)
            data = self._load()
            rec = data["entities"][child["id"]]
            if parent["id"] not in rec["parent_ids"]:
                rec["parent_ids"].append(parent["id"])
            _atomic_write_json(self.path, data)
            return {"child": rec, "parent_id": parent["id"], "parent_type": parent_type, "parent_name": parent_name}

    def migrate(self, entity_id: str, new_path: str, new_parent_ids: list[str] | None = None) -> dict:
        """Миграция сущности: физический перенос + обновление реестра.
        Используется когда родитель появился позже (напр. конкурент без канала → привязка к каналу)."""
        with self._lock:
            data = self._load()
            if entity_id not in data["entities"]:
                raise LinkError("ENTITY_NOT_FOUND", f"Сущность {entity_id} не найдена в реестре.",
                                "Сначала создай её через structure_create.", "structure_status")
            rec = data["entities"][entity_id]
            old_path = rec.get("path", "")
            rec["path"] = new_path
            if new_parent_ids is not None:
                rec["parent_ids"] = list(dict.fromkeys(rec.get("parent_ids", []) + new_parent_ids))
            _atomic_write_json(self.path, data)
            return {"id": entity_id, "old_path": old_path, "new_path": new_path,
                    "parent_ids": rec["parent_ids"]}
