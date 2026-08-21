"""
core/engine/template_engine.py — Движок шаблонов структуры (композиция по ссылке + контроль глубины)

## Назначение
Материализует дерево рабочей области по шаблонам `config/templates/workspace/*.tpl.yaml`:
родитель знает только ИМЯ типа ребёнка и его контейнер, устройство ребёнка не разворачивает.

## Границы
- Спуск только в НАЗВАННЫХ детей; остальные объявленные — в `deferred_children`.
- `kind: table` здесь не строится (уходит в `tables_pending`); `kind: config` — КОПИЯ
  серверного дефолта в узел: правится копия, git-декларация неизменна.
- Скип пофрагментный, без отката: пропущенное несёт причину, соседи создаются.
- Containment — `safe_resolve`, запись — `write_policy`: шаблон объявляет, ЧТО писать, но не что МОЖНО.
- Движок generic (без `if type == ...`); связывание/ORPHAN — не здесь, это `core/ids`.
"""

import re
from pathlib import Path

import yaml

from core.ids.taxonomy import Taxonomy
from core.paths import safe_resolve
from core.write_policy import WritePolicy, WritePolicyError
from core.contracts import ContractError

_TOKEN_RE = re.compile(r"\{parent:([^}]+)\}")


class TemplateError(ContractError):
    """Ошибка движка шаблонов в формате контракта (маппится обёрткой в ErrorDetail)."""


