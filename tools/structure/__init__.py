"""
tools/structure — материализация структуры workspace по шаблонам (Ф1) + реестр связей (Ф2).

Тонкие обёртки: композицию по ссылке и контроль глубины делает core/engine/template_engine,
связи — core/ids/LinkRegistry; здесь только упаковка результата в ToolResult + facts.
Контракт зафиксирован эталоном tests/quick/tools_inventory.golden.json.
"""

from core.contracts import Fact, ToolResult
from core.engine import Engine, TableMaterializer
from core.ids import LinkError
from tools._context import ANNOTATIONS_MODIFY, ANNOTATIONS_READONLY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы structure в движке."""

    materializer = TableMaterializer(ctx.excel_engine, ctx.config_path / "templates" / "tables")

    def _adopt(paths: list[str]) -> list[dict]:
        """Усыновить каталоги, которые есть на диске, но не в реестре (структура опередила реестр).

        Явный шаг: ID существующему каталогу присваивается только по запросу (adopt=true),
        молча сервер этого не делает — иначе теряется различие «создано» / «подхвачено».
        """
        out = []
        for p in sorted(paths, key=lambda s: s.count("/")):   # сверху вниз: родитель раньше ребёнка
            node_type = ctx.chain_resolver.infer_type_at(p)
            if not node_type:
                raise LinkError("CHAIN_UNRESOLVED", f"Не удалось определить тип каталога: {p}",
                                "Каталог вне объявленной структуры — создай узел явно через structure_create.",
                                "structure_resolve")
            parent_ids = ctx.chain_resolver.resolve(p)["parent_ids"]
            eid = ctx.id_generator.generate_simple(ctx.taxonomy.prefix(node_type))
            rec = ctx.link_registry.register({
                "id": eid, "type": node_type, "name": p.rsplit("/", 1)[-1],
                "path": p, "parent_ids": parent_ids, "kind": "node"})
            out.append({"id": rec["id"], "type": rec["type"], "name": rec["name"], "path": rec["path"]})
        return out

    def _entity_block(node_id: str) -> dict:
        """Единый пост-контракт создания (S18-g): имя, адрес, ID и цепочка владельцев."""
        rec = ctx.link_registry.get(node_id) or {}
        ch = ctx.chain_resolver.chain_for_entity(node_id)
        return {"id": node_id, "type": rec.get("type", ""), "name": rec.get("name", ""),
                "path": rec.get("path", ""), "chain": ch["chain_id"],
                "qualified_id": ch["qualified_id"], "owner_id": ch["chain"][-1]["id"] if ch["chain"] else ""}

    # ─── Структура: шаблонное создание (Ф1) ───

    async def structure_create(type: str, name: str, parent_path: str = "",
                               children: dict | None = None, adopt: bool = False) -> "ToolResult":
        """Материализация узла структуры по шаблону с контролем глубины.

        Создаёт СВОИ папки/файлы узла + контейнеры детей; в детей спускается ТОЛЬКО
        для явно названных (children={тип:[имена]}). Книги (kind:table) созданных сущностей
        материализуются здесь же — клиент передаёт только имена. ID узла присваивает сервер,
        предков берёт готовыми из каталога назначения (S18-h).
        """
        # S18-h: цепочка предков выводится ИЗ КАТАЛОГА, а не передаётся вызывающим (F63).
        ok, chain = ctx.safe(lambda: ctx.chain_resolver.resolve(parent_path, node_type=type))
        if not ok:
            return chain
        blocking = [u["path"] for u in chain["unresolved"] if u["exists"]]
        if blocking and not adopt:
            return ctx.err("CHAIN_UNRESOLVED",
                           f"Каталоги-предки существуют, но не зарегистрированы: {', '.join(blocking)}")

        adopted: list[dict] = []
        if blocking:
            ok, adopted = ctx.safe(lambda: _adopt(blocking))
            if not ok:
                return adopted
            ok, chain = ctx.safe(lambda: ctx.chain_resolver.resolve(parent_path, node_type=type))
            if not ok:
                return chain

        ok, res = ctx.safe(lambda: ctx.template_engine.create_node(
            type, name, parent_path, chain["parent_ids"], children))
        if not ok:
            return res

        facts: list = []
        created_ids: list[str] = []
        for a in adopted:
            facts.append(Fact(type="EntityAdopted", data=a))

        def _walk(node: dict) -> None:
            # Ф2: регистрируем узел в реестре связей (для ORPHAN/link).
            ctx.link_registry.register({
                "id": node["node_id"], "type": node["type"], "name": node["name"],
                "path": node["path"], "parent_ids": node["parent_ids"], "kind": "node"})
            created_ids.append(node["node_id"])
            facts.append(Fact(type="NodeCreated", data={
                "id": node["node_id"], "type": node["type"], "name": node["name"],
                "path": node["path"], "parent_ids": node["parent_ids"]}))
            for c in node["created"]:
                facts.append(Fact(
                    type="FolderCreated" if c["kind"] == "folder" else "FileCreated",
                    data={"path": c["path"]}))
            for t in node["tables_pending"]:
                # Регистрируем отложенную таблицу с ID в реестре
                if "file_id" in t:
                    ctx.link_registry.register({
                        "id": t["file_id"], "type": "table_file", "name": t["path"].split("/")[-1],
                        "path": t["path"], "parent_ids": [node["node_id"]], "kind": "file"})
                facts.append(Fact(type="TableDeferred", data=t))
            for d in node["deferred_children"]:
                facts.append(Fact(type="ChildDeferred", data=d))
            for sub in node["children"]:
                _walk(sub)

        _walk(res)

        # Фаза ТАБЛИЦЫ идёт сразу и целиком на сервере (решение владельца S17): клиент передаёт
        # ИМЕНА, книги материализует движок. Создаются книги ТОЛЬКО созданных сущностей — тот же
        # принцип, что у файловой структуры: канал создан ≠ видео-проект существует, книг видео нет.
        pending = [f.data for f in facts if f.type == "TableDeferred"]
        phase = materializer.materialize_pending(pending)
        for m in phase["materialized"]:
            facts.append(Fact(type="TableMaterialized", data={
                "path": m["path"], "book": m["book"], "file_id": m.get("file_id", ""),
                "sheets": [s["sheet"] for s in m["sheets"]], "columns": m["columns_total"]}))
        res["tables_materialized"] = phase["materialized"]
        # Книги без заведённой декларации остаются честно отложенными (G16), а не «успешно созданными».
        res["tables_deferred"] = phase["failed"]

        # Ф2: уведомление о висящих среди только что созданных (напр. конкурент без нашего канала).
        orphan_notices = [o for o in ctx.link_registry.find_orphans() if o["id"] in created_ids]
        for o in orphan_notices:
            facts.append(Fact(type="EntityOrphaned", data=o))
        res["orphan_notices"] = orphan_notices

        # S18-g: единый блок «имя + адрес + ID + цепочка» на КАЖДЫЙ созданный объект.
        res["entities"] = [_entity_block(nid) for nid in created_ids]
        res["adopted"] = adopted
        res["skipped_levels"] = chain["skipped"]
        for e in res["entities"]:
            facts.append(Fact(type="EntityRegistered", data=e))
        return ToolResult(status="success", data=res, facts=facts)

    async def structure_resolve(path: str) -> "ToolResult":
        """Предпросмотр адреса: какая цепочка получится в этом каталоге (ничего не создаёт).

        Отвечает ДО создания: чьи готовые ID унаследует новый узел, какие уровни пропущены,
        какие каталоги на диске ещё не зарегистрированы.
        """
        ok, res = ctx.safe(lambda: ctx.chain_resolver.resolve(path))
        if not ok:
            return res
        facts = [Fact(type="ChainResolved", data={
            "target": res["target"], "node_type": res["node_type"], "chain": res["chain_id"],
            "owner_id": res["owner_id"], "skipped": res["skipped"],
            "missing_required": res["missing_required"],
            "unresolved": [u["path"] for u in res["unresolved"] if u["exists"]]})]
        return ToolResult(status="success", data=res, facts=facts)

    async def structure_link(child_type: str, child_name: str,
                             parent_type: str, parent_name: str) -> "ToolResult":
        """Связать сущность с родителем В ОДНОМ месте (реестр — источник истины).

        Один вызов добавляет parent_id ребёнку; не требует правки обоих деревьев
        (экономит токены, исключает рассинхрон). Пример: привязать конкурента к нашему каналу.
        """
        ok, res = ctx.safe(lambda: ctx.link_registry.link(child_type, child_name, parent_type, parent_name))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="EntityLinked", data={
            "child_id": res["child"]["id"], "child_type": child_type, "child_name": child_name,
            "parent_id": res["parent_id"], "parent_type": parent_type, "parent_name": parent_name})])

    async def structure_migrate(entity_id: str, new_path: str) -> "ToolResult":
        """Миграция сущности: физический перенос папки + обновление реестра.

        Используется когда родитель появился позже (напр. конкурент без канала → привязка к каналу).
        Физически перемещает папку и обновляет path в реестре.
        """
        import shutil
        # Получаем текущий путь из реестра
        entity = ctx.link_registry.get(entity_id)
        if not entity:
            return ctx.err("ENTITY_NOT_FOUND", f"Сущность {entity_id} не найдена в реестре.",
                        "Сначала создай её через structure_create.", "structure_status")
        old_path = entity.get("path", "")
        # Проверяем что старый путь существует
        try:
            old_full = ctx.resolve(old_path)
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"Старый путь выходит за workspace: {old_path}")
        if not old_full.exists():
            return ctx.err("FILE_NOT_FOUND", f"Папка не найдена: {old_path}")
        # Проверяем что новый путь не занят
        try:
            new_full = ctx.resolve(new_path)
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"Новый путь выходит за workspace: {new_path}")
        if new_full.exists():
            return ctx.err("FILE_EXISTS", f"Путь уже существует: {new_path}")
        # Физический перенос
        new_full.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_full), str(new_full))
        # Обновляем реестр
        ok, res = ctx.safe(lambda: ctx.link_registry.migrate(entity_id, new_path))
        if not ok:
            return res
        return ToolResult(status="success", data=res, facts=[Fact(type="EntityMigrated", data={
            "id": entity_id, "old_path": old_path, "new_path": new_path})])

    async def structure_status() -> "ToolResult":
        """Сводка связей: висящие (ORPHAN) + наши каналы без конкурента (мягко).

        Это поверхность «уведомления от сервера»: у вас есть конкурент, не привязанный
        ни к одному каналу / у вас есть канал без конкурента.
        """
        orphans = ctx.link_registry.find_orphans()
        ours_no_comp = ctx.link_registry.find_childless("channel", "competitor_channel")
        facts = [Fact(type="EntityOrphaned", data=o) for o in orphans]
        return ToolResult(status="success",
                          data={"orphans": orphans, "our_channels_without_competitor": ours_no_comp},
                          facts=facts)

    async def structure_check_integrity() -> "ToolResult":
        """Фоновая проверка целостности реестра: висящие ссылки, дубликаты путей, сироты."""
        ok, res = ctx.safe(lambda: ctx.link_registry.check_integrity())
        if not ok:
            return res
        facts = []
        for issue in res.get("issues", []):
            facts.append(Fact(type="IntegrityIssue", data=issue))
        return ToolResult(status="success", data=res, facts=facts)

    # ═══ СТРУКТУРА: шаблонное создание (композиция по ссылке + контроль глубины) ═══
    engine.register(
        name="structure_create",
        title="Структура: создать узел по шаблону",
        description=(
            "Материализует узел (niche/network/channel/video/competitor_channel/competitor_video) "
            "по шаблону: свои папки/файлы + контейнеры детей. В детей спускается ТОЛЬКО для явно "
            "названных (children={тип:[имена]}) — так 'создать канал кроме видео' = не называть видео, "
            "а 'назвать видео' = создать всё его поддерево. Книги .xlsx созданных сущностей "
            "материализуются СРАЗУ по декларациям config/templates/tables/ (клиент передаёт только имена, "
            "листы/столбцы/формулы/enum подставляет сервер); книги неназванных сущностей не создаются. "
            "ID присваивает СЕРВЕР: предков берёт готовыми из каталога назначения, генерирует только "
            "собственный сегмент, пропущенные уровни не выдумывает. В ответе на КАЖДЫЙ созданный объект "
            "приходят имя, путь, id и цепочка владельцев (data.entities[] + факты EntityRegistered). "
            "Каталоги-предки, которые есть на диске, но не в реестре, блокируют создание "
            "(CHAIN_UNRESOLVED) — усынови их adopt=true или посмотри адрес заранее через structure_resolve."),
        input_schema={"type": "object", "properties": {
            "type": {"type": "string",
                     "enum": ctx.taxonomy.node_types,
                     "description": "Тип узла (ключ шаблона)"},
            "name": {"type": "string", "description": "Имя экземпляра (один сегмент пути, без '/')"},
            "parent_path": {"type": "string", "default": "",
                            "description": "Контейнер-путь родителя относительно workspace (пусто → корень типа; для niche = niches/)"},
            "children": {"type": "object",
                         "description": "Каких детей развернуть: {тип_ребёнка: [имена]}. Не названные — отложены (ChildDeferred).",
                         "additionalProperties": {"type": "array", "items": {"type": "string"}}},
            "adopt": {"type": "boolean", "default": False,
                      "description": "Зарегистрировать каталоги-предки, которые уже есть на диске (структура опередила реестр)"},
        }, "required": ["type", "name"]},
        handler=structure_create, group="structure", annotations=ANNOTATIONS_MODIFY)
    engine.register(
        name="structure_resolve",
        title="Структура: разрешить адрес каталога",
        description=(
            "Предпросмотр адреса ДО создания: показывает, какой тип узла здесь появится, чьи ГОТОВЫЕ ID он "
            "унаследует (chain — цепочка владельцев сверху вниз), какие объявленные уровни пропущены "
            "(skipped, напр. нет сетки), каких обязательных предков не хватает (missing_required → узел "
            "станет ORPHAN) и какие каталоги есть на диске, но не в реестре (unresolved). Ничего не создаёт "
            "и не пишет. Бери перед созданием в ЧУЖОЙ/существующей структуре и перед переносом, "
            "чтобы сверить, куда сущность попадёт."),
        input_schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "Каталог назначения относительно workspace (напр. niches/gaming/networks/net1/channels/chA/videos/)"},
        }, "required": ["path"]},
        handler=structure_resolve, group="structure", annotations=ANNOTATIONS_READONLY)
    engine.register(
        name="structure_link",
        title="Структура: связать сущности",
        description=(
            "Связывает сущность с родителем В ОДНОМ месте (реестр связей — источник истины): "
            "например конкурента с нашим каналом. Один вызов, не нужно править оба дерева — "
            "экономит токены и исключает рассинхрон. Снимает уведомление UNLINKED_ENTITY."),
        input_schema={"type": "object", "properties": {
            "child_type": {"type": "string", "description": "Тип привязываемой сущности (напр. competitor_channel)"},
            "child_name": {"type": "string", "description": "Имя привязываемой сущности"},
            "parent_type": {"type": "string", "description": "Тип родителя (напр. channel)"},
            "parent_name": {"type": "string", "description": "Имя родителя"},
        }, "required": ["child_type", "child_name", "parent_type", "parent_name"]},
        handler=structure_link, group="structure", annotations=ANNOTATIONS_MODIFY)
    engine.register(
        name="structure_migrate",
        title="Структура: миграция (перенос папки)",
        description=(
            "Физический перенос папки сущности + обновление реестра. Используется когда родитель "
            "появился позже (напр. конкурент был без канала, теперь канал есть — переносим "
            "competitors/competitor_A/ → competitors/my_channel/competitor_A/). "
            "Обновляет path в реестре и перемещает файлы на диске."),
        input_schema={"type": "object", "properties": {
            "entity_id": {"type": "string", "description": "ID сущности из реестра"},
            "new_path": {"type": "string", "description": "Новый путь относительно workspace"},
        }, "required": ["entity_id", "new_path"]},
        handler=structure_migrate, group="structure", annotations=ANNOTATIONS_MODIFY)
    async def structure_materialize_tables(pending: list[dict] | None = None) -> "ToolResult":
        """Фаза ТАБЛИЦЫ: материализация отложенных книг по декларациям (A1′)."""
        ok, res = ctx.safe(lambda: materializer.materialize_pending(pending or []))
        if not ok:
            return res
        facts = [Fact(type="TableMaterialized", data={
            "path": m["path"], "book": m["book"], "file_id": m.get("file_id", ""),
            "sheets": [s["sheet"] for s in m["sheets"]], "columns": m["columns_total"]})
            for m in res["materialized"]]
        return ToolResult(status="success", data=res, facts=facts)

    engine.register(
        name="structure_status",
        title="Структура: сводка связей (висящие)",
        description=(
            "Сводка реестра связей: висящие сущности (ORPHAN — напр. конкурент без нашего канала) "
            "и наши каналы без привязанного конкурента. Поверхность серверных уведомлений о непривязанном."),
        input_schema={"type": "object", "properties": {}},
        handler=structure_status, group="structure", annotations=ANNOTATIONS_READONLY)
    engine.register(
        name="structure_materialize_tables",
        title="Структура: фаза ТАБЛИЦЫ (материализация книг)",
        description=(
            "ДОГОНЯЮЩАЯ фаза ТАБЛИЦЫ (обычно не нужна: structure_create материализует книги сам). "
            "Создаёт .xlsx-книги по отложенным записям structure_create "
            "(факты TableDeferred: path + table_template). Форма книги берётся из декларации "
            "config/templates/tables/<table_template>.schema.yaml — листы, столбцы, формулы "
            "вычисляемых колонок, выпадающие списки enum. Отказ одной книги не отменяет остальные: "
            "результат содержит materialized и failed с кодом реакции на каждую неудачу."),
        input_schema={"type": "object", "properties": {
            "pending": {"type": "array", "description": "Отложенные книги из фактов TableDeferred",
                        "items": {"type": "object", "properties": {
                            "path": {"type": "string", "description": "Путь книги относительно workspace"},
                            "table_template": {"type": "string", "description": "Имя декларации без .schema.yaml"},
                            "file_id": {"type": "string", "description": "ID файла, присвоенный structure_create"},
                        }, "required": ["path", "table_template"]}},
        }, "required": ["pending"]},
        handler=structure_materialize_tables, group="structure", annotations=ANNOTATIONS_MODIFY)
    engine.register(
        name="structure_check_integrity",
        title="Структура: проверка целостности реестра",
        description=(
            "Фоновая проверка: висящие ссылки, дубликаты путей, сироты. "
            "Возвращает общую статистику + список проблем."),
        input_schema={"type": "object", "properties": {}},
        handler=structure_check_integrity, group="structure", annotations=ANNOTATIONS_READONLY)
