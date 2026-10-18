"""core/engine/template_engine.py — материализация дерева рабочей области по шаблонам.

Родитель знает только ИМЯ типа ребёнка и его контейнер, устройства ребёнка не разворачивает:
спуск идёт лишь в НАЗВАННЫХ детей, прочие объявленные уезжают в `deferred_children`.
`kind: table` здесь не строится, `kind: config` кладётся КОПИЕЙ серверного умолчания — правится
копия, декларация в git неизменна. Пропуск пофрагментный и без отката: пропущенное несёт
причину, соседи создаются. Шаблон объявляет, ЧТО писать, но не что МОЖНО: границу держит
политика записи, а связывание и разбор сирот живут не здесь.
"""

import re
from pathlib import Path

import yaml

from core.ids.taxonomy import Taxonomy
from core.paths import Files, PathRefused
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
        if not p.exists():  # типы таксономии — те же файлы шаблонов: файла нет — тип назван неизвестный
            raise TemplateError("TEMPLATE_NOT_FOUND", facts={"template": node_type, "known": self.taxonomy.node_types})
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        body = data.get(node_type)
        if not isinstance(body, dict):
            raise TemplateError("DECLARATION_INVALID",
                                facts={"file": p.name, "details": f"нет корневого ключа «{node_type}»"})
        self._cache[node_type] = body
        return body

    def _create(self, files: Files, path: Path, data, skip: dict, skipped: list) -> bool:
        """Файл узла через дверь с политикой; не создан — строка `skipped` {reason: код, name, …факты}, а не «создан»
        (занятый — FILE_EXISTS: правку проекта не затираем)."""
        try:
            if files.create(path, data, policy=self.policy):
                return True
            skipped.append({**skip, "reason": "FILE_EXISTS"})
        except WritePolicyError as e:
            skipped.append({**skip, "reason": e.code, **e.facts})
        return False

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
        children рекурсивно); TemplateError и отказ двери пути (PathRefused) клиент получает своим кодом.
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
        files = Files(self.ws)
        node_dir = files.place(node_rel)  # отказ двери уходит клиенту своим кодом
        node_dir.mkdir(parents=True, exist_ok=True)

        node_id = self.ids.generate_simple(self.taxonomy.prefix(node_type))

        created: list[dict] = []
        skipped: list[dict] = []
        tables_pending: list[dict] = []

        for fr in (body.get("folders") or []):
            fname = fr.get("name", "")
            if not fname:
                skipped.append({"kind": "folder", "reason": "no name"})
                continue
            try:
                d = files.place(f"{node_rel}/{fname}")
            except PathRefused:
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
                f = files.place(f"{node_rel}/{fname}")
            except PathRefused:
                skipped.append({"kind": "file", "name": fname, "reason": "path escape"})
                continue
            if fr.get("kind") == "config":
                # Per-project override: копия серверного дефолта в данные проекта (doc 10 §5.0).
                rel_src = str(fr.get("source", fname))
                try:
                    # Источник — только внутри config/, иначе шаблон вычерпает .env наружу.
                    src = Files(self.config_dir).place(rel_src)
                except PathRefused:
                    skipped.append({"kind": "config", "name": fname, "reason": "source escape",
                                    "source": rel_src})
                    continue
                if not src.is_file():
                    skipped.append({"kind": "config", "name": fname, "reason": "no default",
                                    "source": rel_src})
                    continue
                text = src.read_text(encoding="utf-8")
                if self._create(files, f, text, {"kind": "config", "name": fname, "source": rel_src}, skipped):
                    created.append({"kind": "config", "path": f"{node_rel}/{fname}", "source": rel_src})
                continue
            if self._create(files, f, fr.get("content", ""), {"kind": "file", "name": fname}, skipped):
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
