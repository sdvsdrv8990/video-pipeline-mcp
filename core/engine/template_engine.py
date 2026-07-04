"""
core/engine/template_engine.py — Движок шаблонов структуры (композиция по ссылке + контроль глубины)

## Назначение
Материализует дерево рабочего пространства (ниша → сетка → канал → видео + конкуренты)
по декларативным шаблонам `config/templates/workspace/*.tpl.yaml`.

## Ключевые законы (ИНСТРУКЦИЯ_шаблоны.md)
- **Композиция по ссылке (§1).** Родитель знает только ИМЯ типа ребёнка и его контейнер
  (`children: [{type: video, container: videos/}]`), НЕ разворачивает его устройство.
- **Контроль глубины (суть задачи).** Материализуется только СВОИ folders+files узла
  (+ контейнеры детей). В `children` спускаемся ТОЛЬКО для явно НАЗВАННЫХ детей;
  остальные — отложены (`ChildDeferred`). «Создать канал кроме видео» = не называть видео;
  «назвать видео → все его дети создаются» = передать имя видео в children.
- **ID от сервера (§1).** Каждый узел получает ID (в facts), имя — на входе.
- **Пофрагментный скип (§1).** Нет валидного имени фрагмента → фрагмент пропущен,
  соседи создаются, не атомарный откат.
- **Таблицы отложены (Ф3, G16).** Фрагменты `kind: table` здесь НЕ строятся — они
  честно уходят в `tables_pending` (фаза таблиц строит .xlsx отдельно). Структура на
  уровне папок/json работает сразу.

## Границы
- Containment: все пути через `core.paths.safe_resolve` (ValueError → PATH_ESCAPE у обёртки, D29/G17).
- Движок generic: поведение задаётся шаблоном, без `if type == ...`.
- Связывание/ORPHAN — не здесь (Ф2, core/ids реестр связей). Здесь только materialize + node_id.
"""

from pathlib import Path

import yaml

from core.paths import safe_resolve


# Префикс ID по типу узла (сервер присваивает, D9/D28).
TYPE_PREFIX = {
    "niche": "NICHE",
    "network": "NET",
    "channel": "CH",
    "video": "VID",
    "competitor_channel": "COMP",
    "competitor_video": "CVID",
}

# Корневой контейнер для типов без родителя-шаблона (ниша живёт в niches/).
ROOT_CONTAINER = {"niche": "niches/"}


class TemplateError(Exception):
    """Ошибка движка шаблонов в формате контракта (маппится обёрткой в ErrorDetail)."""

    def __init__(self, code: str, message: str, reason: str = "", suggested_tool: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool


class TemplateEngine:
    """Материализатор структуры по шаблонам с контролем глубины.

    Attributes:
        ws: путь к workspace/ (containment-корень)
        ids: генератор ID (сервер присваивает узлам)
        tpl_dir: config/templates/workspace/
    """

    def __init__(self, workspace_path, id_generator, templates_dir):
        self.ws = Path(workspace_path)
        self.ids = id_generator
        self.tpl_dir = Path(templates_dir)
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

    @staticmethod
    def _valid_name(name: str) -> bool:
        """Имя узла — один сегмент: без '/', без пустоты и краевых пробелов."""
        return bool(name) and ("/" not in name) and name.strip() == name

    def create_node(self, node_type: str, name: str, parent_path: str = "",
                    parent_ids: list | None = None, children: dict | None = None) -> dict:
        """Материализовать узел (+ явно названных детей) с контролем глубины.

        Args:
            node_type: тип узла (ключ шаблона)
            name: имя экземпляра (один сегмент пути)
            parent_path: контейнер-путь, куда кладётся папка узла (относительно workspace).
                Пусто → берётся ROOT_CONTAINER (для niche = "niches/").
            parent_ids: ID родителей (для реестра связей, Ф2) — прокидывается в результат.
            children: {child_type: [имена]} — какие дети развернуть. Не названные — отложены.

        Returns:
            dict-дерево результата: node_id, path, created[], skipped[], tables_pending[],
            deferred_children[], children[] (рекурсивно).

        Raises:
            TemplateError / ValueError (path escape) — маппятся обёрткой в ErrorDetail.
        """
        body = self._load(node_type)
        if not self._valid_name(name):
            raise TemplateError(
                "VALIDATION_ERROR", f"Некорректное имя узла: {name!r}",
                "Имя — один сегмент: без '/', без краевых пробелов.")

        base = parent_path if parent_path else ROOT_CONTAINER.get(node_type, "")
        node_rel = f"{base}{name}".strip("/")
        # Жёсткая проверка пути узла (нарушение → ValueError → PATH_ESCAPE у обёртки).
        node_dir = safe_resolve(node_rel, self.ws)
        node_dir.mkdir(parents=True, exist_ok=True)

        node_id = self.ids.generate_simple(TYPE_PREFIX.get(node_type, node_type.upper()))

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

        # --- files (таблицы отложены в фазу таблиц, Ф3/G16) ---
        for fr in (body.get("files") or []):
            fname = fr.get("name", "")
            if not fname:
                skipped.append({"kind": "file", "reason": "no name"})
                continue
            if fr.get("kind") == "table":
                tables_pending.append({
                    "path": f"{node_rel}/{fname}",
                    "table_template": fr.get("table_template"),
                    "required": fr.get("required", False),
                })
                continue
            try:
                f = safe_resolve(f"{node_rel}/{fname}", self.ws)
            except ValueError:
                skipped.append({"kind": "file", "name": fname, "reason": "path escape"})
                continue
            f.parent.mkdir(parents=True, exist_ok=True)
            if not f.exists():
                f.write_text(fr.get("content", ""), encoding="utf-8")
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
        for cref in (body.get("children") or []):
            ctype = cref.get("type")
            container = cref.get("container", "")
            child_parent = f"{node_rel}/{container}"
            names = named.get(ctype) or []
            if not names:
                result["deferred_children"].append({"type": ctype, "container": child_parent})
                continue
            for cname in names:
                sub = self.create_node(
                    ctype, cname, parent_path=child_parent,
                    parent_ids=(parent_ids or []) + [node_id], children=named)
                result["children"].append(sub)

        return result
