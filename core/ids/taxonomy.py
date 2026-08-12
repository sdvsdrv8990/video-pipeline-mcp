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


class Taxonomy:
    """Объявленная иерархия типов узлов (из шаблонов workspace)."""

    def __init__(self, templates_dir):
        self.dir = Path(templates_dir)
        self._types: dict[str, dict] | None = None

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
        return out
