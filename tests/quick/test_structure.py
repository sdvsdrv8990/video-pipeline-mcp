"""
tests/quick/test_structure.py — Ф1: TemplateEngine (шаблоны структуры + контроль глубины).

Standalone-прогон:  python tests/quick/test_structure.py
Проверяет: niche-only, channel-минус-видео, названное видео → поддерево, отложенные
таблицы (kind:table), пофрагментный контроль детей, PATH_ESCAPE, TEMPLATE_NOT_FOUND, ID узлов.
"""
import sys
import tempfile
import warnings
from pathlib import Path

warnings.simplefilter("error", UserWarning)  # чужой Fact.type (D25) → падение

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.engine import TemplateEngine, TemplateError
from core.ids import ChainResolver, IDGenerator, LinkRegistry, LinkError, Taxonomy

TPL_DIR = ROOT / "config" / "templates" / "workspace"

_checks = 0
_fails = []


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def paths(node):
    """Множество путей всех созданных фрагментов узла + детей (рекурсивно)."""
    s = {c["path"] for c in node["created"]}
    for sub in node["children"]:
        s |= paths(sub)
    return s


def new_engine():
    ws = Path(tempfile.mkdtemp(prefix="vpm_struct_"))
    return TemplateEngine(ws, IDGenerator(), TPL_DIR), ws


print("== 1. niche-only (проход 1: знаем только нишу) ==")
eng, ws = new_engine()
res = eng.create_node("niche", "fitness")
ps = paths(res)
ok(res["node_id"].startswith("NICHE"), "node_id с префиксом NICHE")
ok((ws / "niches" / "fitness" / "_NICHE_INDEX.md").exists(), "_NICHE_INDEX.md на диске")
ok((ws / "niches" / "fitness" / "niche_read.json").read_text() == "{}", "niche_read.json = {}")
ok((ws / "niches" / "fitness" / "networks").is_dir(), "контейнер networks/ создан")
ok(res["children"] == [], "детей нет (не названы)")
ok(any(d["type"] == "network" for d in res["deferred_children"]), "network отложен (ChildDeferred)")
ok(res["tables_pending"] == [], "у ниши нет таблиц")

print("== 2. channel — 'кроме видео' (контроль глубины) ==")
eng, ws = new_engine()
res = eng.create_node("channel", "ch1", parent_path="niches/fitness/networks/main/channels/")
base = ws / "niches/fitness/networks/main/channels/ch1"
ok(base.is_dir(), "папка канала создана")
ok((base / "scene_layouts").is_dir() and (base / "videos").is_dir(), "scene_layouts/ и videos/ созданы")
ok((base / "project_memory.md").exists(), "project_memory.md создан")
ok(res["children"] == [], "видео НЕ созданы (не названы)")
ok(any(d["type"] == "video" for d in res["deferred_children"]), "video отложен")
ok(list((base / "videos").iterdir()) == [], "videos/ пуст — ни одного видео")
tp = {t["path"].split("/")[-1] for t in res["tables_pending"]}
ok(tp == {"channel_data.xlsx", "channel_config.xlsx"}, "обе таблицы отложены (tables_pending), не на диске")
ok(not (base / "channel_data.xlsx").exists(), "channel_data.xlsx НЕ создан (фаза таблиц, Ф3)")

print("== 3. названное видео → всё поддерево видео ==")
eng, ws = new_engine()
res = eng.create_node("channel", "ch1",
                      parent_path="niches/fitness/networks/main/channels/",
                      children={"video": ["intro"]})
ok(len(res["children"]) == 1 and res["children"][0]["type"] == "video", "видео 'intro' развернуто")
vbase = ws / "niches/fitness/networks/main/channels/ch1/videos/intro"
ok(vbase.is_dir(), "папка видео создана под videos/intro")
ok((vbase / "assets" / "svg").is_dir() and (vbase / "assets" / "audio").is_dir(), "assets/{svg,audio} созданы (дети видео)")
ok((vbase / "read.json").exists() and (vbase / "renders").is_dir(), "read.json + renders/ созданы")
ok(res["children"][0]["node_id"].startswith("VID"), "видео получило ID с префиксом VID")
ok(res["children"][0]["parent_ids"] == [res["node_id"]], "parent_ids видео = [id канала]")

