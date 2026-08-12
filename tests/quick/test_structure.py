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

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
