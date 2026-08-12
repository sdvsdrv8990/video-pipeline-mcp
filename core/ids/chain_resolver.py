"""
core/ids/chain_resolver.py — Разрешение цепочки предков по каталогу назначения (S18-h)

## Назначение
Отвечает на вопрос «что за сущность здесь появится и чьи готовые ID она унаследует»: обходит путь
вверх до workspace, берёт зарегистрированных предков КАК ЕСТЬ и сообщает, каких объявленных уровней
нет. Один вход обслуживает и создание, и перенос — цепочка выводится из каталога, а не передаётся
параметром (F63).

## Границы
- Ничего не пишет: только читает реестр, диск и таксономию (регистрирует вызывающий).
- Никогда не перегенерирует ID существующего каталога — второй ID на занятый путь = раздвоение личности.
- Каталог на диске без записи в реестре не выдумывается, а возвращается в `unresolved`.
- Containment: путь проверяется `core.paths.safe_resolve` (нарушение → PathEscapeError → PATH_ESCAPE).
"""

from pathlib import Path, PurePosixPath

from core.paths import safe_resolve


class ChainResolver:
    """Резолвер цепочки: каталог → готовые предки + место нового сегмента."""

    def __init__(self, workspace_path, registry, taxonomy):
        self.ws = Path(workspace_path)
        self.registry = registry
        self.taxonomy = taxonomy

    @staticmethod
    def _norm(path: str) -> str:
        p = (path or "").replace("\\", "/").strip()
        while p.startswith("./"):
            p = p[2:]
        return p.strip("/")

    def _self_and_ancestors(self, rel: str) -> list[str]:
        """Сам путь и все его предки, сверху вниз. Пустой путь (корень workspace) — пустой список."""
        parts = PurePosixPath(rel).parts if rel else ()
        return ["/".join(parts[: i + 1]) for i in range(len(parts))]

    def _infer_type(self, chain: list[dict], parent_rel: str) -> str:
        """Тип нового узла — из объявления родителя (`children[].container`), не из карты путей."""
        if not chain:
            # Нет зарегистрированных предков — узел ложится в корневой контейнер (niches/ → niche).
            return self.taxonomy.type_for_root_container(PurePosixPath(parent_rel).name if parent_rel else "")
        nearest = chain[-1]
        container = self._norm(parent_rel[len(self._norm(nearest["path"])):]) if parent_rel else ""
        return self.taxonomy.child_type_for(nearest["type"], container)

    def infer_type_at(self, path: str) -> str:
        """Тип сущности, которая ЛЕЖИТ по этому пути (для усыновления незарегистрированных каталогов)."""
        rel = self._norm(path)
        parent = str(PurePosixPath(rel).parent) if "/" in rel else ""
        return self.resolve("" if parent == "." else parent)["node_type"]

    def resolve(self, target_dir: str, node_type: str = "") -> dict:
        """Разрешить цепочку для каталога назначения.

        Args:
            target_dir: каталог, В КОТОРОМ появится узел (относительно workspace)
            node_type: тип нового узла; пусто → выводится из объявления родителя

        Returns:
            {target, node_type, chain, chain_id, owner_id, skipped, missing_required, unresolved}

        Raises:
            PathEscapeError: путь вне workspace (маппится обёрткой в PATH_ESCAPE)
        """
        rel = self._norm(target_dir)
        safe_resolve(rel, self.ws)  # containment-чек, результат не нужен

        chain: list[dict] = []
        unresolved: list[dict] = []
        containers = self.taxonomy.containers
        for anc in self._self_and_ancestors(rel):
            name = PurePosixPath(anc).name
            if name in containers:
                continue  # контейнер — не сущность
            rec = self.registry.find_by_path(anc)
            if rec:
                chain.append(rec)
            else:
                unresolved.append({"path": anc, "exists": (self.ws / anc).exists()})

        inferred = node_type or self._infer_type(chain, rel)
        skipped: list[str] = []
        missing_required: list[str] = []
        if inferred:
            present = {e["type"] for e in chain}
            for a in self.taxonomy.ancestors(inferred):
                if a["type"] in present:
                    continue
                (missing_required if a["required"] else skipped).append(a["type"])

        return {
            "target": rel,
            "node_type": inferred,
            "chain": [{"id": e["id"], "type": e["type"], "name": e["name"], "path": e["path"]} for e in chain],
            "chain_id": "/".join(e["id"] for e in chain),
            "owner_id": chain[-1]["id"] if chain else "",
            "parent_ids": [e["id"] for e in chain],
            "skipped": skipped,
            "missing_required": missing_required,
            "unresolved": unresolved,
            "prefix": self.taxonomy.prefix(inferred) if inferred else "",
        }

    def qualified_id(self, chain_id: str, own_id: str) -> str:
        """Квалифицированный адрес (S18-g): цепочка предков + собственный сегмент."""
        return f"{chain_id}/{own_id}" if chain_id else own_id

    def chain_for_entity(self, entity_id: str) -> dict:
        """Цепочка УЖЕ существующей сущности — по её пути (вычисляема, не хранится)."""
        rec = self.registry.get(entity_id)
        if not rec:
            return {"chain_id": "", "chain": [], "qualified_id": ""}
        res = self.resolve(rec["path"], node_type=rec["type"])
        # Сама сущность попала в chain последней — отделяем её от предков.
        ancestors = [e for e in res["chain"] if e["id"] != entity_id]
        chain_id = "/".join(e["id"] for e in ancestors)
        return {
            "chain": ancestors,
            "chain_id": chain_id,
            "qualified_id": self.qualified_id(chain_id, entity_id),
            "skipped": res["skipped"],
            "missing_required": res["missing_required"],
        }