class TemplateEngine:
    """Материализатор структуры по шаблонам с контролем глубины.

    Attributes:
        ws: путь к workspace/ (containment-корень)
        ids: генератор ID (сервер присваивает узлам)
        tpl_dir: config/templates/workspace/
    """

    def __init__(self, workspace_path, id_generator, templates_dir, config_dir=None):
        self.ws = Path(workspace_path)
        self.ids = id_generator
        self.tpl_dir = Path(templates_dir)
        # Корень серверных деклараций — источник копий для `kind: config`.
        self.config_dir = Path(config_dir) if config_dir else self.tpl_dir.parents[1]
        # Шаблон — тоже пишущий путь, значит через ту же дверь, что и fs_*.
        self.policy = WritePolicy(self.config_dir)
        self.taxonomy = Taxonomy(templates_dir)   # префиксы и контейнеры — из шаблонов, не из кода
        self._cache: dict[str, dict] = {}

    def _load(self, node_type: str) -> dict:
        """Загрузка тела шаблона по типу (с кэшем). Верхний ключ yaml == тип."""
        if node_type in self._cache:
            return self._cache[node_type]
        p = self.tpl_dir / f"{node_type}.tpl.yaml"
        if not p.exists():
            raise TemplateError(
                "TEMPLATE_NOT_FOUND", f"Шаблон не найден: {node_type}",
                "Проверь имя типа в config/templates/workspace/*.tpl.yaml.")
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        body = data.get(node_type)
        if not isinstance(body, dict):
            raise TemplateError(
                "TEMPLATE_NOT_FOUND", f"В шаблоне {p.name} нет корневого ключа '{node_type}'.",
                "Верхний ключ шаблона должен совпадать с именем типа.")
        self._cache[node_type] = body
        return body

    def _forbidden(self, name: str, content) -> str:
        """Причина отказа по allowlist записи (пусто = можно): тип файла И его содержимое."""
        try:
            self.policy.check(name)
            self.policy.check_content(name, content)
        except WritePolicyError as e:
            return e.message
        return ""

    @staticmethod
    def _valid_name(name: str) -> bool:
        """Имя узла — один сегмент: без '/', без пустоты и краевых пробелов."""
        return bool(name) and ("/" not in name) and name.strip() == name

    @staticmethod
    def fill_container(container: str, ancestors: dict) -> tuple[str, list]:
        """Подставить `{parent:<тип>}` из имён предков (§4: конкурент лежит под НАШИМ каналом).

        Неизвестный предок → сегмент опускается (не заглушка), его тип возвращается вторым
        значением, чтобы вызывающий знал: группировка не состоялась.
        """
        missing = []
        out = []
        for seg in container.split("/"):
            if seg.startswith("{parent:") and seg.endswith("}"):
                want = seg[len("{parent:"):-1]
                value = ancestors.get(want, "")
                if value:
                    out.append(value)
                else:
                    missing.append(want)
                continue
            if seg:
                out.append(seg)
        return ("/".join(out) + "/" if out else ""), missing

    def address_for(self, node_type: str, parent_type: str, parent_path: str,
                    ancestors: dict) -> tuple[str, list]:
        """Где узел ОБЯЗАН лежать под этим родителем: (каталог, типы неизвестных предков).

        Одно правило §4 на всех: создание, связывание и reconcile спрашивают адрес здесь,
        а не считают его каждый по-своему.
        """
        container, missing = self.fill_container(
            self.taxonomy.child_container(parent_type, node_type), ancestors)
        base = parent_path.strip("/")
        return (f"{base}/{container}" if base else container), missing

    def create_node(self, node_type: str, name: str, parent_path: str = "",
                    parent_ids: list | None = None, children: dict | None = None,
                    ancestors: dict | None = None) -> dict:
        """Материализовать узел (+ явно названных детей) с контролем глубины.

        `parent_path` — контейнер относительно workspace; пусто → корневой контейнер типа.
        `children` = {тип: [имена]}: названные разворачиваются, остальные отложены.
        Возвращает дерево (node_id, path, created/skipped/tables_pending/deferred_children,
        children рекурсивно); TemplateError и ValueError маппятся обёрткой в ErrorDetail.
        """
        body = self._load(node_type)
        if not self._valid_name(name):
            raise TemplateError(
                "VALIDATION_ERROR", f"Некорректное имя узла: {name!r}",
                "Имя — один сегмент: без '/', без краевых пробелов.")

        root = self.taxonomy.root_container(node_type)
        base = parent_path if parent_path else (f"{root}/" if root else "")
        # parent_path — КОНТЕЙНЕР: без разделителя имя приклеится к последнему сегменту
        # ("niches/g/networks" + "n1" → "networksn1" рядом с контейнером, а не внутри).
        if base and not base.endswith("/"):
            base += "/"
        node_rel = f"{base}{name}".strip("/")
        # Жёсткая проверка пути узла (нарушение → ValueError → PATH_ESCAPE у обёртки).
        node_dir = safe_resolve(node_rel, self.ws)
        node_dir.mkdir(parents=True, exist_ok=True)

        node_id = self.ids.generate_simple(self.taxonomy.prefix(node_type))

        created: list[dict] = []
        skipped: list[dict] = []
        tables_pending: list[dict] = []

        # --- folders ---
        for fr in (body.get("folders") or []):
            fname = fr.get("name", "")
            if not fname:
                skipped.append({"kind": "folder", "reason": "no name"})
                continue
            try:
                d = safe_resolve(f"{node_rel}/{fname}", self.ws)
            except ValueError:
                skipped.append({"kind": "folder", "name": fname, "reason": "path escape"})
                continue
            d.mkdir(parents=True, exist_ok=True)
            created.append({"kind": "folder", "path": f"{node_rel}/{fname}"})

        # --- files (таблицы отложены в фазу таблиц) ---
        for fr in (body.get("files") or []):
            fname = fr.get("name", "")
            if not fname:
                skipped.append({"kind": "file", "reason": "no name"})
                continue
            if fr.get("kind") == "table":
                # Таблицы отложены, но присваиваем ID для будущего создания
                file_id = self.ids.generate_simple("FILE")
                tables_pending.append({
                    "path": f"{node_rel}/{fname}",
                    "table_template": fr.get("table_template"),
                    "required": fr.get("required", False),
                    "file_id": file_id,
                })
                continue
            try:
                f = safe_resolve(f"{node_rel}/{fname}", self.ws)
            except ValueError:
                skipped.append({"kind": "file", "name": fname, "reason": "path escape"})
                continue
            if fr.get("kind") == "config":
                # Per-project override: копия серверного дефолта в данные проекта (doc 10 §5.0).
                rel_src = str(fr.get("source", fname))
                try:
                    # Источник — только внутри config/, иначе шаблон вычерпает .env наружу.
                    src = safe_resolve(rel_src, self.config_dir)
                except ValueError:
                    skipped.append({"kind": "config", "name": fname, "reason": "source escape",
                                    "source": rel_src})
                    continue
                if not src.is_file():
                    skipped.append({"kind": "config", "name": fname, "reason": "no default",
                                    "source": rel_src})
                    continue
                text = src.read_text(encoding="utf-8")
                denied = self._forbidden(fname, text)
                if denied:
                    skipped.append({"kind": "config", "name": fname, "reason": "forbidden",
                                    "detail": denied})
                    continue
                f.parent.mkdir(parents=True, exist_ok=True)
                if not f.exists():   # уже правленную копию не затираем
                    f.write_text(text, encoding="utf-8")
                created.append({"kind": "config", "path": f"{node_rel}/{fname}",
                                "source": rel_src})
                continue
            content = fr.get("content", "")
            denied = self._forbidden(fname, content)
            if denied:
                skipped.append({"kind": "file", "name": fname, "reason": "forbidden", "detail": denied})
                continue
            f.parent.mkdir(parents=True, exist_ok=True)
            if not f.exists():
                f.write_text(content, encoding="utf-8")
            created.append({"kind": "file", "path": f"{node_rel}/{fname}"})

        result = {
            "node_id": node_id,
            "type": node_type,
            "name": name,
            "path": node_rel,
            "parent_path": base,
            "parent_ids": list(parent_ids or []),
            "created": created,
            "skipped": skipped,
            "tables_pending": tables_pending,
            "deferred_children": [],
            "children": [],
        }

        # --- children: контроль глубины (только названные) ---
        named = children or {}
        # Имена предков для подстановки {parent:<тип>}: то, что пришло сверху, + сам узел.
        known = dict(ancestors or {})
        known[node_type] = name
        # Дети текущего вызова тоже могут быть якорем группировки (наш канал для конкурента),
        # но только пока имя однозначно: два канала в одном вызове — группировать не по чему.
        for cref in (body.get("children") or []):
            ctype = cref.get("type")
            sibling_names = named.get(ctype) or []
            if len(sibling_names) == 1 and ctype not in known:
                known[ctype] = sibling_names[0]
        for cref in (body.get("children") or []):
            ctype = cref.get("type")
            container, missing = self.fill_container(cref.get("container", ""), known)
            child_parent = f"{node_rel}/{container}"
            names = named.get(ctype) or []
            if not names:
                entry = {"type": ctype, "container": child_parent}
                if missing:
                    entry["ungrouped_by"] = missing
                result["deferred_children"].append(entry)
                continue
            # Чьим именем сгруппирован путь — то же имя обязано стать связью, иначе ФС и реестр
            # рассказывают о паре разное: каталог внутри канала, а сущность «висит без канала».
            grouped = {t: known[t] for t in _TOKEN_RE.findall(cref.get("container", ""))
                       if known.get(t)}
            for cname in names:
                sub = self.create_node(
                    ctype, cname, parent_path=child_parent,
                    parent_ids=(parent_ids or []) + [node_id], children=named, ancestors=known)
                if missing:
                    sub["ungrouped_by"] = missing
                if grouped:
                    sub["grouped_by"] = grouped
                result["children"].append(sub)

        return result
