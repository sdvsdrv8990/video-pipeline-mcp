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
from core.ids import IDGenerator, LinkRegistry, LinkError

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

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