print("== 4. competitor_video (лист, только files) ==")
eng, ws = new_engine()
res = eng.create_node("competitor_video", "cv1", parent_path="x/videos/")
ok((ws / "x/videos/cv1/read.json").exists(), "competitor_video read.json создан")
ok(res["node_id"].startswith("CVID"), "префикс CVID")

print("== 5. containment: имя с обходом → ValueError (→ PATH_ESCAPE у обёртки) ==")
eng, ws = new_engine()
try:
    eng.create_node("niche", "../evil")
    ok(False, "traversal должен был бросить")
except ValueError:
    ok(True, "traversal имя → ValueError")
except TemplateError as e:
    ok(e.code == "VALIDATION_ERROR", "traversal имя → VALIDATION_ERROR (имя с '/')")

print("== 6. неизвестный тип → TEMPLATE_NOT_FOUND ==")
eng, ws = new_engine()
try:
    eng.create_node("bogus", "x")
    ok(False, "должно было бросить TEMPLATE_NOT_FOUND")
except TemplateError as e:
    ok(e.code == "TEMPLATE_NOT_FOUND", "неизвестный тип → TEMPLATE_NOT_FOUND")

print("== 7. пустое имя → VALIDATION_ERROR ==")
eng, ws = new_engine()
try:
    eng.create_node("niche", "  ")
    ok(False, "должно было бросить")
except TemplateError as e:
    ok(e.code == "VALIDATION_ERROR", "краевые пробелы в имени → VALIDATION_ERROR")

print("== 8. Ф2 реестр: конкурент без нашего канала → ORPHAN ==")
ws = Path(tempfile.mkdtemp(prefix="vpm_reg_"))
reg = LinkRegistry(ws)
reg.register({"id": "NET_1", "type": "network", "name": "main", "path": "n", "parent_ids": []})
reg.register({"id": "COMP_1", "type": "competitor_channel", "name": "rival", "path": "c", "parent_ids": ["NET_1"]})
orph = reg.find_orphans()
ok(len(orph) == 1 and orph[0]["id"] == "COMP_1", "конкурент без канала-родителя → в orphans")
ok(orph[0]["needs_parent_type"] == "channel", "needs_parent_type = channel")

print("== 9. Ф2 link (в одном месте) снимает ORPHAN ==")
reg.register({"id": "CH_1", "type": "channel", "name": "ourchan", "path": "ch", "parent_ids": ["NET_1"]})
res = reg.link("competitor_channel", "rival", "channel", "ourchan")
ok(res["parent_id"] == "CH_1", "link вернул parent_id канала")
ok(reg.find_orphans() == [], "после link висящих нет")
ok("CH_1" in reg.get("COMP_1")["parent_ids"], "parent_ids конкурента содержит id канала")

print("== 10. Ф2 наш канал без конкурента (мягкое уведомление) ==")
ws2 = Path(tempfile.mkdtemp(prefix="vpm_reg2_"))
reg2 = LinkRegistry(ws2)
reg2.register({"id": "CH_9", "type": "channel", "name": "solo", "path": "ch", "parent_ids": []})
cl = reg2.find_childless("channel", "competitor_channel")
ok(len(cl) == 1 and cl[0]["id"] == "CH_9", "канал без конкурента → find_childless")
reg2.register({"id": "COMP_9", "type": "competitor_channel", "name": "r2", "path": "c", "parent_ids": ["CH_9"]})
ok(reg2.find_childless("channel", "competitor_channel") == [], "после привязки конкурента — не в childless")

print("== 11. Ф2 link к несуществующему → ENTITY_NOT_FOUND ==")
try:
    reg2.link("competitor_channel", "ghost", "channel", "solo")
    ok(False, "должно было бросить")
