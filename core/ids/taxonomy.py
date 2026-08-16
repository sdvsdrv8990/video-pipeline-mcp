"""
core/ids/taxonomy.py — Таксономия узлов workspace: префиксы, предки, контейнеры

## Назначение
Единственный источник ответов «какой префикс у типа», «какие предки объявлены и какие из них
обязательны», «что лежит в контейнере `videos/`». Читает блоки `id:` и `children:` из
`config/templates/workspace/*.tpl.yaml` — иерархия объявляется там же, где создаётся структура.

## Границы
- Ничего не генерирует и не пишет: только читает объявления (ID собирает ChainResolver/TemplateEngine).
- Шаблон без блока `id:` — ошибка `TEMPLATE_INVALID` (честный отказ, не молчаливый `type.upper()`).
"""

from pathlib import Path

import yaml


class TaxonomyError(Exception):
    """Ошибка объявления таксономии в формате контракта (маппится обёрткой в ErrorDetail)."""

    def __init__(self, code: str, message: str, reason: str = "", suggested_tool: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool


# Стратегии генерации собственного сегмента ID. `hex` = полный uuid4 (D9).
# Укороченный `scoped8` требует проверки уникальности в реестре и пока не подключён (G16).
SUPPORTED_STRATEGIES = {"hex"}

# Классы файлов (префиксы ID) объявлены рядом с шаблонами, но отдельным файлом:
# у файла нет шаблона папок, только личность.
FILE_CLASSES_FILE = "_file_classes.yaml"


class Taxonomy:
    """Объявленная иерархия типов узлов (из шаблонов workspace)."""

    def __init__(self, templates_dir):
        self.dir = Path(templates_dir)
        self._types: dict[str, dict] | None = None
        self._file_classes: dict[str, dict] | None = None

    def _load(self) -> dict[str, dict]:
        if self._types is not None:
            return self._types
        types: dict[str, dict] = {}
        for p in sorted(self.dir.glob("*.tpl.yaml")):
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            node_type = p.name[: -len(".tpl.yaml")]
            body = data.get(node_type)
            if not isinstance(body, dict):
                raise TaxonomyError(
                    "TEMPLATE_INVALID", f"В шаблоне {p.name} нет корневого ключа '{node_type}'.",
                    "Верхний ключ шаблона должен совпадать с именем типа.")
            id_block = body.get("id")
            if not isinstance(id_block, dict) or not id_block.get("prefix"):
                raise TaxonomyError(
                    "TEMPLATE_INVALID", f"В шаблоне {p.name} нет блока id: с prefix.",
                    "Префикс и предки объявляются в шаблоне — единственном источнике таксономии.")
            strategy = id_block.get("strategy", "hex")
            if strategy not in SUPPORTED_STRATEGIES:
                raise TaxonomyError(
                    "TEMPLATE_INVALID", f"Шаблон {p.name}: стратегия ID '{strategy}' не поддержана.",
                    f"Доступно: {', '.join(sorted(SUPPORTED_STRATEGIES))}.")
            types[node_type] = {
                "prefix": id_block["prefix"],
                "strategy": strategy,
                "root_container": str(body.get("root_container", "")).strip("/"),
                "ancestors": [
                    {"type": a["type"], "required": bool(a.get("required", False)), "role": a.get("role", "")}
                    for a in (id_block.get("ancestors") or [])
                ],
                "children": [
                    {"type": c.get("type", ""), "container": str(c.get("container", "")).strip("/")}
                    for c in (body.get("children") or [])
                ],
                # Папки, объявленные шаблоном (assets/svg, renders…): структура, а не сущности —
                # резолвер обязан их пропускать наравне с контейнерами детей.
                "folders": [str(f.get("name", "")).strip("/") for f in (body.get("folders") or []) if f.get("name")],
            }
        self._types = types
        return types

    @property
    def node_types(self) -> list[str]:
        return sorted(self._load())

    def _get(self, node_type: str) -> dict:
        t = self._load().get(node_type)
        if t is None:
            raise TaxonomyError(
                "TEMPLATE_NOT_FOUND", f"Тип узла не объявлен: {node_type}",
                f"Известные типы: {', '.join(self.node_types)}.")
        return t

    def prefix(self, node_type: str) -> str:
        return self._get(node_type)["prefix"]

    def strategy(self, node_type: str) -> str:
        return self._get(node_type)["strategy"]

    def ancestors(self, node_type: str) -> list[dict]:
        """Объявленные предки сверху вниз: [{type, required, role}]."""
        return list(self._get(node_type)["ancestors"])

    def required_parent_types(self, node_type: str) -> list[str]:
        return [a["type"] for a in self.ancestors(node_type) if a["required"]]

    def child_type_for(self, parent_type: str, container: str) -> str:
        """Тип ребёнка, который живёт в контейнере родителя (`videos/` → video)."""
        want = container.strip("/")
        for c in self._get(parent_type)["children"]:
            if c["container"] == want:
                return c["type"]
        return ""

    def child_matches(self, parent_type: str, segments: list[str]) -> list[tuple[str, int]]:
        """Все варианты «какой ребёнок начинается здесь и сколько сегментов занял контейнер».

        Токен `{parent:<тип>}` (`competitors/{parent:channel}/`) может быть подставлен, а может
        отсутствовать — когда наш канал неизвестен, сегмент опускается (§4). Поэтому вариантов
        бывает несколько, а выбирает из них вызывающий — по тому, какой разбор уходит глубже.
        """
        out = []
        for c in self._get(parent_type)["children"]:
            parts = [p for p in c["container"].split("/") if p]
            literal = [p for p in parts if not (p.startswith("{parent:") and p.endswith("}"))]
            for variant in ({tuple(parts), tuple(literal)} if len(literal) != len(parts) else {tuple(parts)}):
                if not variant or len(variant) > len(segments):
                    continue
                if all((p.startswith("{parent:") and p.endswith("}")) or p == segments[i]
                       for i, p in enumerate(variant)):
                    out.append((c["type"], len(variant)))
        return out

    def _load_file_classes(self) -> dict[str, dict]:
        if self._file_classes is not None:
            return self._file_classes
        p = self.dir / FILE_CLASSES_FILE
        if not p.exists():
            raise TaxonomyError(
                "TEMPLATE_INVALID", f"Нет объявления классов файлов: {FILE_CLASSES_FILE}",
                "Префиксы файлов объявляются декларативно, в коде их нет.")
        data = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("file_classes")
        if not isinstance(data, dict) or not data:
            raise TaxonomyError(
                "TEMPLATE_INVALID", f"{FILE_CLASSES_FILE}: пустой или неверный ключ file_classes.",
                "Ожидается {класс: {prefix, extensions}}.")
        self._file_classes = {
            name: {"prefix": body["prefix"],
                   "extensions": [str(e).lower() for e in (body.get("extensions") or [])]}
            for name, body in data.items()}
        return self._file_classes

    def file_class(self, path: str) -> str:
        """Класс файла по расширению (`.xlsx` → table). Незаявленное расширение → дефолтный класс."""
        suffix = Path(path).suffix.lower()
        classes = self._load_file_classes()
        for name, body in classes.items():
            if suffix and suffix in body["extensions"]:
                return name
        return next((n for n, b in classes.items() if not b["extensions"]), "file")

    def file_prefix(self, path: str) -> str:
        """Префикс ID для файла (из объявления класса)."""
        return self._load_file_classes()[self.file_class(path)]["prefix"]

    def type_for_root_container(self, container: str) -> str:
        """Обратно: какой тип живёт в корневом контейнере (`niches` → niche)."""
        want = (container or "").strip("/")
        for t, body in self._load().items():
            if body["root_container"] and body["root_container"] == want:
                return t
        return ""

    def root_container(self, node_type: str) -> str:
        """Контейнер для типа без родителя-шаблона (ниша → `niches/`). Пусто, если родитель есть."""
        return self._get(node_type)["root_container"]

    @property
    def containers(self) -> set[str]:
        """Имена контейнер-каталогов из всех шаблонов (`niches`, `channels`, `videos`, `competitors`…).

        Резолвер пропускает их при обходе вверх: контейнер — не сущность.
        """
        out: set[str] = set()
        for t in self._load().values():
            for c in t["children"]:
                if c["container"]:
                    out.update(Path(c["container"]).parts)
            if t["root_container"]:
                out.update(Path(t["root_container"]).parts)
            for f in t["folders"]:
                out.update(Path(f).parts)
        # Токен `{parent:<тип>}` — не имя каталога, а место для имени предка: наружу
        # он утекать не должен (иначе читатель сверяет имена с литералом '{parent:channel}').
        return {c for c in out if not (c.startswith("{parent:") and c.endswith("}"))}

    def children_of(self, parent_type: str) -> list[dict]:
        """Объявленные дети: [{type, container}] — контейнер как в шаблоне, вместе с токеном."""
        return list(self._get(parent_type)["children"])

    def descendant_containers(self, parent_type: str) -> list[dict]:
        """Где под узлом этого типа МОГУТ лежать сущности: [{type, container}].

        Шире прямых детей: уровень иерархии разрешено пропускать (канал прямо под нишей),
        и тогда потомок лежит в СВОЁМ контейнере, объявленном его типом-родителем.
        """
        by_child: dict[str, str] = {}
        for body in self._load().values():
            for c in body["children"]:
                by_child.setdefault(c["type"], c["container"])
        out = []
        for node_type in self.node_types:
            if node_type == parent_type or node_type not in by_child:
                continue
            if parent_type in (a["type"] for a in self.ancestors(node_type)):
                out.append({"type": node_type, "container": by_child[node_type]})
        return out
