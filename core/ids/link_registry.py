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

    def __init__(self, workspace_path, identity=None):
        self.ws = Path(workspace_path)
        self.path = self.ws / REGISTRY_FILE
        self._lock = threading.Lock()
        # S9: личность инстанса. None → подпись не ставится и не проверяется (локальные
        # прогоны и тесты); в бою её выдаёт build_context, и тогда чужие записи отклоняются.
        self.identity = identity

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("entities"), dict):
                    self._verdict = self._check_seal(data)
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        self._verdict = ""
        return {"entities": {}}

    def _check_seal(self, data: dict) -> str:
        """S9: чья это запись. "" — наша (или проверка не подключена)."""
        if self.identity is None:
            return ""
        seal = data.get("_seal") or {}
        if not seal:
            # Реестр без подписи — наследство до S9. Усыновляем при первой же нашей записи,
            # дальше отсутствие подписи означает постороннюю правку.
            return ""
        return self.identity.verify({"entities": data["entities"]}, seal)

    def _guard_write(self, data: dict) -> None:
        """Отказ записи поверх чужого артефакта (S9): состояние не меняем."""
        verdict = getattr(self, "_verdict", "") or self._check_seal(data)
        if not verdict:
            return
        if verdict == "MACHINE_MISMATCH":
            raise LinkError(
                "MACHINE_MISMATCH",
                "Реестр создан на другой машине: запись заблокирована, чтение доступно.",
                "Если это ваш перенос (смена железа/восстановление), подтвердите его "
                "запуском server.py --re-adopt.", "structure_check_integrity")
        raise LinkError(
            "FOREIGN_WRITE",
            "Реестр подписан другим сервером или изменён вне этого сервера.",
            "Состояние не изменено. Проверьте, кто ещё пишет в этот workspace; "
            "восстановите файл из бэкапа или подтвердите присвоение через --re-adopt.",
            "structure_check_integrity")

    def _save(self, data: dict) -> None:
        """Атомарная запись с подписью инстанса (S9)."""
        if self.identity is not None:
            seal = self.identity.seal({"entities": data["entities"]})
            if seal:
                data["_seal"] = seal
        _atomic_write_json(self.path, data)

    @staticmethod
    def _norm(path: str) -> str:
        """Нормализация пути записи: без ведущего './', без краевых '/'."""
        p = (path or "").replace("\\", "/").strip()
        while p.startswith("./"):
            p = p[2:]
        return p.strip("/")

    def find_by_path(self, path: str) -> dict | None:
        """Кто зарегистрирован по этому пути (обратное отображение путь → сущность, F64)."""
        want = self._norm(path)
        if not want:
            return None
        for e in self._load()["entities"].values():
            if self._norm(e.get("path", "")) == want:
                return e
        return None

    def register(self, entity: dict, check_unique: bool = False) -> dict:
        """Upsert сущности по id. entity: {id,type,name,path,parent_ids}.
        parent_ids сливаются (не теряем ранее известных родителей).
        check_unique=True → проверяет что ID не занят другой сущностью (для файлов).

        Инвариант пути: один путь = одна сущность. Попытка завести ВТОРОЙ ID на уже
        занятый путь — раздвоение личности каталога (S18-h) → DUPLICATE_PATH."""
        with self._lock:
            data = self._load()
            self._guard_write(data)
            eid = entity["id"]
            prev = data["entities"].get(eid, {})
            # Проверка уникальности: если ID занят другой сущностью — ошибка
            if check_unique and prev and prev.get("type") != entity.get("type"):
                raise LinkError(
                    "DUPLICATE_ID", f"ID '{eid}' уже занят сущностью типа '{prev.get('type')}'",
                    "Сгенерируй новый ID или проверь реестр.", "structure_status")
            path_norm = self._norm(entity.get("path", ""))
            if path_norm:
                for other_id, other in data["entities"].items():
                    if other_id != eid and self._norm(other.get("path", "")) == path_norm:
                        raise LinkError(
                            "DUPLICATE_PATH",
                            f"Путь '{path_norm}' уже принадлежит сущности {other_id} ({other.get('type')}).",
                            "Переиспользуй существующий ID вместо генерации нового "
                            "(структура уже создана) или укажи другой каталог.",
                            "structure_resolve")
            merged = list(dict.fromkeys(
                (prev.get("parent_ids") or []) + list(entity.get("parent_ids") or [])))
            rec = {
                "id": eid,
                "type": entity["type"],
                "name": entity["name"],
                "path": path_norm,
                "parent_ids": merged,
                "kind": entity.get("kind", "node"),
            }
            data["entities"][eid] = rec
            self._save(data)
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

    def resolve_ref(self, entity_id: str = "", path: str = "",
                    etype: str = "", name: str = "") -> dict:
        """Адресация сущности: по ID → по пути → по (тип, имя). ID и путь однозначны всегда (F64).

        Пара (тип, имя) в иерархии неоднозначна по природе (два видео `intro` в разных каналах),
        поэтому при совпадении нескольких отдаём КАНДИДАТОВ с их ID и путями — чтобы перезвать
        по ID, а не гадать.
        """
        if entity_id:
            rec = self.get(entity_id)
            if not rec:
                raise LinkError("ENTITY_NOT_FOUND", f"Сущности {entity_id} нет в реестре.",
                                "Проверь ID через structure_status или найди сущность поиском.",
                                "structure_status")
            return rec
        if path:
            rec = self.find_by_path(path)
            if not rec:
                raise LinkError("ENTITY_NOT_FOUND", f"По пути '{path}' сущность не зарегистрирована.",
                                "Посмотри, что известно о каталоге, через structure_resolve.",
                                "structure_resolve")
            return rec
        return self._resolve_one(etype, name)

    def _resolve_one(self, etype: str, name: str) -> dict:
        hits = self.find(type=etype, name=name)
        if not hits:
            raise LinkError("ENTITY_NOT_FOUND", f"Нет сущности {etype}:{name} в реестре.",
                            "Сначала создай её через structure_create.", "structure_status")
        if len(hits) > 1:
            listing = "; ".join(f"{h['id']} → {h['path']}" for h in hits)
            raise LinkError(
                "VALIDATION_ERROR",
                f"Неоднозначно: {etype}:{name} встречается {len(hits)} раз. Кандидаты: {listing}",
                "Имя в иерархии не адрес — перезови с child_id/parent_id (или путём) из списка выше.",
                "structure_status")
        return hits[0]

    def check_integrity(self) -> dict:
        """Проверка целостности реестра: висящие ссылки, дубликаты путей, сироты."""
        data = self._load()
        entities = data.get("entities", {})
        issues = []
        paths_seen: dict = {}
        ids_by_type: dict = {}
        for eid, e in entities.items():
            # Проверка висящих ссылок (parent_ids ведут на несуществующие ID)
            for pid in e.get("parent_ids", []):
                if pid not in entities:
                    issues.append({"type": "broken_reference", "id": eid, "missing_parent": pid})
            # Проверка дубликатов путей
            path = self._norm(e.get("path", ""))
            if path:
                if path in paths_seen:
                    issues.append({"type": "duplicate_path", "id": eid, "path": path,
                                   "also": paths_seen[path]})
                else:
                    paths_seen[path] = eid
                # Рассинхрон реестра с диском: запись есть, каталога/файла нет (F65).
                # Перенос мимо реестра больше не проходит молча.
                if not (self.ws / path).exists():
                    issues.append({"type": "missing_path", "id": eid, "path": path,
                                   "entity_type": e.get("type", "")})
            # Подсчёт по типам
            t = e.get("type", "unknown")
            ids_by_type[t] = ids_by_type.get(t, 0) + 1
        # Проверка сирот
        orphans = self.find_orphans()
        for o in orphans:
            issues.append({"type": "orphan", "id": o["id"], "entity_type": o["type"],
                           "name": o["name"], "needs": o["needs_parent_type"]})
        return {
            "total_entities": len(entities),
            "by_type": ids_by_type,
            "issues_count": len(issues),
            "issues": issues,
        }

    def link(self, child_type: str = "", child_name: str = "", parent_type: str = "",
             parent_name: str = "", child_id: str = "", parent_id: str = "",
             child_path: str = "", parent_path: str = "") -> dict:
        """Связать ребёнка с родителем В ОДНОМ месте: добавить parent_id ребёнку.

        Адресовать можно ID (однозначно), путём или парой (тип, имя) — см. resolve_ref.
        """
        with self._lock:
            child = self.resolve_ref(child_id, child_path, child_type, child_name)
            parent = self.resolve_ref(parent_id, parent_path, parent_type, parent_name)
            data = self._load()
            self._guard_write(data)
            rec = data["entities"][child["id"]]
            if parent["id"] not in rec["parent_ids"]:
                rec["parent_ids"].append(parent["id"])
            self._save(data)
            return {"child": rec, "parent_id": parent["id"],
                    "parent_type": parent["type"], "parent_name": parent["name"]}

    # РЕСУРСНЫЕ границы (не защита): реестр — единый JSON, и чужой текст не должен его раздувать.
    # Защита от инъекций делается конвертом провенанса на выводе (core/contracts/untrusted.py, S3):
    # обрезать «ignore previous instructions» до 200 символов бессмысленно — оно короче.
    LABEL_MAX = 500
    TAG_MAX = 40
    TAGS_MAX = 10

    @classmethod
    def _clean(cls, text: str, limit: int) -> str:
        """Ресурсная нормализация: схлопнуть пробелы и ограничить длину. Смысл текста не трогаем."""
        flat = " ".join(str(text or "").split())
        return flat[:limit]

    def annotate(self, entity_id: str, label: str = "", tags: list | None = None,
                 source: str = "") -> dict:
        """Пометить сущность человеческим ярлыком и метками (индекс для поиска ID).

        Смысл: чтобы узнать нужный ID, ИИ спрашивает реестр, а не перечитывает переписку
        и файлы памяти. Данные попадают сюда ЯВНО — из чата или разбором памяти.
        """
        with self._lock:
            data = self._load()
            self._guard_write(data)
            rec = data["entities"].get(entity_id)
            if not rec:
                raise LinkError("ENTITY_NOT_FOUND", f"Сущности {entity_id} нет в реестре.",
                                "Проверь ID через structure_find или создай сущность.",
                                "structure_find")
            if label:
                rec["label"] = self._clean(label, self.LABEL_MAX)
            if tags:
                merged = list(dict.fromkeys(
                    (rec.get("tags") or []) + [self._clean(t, self.TAG_MAX) for t in tags if str(t).strip()]))
                rec["tags"] = merged[: self.TAGS_MAX]
            if source:
                rec["source"] = self._clean(source, self.TAG_MAX * 2)
            self._save(data)
            return rec

    def search(self, name: str = "", etype: str = "", tag: str = "", text: str = "",
               limit: int = 50) -> list[dict]:
        """Поиск сущности по человеческим признакам → её ID (дешёвый вход в адресацию).

        `text` ищет подстроку в имени, ярлыке, метках и пути — то, чем ИИ реально помнит
        сущность («интро про сетапы»), а не по ID, которого он ещё не знает.
        """
        needle = text.lower().strip()
        out = []
        for e in self._load()["entities"].values():
            if etype and e.get("type") != etype:
                continue
            if name and e.get("name") != name:
                continue
            if tag and tag not in (e.get("tags") or []):
                continue
            if needle:
                haystack = " ".join([
                    e.get("name", ""), e.get("label", ""), e.get("path", ""),
                    " ".join(e.get("tags") or [])]).lower()
                if needle not in haystack:
                    continue
            out.append(e)
            if len(out) >= limit:
                break
        return out

    def re_adopt(self) -> dict:
        """S9: присвоить существующий реестр этому инстансу (явное действие владельца).

        Нужен после законного переноса — смены железа, переезда VM, восстановления из бэкапа.
        Данные не меняются, меняется только подпись: чужой артефакт становится нашим осознанно,
        а не автоматически.
        """
        with self._lock:
            data = self._load()
            self._verdict = ""          # снимаем вердикт: владелец подтвердил присвоение
            self._save(data)
            return {"entities": len(data["entities"])}

    def all(self) -> list[dict]:
        """Все записи реестра (снимок). Для индексов на стороне читателей (поиск)."""
        return list(self._load()["entities"].values())

    def find_under(self, path: str) -> list[dict]:
        """Сущности, лежащие по этому пути или под ним (поддерево)."""
        want = self._norm(path)
        if not want:
            return []
        out = []
        for e in self._load()["entities"].values():
            p = self._norm(e.get("path", ""))
            if p == want or p.startswith(want + "/"):
                out.append(e)
        return out

    def migrate_subtree(self, old_path: str, new_path: str) -> list[dict]:
        """Перенос поддерева: путь сущности и всех её потомков переписывается (F65).

        Собственные ID неизменны — меняется только адрес (S18-g), поэтому ссылки не рвутся.
        """
        old, new = self._norm(old_path), self._norm(new_path)
        moved = []
        with self._lock:
            data = self._load()
            self._guard_write(data)
            for rec in data["entities"].values():
                p = self._norm(rec.get("path", ""))
                if p != old and not p.startswith(old + "/"):
                    continue
                rec["path"] = new + p[len(old):]
                moved.append({"id": rec["id"], "type": rec["type"], "old_path": p, "new_path": rec["path"]})
            if moved:
                self._save(data)
        return moved

    def forget_subtree(self, path: str) -> list[dict]:
        """Забыть сущности поддерева (удаление с диска). Возвращает снятые записи."""
        want = self._norm(path)
        dropped = []
        with self._lock:
            data = self._load()
            self._guard_write(data)
            for eid in list(data["entities"]):
                p = self._norm(data["entities"][eid].get("path", ""))
                if p == want or p.startswith(want + "/"):
                    dropped.append(data["entities"].pop(eid))
            if dropped:
                self._save(data)
        return dropped

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
            self._save(data)
            return {"id": entity_id, "old_path": old_path, "new_path": new_path,
                    "parent_ids": rec["parent_ids"]}