except LinkError as e:
    ok(e.code == "ENTITY_NOT_FOUND", "link к несуществующему → ENTITY_NOT_FOUND")

print("== 12. Ф2 персист: новый инстанс видит данные (атомарно, D9) ==")
reg3 = LinkRegistry(ws)  # тот же workspace, что в §8/§9
ok(reg3.get("COMP_1") is not None and "CH_1" in reg3.get("COMP_1")["parent_ids"],
   "перезагруженный реестр сохранил связь")

print("== 13. Таксономия: префиксы и иерархия объявлены в шаблонах (F62) ==")
tx = Taxonomy(TPL_DIR)
ok(set(tx.node_types) == {"niche", "network", "channel", "video",
                          "competitor_channel", "competitor_video"}, "6 типов узлов из шаблонов")
ok(tx.prefix("video") == "VID" and tx.prefix("competitor_video") == "CVID", "префиксы из блока id:")
ok([a["type"] for a in tx.ancestors("video")] == ["niche", "network", "channel"],
   "предки video объявлены сверху вниз")
ok(tx.ancestors("video")[1]["type"] == "network" and not tx.ancestors("video")[1]["required"],
   "сетка объявлена НЕобязательной (уровень пропускаем)")
ok([a["role"] for a in tx.ancestors("competitor_video") if a["role"]] == ["owner_channel"],
   "наш канал помечен ролью owner_channel у видео конкурента")
ok(tx.child_type_for("channel", "videos") == "video", "тип ребёнка выводится из children[].container")
ok(tx.containers >= {"niches", "networks", "channels", "videos", "competitors"},
   "контейнеры собраны из объявлений, не из кода")
ok(tx.root_container("niche") == "niches" and tx.root_container("video") == "",
   "корневой контейнер объявлен в шаблоне ниши")

print("== 14. Резолвер: цепочка из каталога назначения (F63, сценарий владельца) ==")
eng13, ws13 = new_engine()
reg13 = LinkRegistry(ws13)
res13 = eng13.create_node("niche", "gaming", children={"network": ["net1"], "channel": ["chA"]})


def _reg_tree(node):
    reg13.register({"id": node["node_id"], "type": node["type"], "name": node["name"],
                    "path": node["path"], "parent_ids": node["parent_ids"]})
    for sub in node["children"]:
        _reg_tree(sub)


_reg_tree(res13)
rv = ChainResolver(ws13, reg13, tx)
ch_id = res13["children"][0]["children"][0]["node_id"]
out13 = rv.resolve("niches/gaming/networks/net1/channels/chA/videos/")
ok(out13["node_type"] == "video", "тип нового узла выведен из каталога (videos/ → video)")
ok(len(out13["chain"]) == 3 and out13["owner_id"] == ch_id,
   "цепочка = 3 готовых предка, владелец = существующий канал")
ok(out13["chain_id"].split("/") == [e["id"] for e in out13["chain"]],
   "chain_id — цепочка ID сверху вниз")
ok(out13["prefix"] == "VID" and out13["missing_required"] == [], "префикс из таксономии, обязательных пропусков нет")
ok(all(e["id"] in [n["node_id"] for n in [res13, res13["children"][0], res13["children"][0]["children"][0]]]
       for e in out13["chain"]), "предки взяты ГОТОВЫМИ (новых ID не появилось)")

print("== 15. Резолвер: пропуск уровня и незарегистрированные предки (S18-h) ==")
(ws13 / "niches/gaming/channels/chSolo/videos").mkdir(parents=True)
reg13.register({"id": "CH_solo", "type": "channel", "name": "chSolo",
                "path": "niches/gaming/channels/chSolo", "parent_ids": []})
out15 = rv.resolve("niches/gaming/channels/chSolo/videos/")
ok(out15["skipped"] == ["network"], "нет сетки → уровень в skipped, а не заглушка")
ok("NET_" not in out15["chain_id"] and out15["owner_id"] == "CH_solo", "сегмента сетки в цепочке нет")
(ws13 / "niches/gaming/networks/ghost/channels/chX/videos").mkdir(parents=True)
out15b = rv.resolve("niches/gaming/networks/ghost/channels/chX/videos/")
ok([u["path"] for u in out15b["unresolved"]] ==
   ["niches/gaming/networks/ghost", "niches/gaming/networks/ghost/channels/chX"],
   "каталоги на диске без записи → unresolved (сервер не выдумывает ID)")
ok(all(u["exists"] for u in out15b["unresolved"]), "unresolved помечает, что каталог реально на диске")

print("== 16. Инвариант пути: второй ID на занятый каталог → DUPLICATE_PATH (S18-h) ==")
try:
    reg13.register({"id": "CH_dup", "type": "channel", "name": "chSolo",
                    "path": "niches/gaming/channels/chSolo", "parent_ids": []})
    ok(False, "второй ID на занятый путь должен был упасть")
except LinkError as e:
    ok(e.code == "DUPLICATE_PATH", "раздвоение личности каталога → DUPLICATE_PATH")
ok(reg13.find_by_path("niches/gaming/channels/chSolo")["id"] == "CH_solo",
   "find_by_path: обратное отображение путь → сущность (F64)")
ok(rv.chain_for_entity(ch_id)["qualified_id"].endswith(ch_id) and
   len(rv.chain_for_entity(ch_id)["chain"]) == 2,
   "цепочка существующей сущности вычисляема (2 предка), собственный сегмент на месте")

print("== 17. check_integrity видит рассинхрон реестра с диском (F65) ==")
import shutil as _shutil
_shutil.move(str(ws13 / "niches/gaming/channels/chSolo"), str(ws13 / "niches/gaming/channels/chMoved"))
integ = reg13.check_integrity()
ok(any(i["type"] == "missing_path" and i["id"] == "CH_solo" for i in integ["issues"]),
   "перенос мимо реестра → missing_path (раньше 0 issues)")

print("== 18. Инструменты: создание ВТОРЫМ вызовом наследует цепочку (F63, сценарий владельца) ==")
import asyncio

from core.engine import Engine
from core.excel import ExcelEngine
from core.reactions import Reactions
from core.state import StateManager
from tools import structure as _structure_group
from tools._context import ToolContext

CFG = ROOT / "config"
ws18 = Path(tempfile.mkdtemp(prefix="vpm_tool_"))
_sm = StateManager(ws18)
_ids = IDGenerator()
_eng = Engine(reactions=Reactions(CFG / "server_reactions.yaml"), state_manager=_sm)
_ctx = ToolContext(_eng, _ids, _sm, None, ExcelEngine(ws18),
                   TemplateEngine(ws18, _ids, TPL_DIR), LinkRegistry(ws18), ws18, CFG)
_structure_group.register(_eng, _ctx)


def call(tool, **kw):
    return asyncio.run(_eng.tools[tool].handler(**kw))


s1 = call("structure_create", type="niche", name="gaming", children={"network": ["net1"], "channel": ["chA"]})
ok(s1.status == "success" and len(s1.data["entities"]) == 3, "сессия 1: ниша+сетка+канал, 3 блока entities[]")
ok(all({"id", "name", "path", "chain", "qualified_id", "owner_id"} <= set(e) for e in s1.data["entities"]),
   "каждый блок несёт имя, путь, id и цепочку (пост-контракт S18-g)")
ok(any(f.type == "EntityRegistered" for f in s1.facts), "факт EntityRegistered доехал в контракт (D25)")

s2 = call("structure_create", type="video", name="intro",
          parent_path="niches/gaming/networks/net1/channels/chA/videos/")
ev = s2.data["entities"][0]
ok(s2.status == "success" and ev["chain"].count("/") == 2,
   "видео вторым вызовом получило цепочку из 3 предков (было parent_ids=[])")
ok(ev["owner_id"] == s1.data["entities"][2]["id"], "владелец = ГОТОВЫЙ ID существующего канала")
ok(_ctx.link_registry.get(ev["id"])["parent_ids"] != [], "реестр знает предков видео")
ok(ev["qualified_id"] == f"{ev['chain']}/{ev['id']}", "квалифицированный адрес = цепочка + собственный сегмент")

print("== 19. Инструменты: предпросмотр и усыновление (S18-h) ==")
prev = call("structure_resolve", path="niches/gaming/networks/net1/channels/chA/videos/")
ok(prev.status == "success" and prev.data["node_type"] == "video", "structure_resolve выводит тип узла")
ok(prev.data["owner_id"] == ev["owner_id"], "предпросмотр отдаёт ТОТ ЖЕ адрес, что присвоит создание")
ok(not (ws18 / "niches/gaming/networks/net1/channels/chA/videos/ghost").exists(),
   "предпросмотр ничего не создал")
(ws18 / "niches/gaming/networks/ghostnet/channels/chX/videos").mkdir(parents=True)
blocked = call("structure_create", type="video", name="v1",
               parent_path="niches/gaming/networks/ghostnet/channels/chX/videos/")
ok(blocked.status == "error" and blocked.error.code == "CHAIN_UNRESOLVED",
   "предки на диске без записи → CHAIN_UNRESOLVED, а не выдуманный ID")
ok(blocked.error.recovery.suggested_tool == "structure_resolve", "ошибка ведёт к предпросмотру (F59)")
adopted = call("structure_create", type="video", name="v1", adopt=True,
               parent_path="niches/gaming/networks/ghostnet/channels/chX/videos/")
ok(adopted.status == "success" and [a["type"] for a in adopted.data["adopted"]] == ["network", "channel"],
   "adopt=true усыновляет предков с типами из шаблонов")
ok(any(f.type == "EntityAdopted" for f in adopted.facts), "усыновление отражено фактом, а не молча")

print("== 20. Инструменты: пропуск уровня доезжает до клиента (S18-g) ==")
solo = call("structure_create", type="channel", name="chSolo", parent_path="niches/gaming/channels/")
ok(solo.status == "success" and solo.data["skipped_levels"] == ["network"],
   "канал без сетки → skipped_levels=[network] в ответе")
ok("NET_" not in solo.data["entities"][0]["chain"], "сегмента сетки в цепочке нет (без заглушек)")

print("== 21. Ручная ветка ФС: владелец по вместимости и перенос через реестр (F61/F65) ==")
from tools import filesystem as _fs_group

_fs_group.register(_eng, _ctx)
vid_id = call("structure_create", type="video", name="clip",
              parent_path="niches/gaming/networks/net1/channels/chA/videos/").data["entities"][0]["id"]
manual = call("fs_create_file",
              path="niches/gaming/networks/net1/channels/chA/videos/clip/assets/notes.md", content="x")
ok(manual.data["owner_id"] == vid_id and manual.data["owner_type"] == "video",
   "файл, созданный вручную, получает владельца по вместимости (было: ничего)")
ok(manual.data["chain"].count("/") == 3 and manual.data["chain"].endswith(vid_id),
   "и цепочку владельцев сверху вниз, заканчивающуюся самим видео")
ok(any(f.type == "FileCreated" and f.data.get("owner_id") for f in manual.facts),
   "владелец доехал и в факт, не только в data")
ok(call("structure_check_integrity").data["issues_count"] == 0,
   "после создания реестр и диск согласованы (книги регистрируются только после материализации)")

mv = call("fs_move", source="niches/gaming/networks/net1/channels/chA/videos/clip",
          destination="niches/gaming/networks/net1/channels/chA/videos/clip_v2")
ok(mv.data["new_chain"] and mv.data["new_owner_id"],
   "адрес цели посчитан ДО переноса (ИИ может сверить корректность)")
ok([m["id"] for m in mv.data["entities_moved"]] == [vid_id],
   "запись реестра переехала вместе с диском (было: путь протухал молча)")
ok(_ctx.link_registry.get(vid_id)["path"].endswith("clip_v2") and
   _ctx.link_registry.get(vid_id)["id"] == vid_id,
   "адрес изменился, СОБСТВЕННЫЙ сегмент — нет (ссылки не рвутся, S18-g)")
ok(call("structure_check_integrity").data["issues_count"] == 0,
   "после переноса рассинхрона нет (раньше check_integrity давал 0 issues при битом реестре)")

dl = call("fs_delete", path="niches/gaming/networks/net1/channels/chA/videos/clip_v2", force=True)
ok([d["id"] for d in dl.data["entities_dropped"]] == [vid_id], "удаление снимает записи поддерева с реестра")
ok(_ctx.link_registry.get(vid_id) is None and call("structure_check_integrity").data["issues_count"] == 0,
   "реестр не переживает диск")

print("== 22. Собственный ID файла — по запросу (директива владельца S19) ==")
ok(tx.file_class("a.md") == "memory" and tx.file_prefix("a.md") == "MEM", "класс файла из объявления (.md → MEM)")
ok(tx.file_prefix("s.svg") == "AST" and tx.file_prefix("b.xlsx") == "TBL", "ассет → AST, книга → TBL")
ok(tx.file_class("x.unknown") == "file" and tx.file_prefix("x.unknown") == "FILE",
   "незаявленное расширение → дефолтный класс, а не отказ")
ok({"assets", "renders", "scene_layouts"} <= tx.containers,
   "папки из шаблонов пропускаются резолвером (структура, не сущности)")

vid2 = call("structure_create", type="video", name="clip2",
            parent_path="niches/gaming/networks/net1/channels/chA/videos/").data["entities"][0]["id"]
V2 = "niches/gaming/networks/net1/channels/chA/videos/clip2"
plain = call("fs_create_file", path=f"{V2}/assets/notes.md", content="x")
ok("id" not in plain.data and plain.data["owner_id"] == vid2,
   "без флага: собственного ID нет, владелец есть (создание без ID возможно)")
withid = call("fs_create_file", path=f"{V2}/assets/svg/scene.svg", content="<svg/>", assign_id=True)
ok(withid.data["id"].startswith("AST_") and withid.data["file_class"] == "asset",
   "assign_id=true: ID выдан, префикс соответствует классу (ИИ может сверить)")
ok(withid.data["qualified_id"].endswith(withid.data["id"]) and withid.data["owner_id"] == vid2,
   "квалифицированный адрес файла = цепочка владельцев + собственный сегмент")
again = call("fs_write_file", path=f"{V2}/assets/svg/scene.svg", content="<svg2/>", assign_id=True)
ok(again.data["id"] == withid.data["id"] and again.data["reused"] is True,
   "повторный запрос отдаёт ТОТ ЖЕ ID (второго на путь не заводится)")
ok(_ctx.link_registry.find_by_path(f"{V2}/assets/svg/scene.svg")["type"] == "asset",
   "файл с ID попал в реестр со своим классом")
ok(call("structure_check_integrity").data["issues_count"] == 0, "реестр и диск согласованы")
mvf = call("fs_move", source=f"{V2}/assets/svg/scene.svg", destination=f"{V2}/assets/scenes/scene.svg")
ok([m["id"] for m in mvf.data["entities_moved"]] == [withid.data["id"]],
   "файл с собственным ID переезжает вместе с записью, ID не меняется")
(ws18 / "niches/gaming/networks/ghostnet2").mkdir(parents=True)
blocked_file = call("fs_create_file", path="niches/gaming/networks/ghostnet2/x.md", content="", assign_id=True)
ok(blocked_file.status == "error" and blocked_file.error.code == "CHAIN_UNRESOLVED",
   "ID в незарегистрированном поддереве → отказ, а не выдуманная цепочка")
ok(call("fs_create_file", path="niches/gaming/networks/ghostnet2/x.md", content="").status == "success",
   "тот же файл без assign_id создаётся спокойно")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
