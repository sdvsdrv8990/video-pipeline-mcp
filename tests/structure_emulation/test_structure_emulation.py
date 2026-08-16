"""
tests/structure_emulation/test_structure_emulation.py — E-матрица: эмуляция реальной работы ИИ.

Standalone-прогон:  python tests/structure_emulation/test_structure_emulation.py
Сервер поднимает себе сам (харнесс T2): свой порт, своя временная область, настоящий ключ.

Отличие от `tests/quick/test_structure.py`: тот проверяет единицы (движок, реестр, инструмент),
этот — СЦЕНАРИИ, которыми ИИ пользуется на самом деле, целиком через провод. E-A конфиг-матрица
ниши, E-B четыре точки входа × контроль глубины, E-C метаморфный инвариант «3 прохода ≡ 2 ≡ 1».
Область одна на весь файл, сценарии разведены именами ниш — как у настоящего клиента.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.harness import live_server

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


def under(items, niche):
    """Только сущности этого сценария: реестр общий на всю область, как у клиента."""
    return [i for i in items if i.get("path", "").startswith(f"niches/{niche}/")]


with live_server() as srv:
    rpc = srv.rpc

    def create(**args):
        env = rpc.call_tool("structure_create", args)
        return env["data"] or {}, [f.get("type") for f in (env.get("facts") or [])], env

    def status():
        d = rpc.call_tool("structure_status", {})["data"] or {}
        return d.get("orphans", []), d.get("our_channels_without_competitor", []), d

    print("== E-A1. Холодный старт: сервер ведёт, а не отдаёт два пустых списка (F26) ==")
    _, _, cold = status()
    ok(cold.get("cold_start") is not None, "в пустой области ответ несёт признак холодного старта")
    ids = {r["id"] for r in cold.get("recommendations", [])}
    ok("no_data_yet" in ids,
       f"сервер говорит, что данных для анализа НЕТ, а не молчит ({sorted(ids)})")
    ok("study_competitors_first" in ids, "предложен рекомендованный путь — начать с конкурентов")

    d1, f1, _ = create(type="niche", name="ea1")
    ok("NodeCreated" in f1, "ниша создана (факт NodeCreated)")
    ok([c["type"] for c in d1["deferred_children"]] == ["network"],
       f"глубже сервер не лезет: сетка отложена ({[c['type'] for c in d1['deferred_children']]})")
    ok("ChildDeferred" in f1, "отложенный ребёнок объявлен фактом, а не молча пропущен")

    print("== E-A2. Ниша+сетка+наш канал, без конкурента → мягкое childless ==")
    d2, f2, _ = create(type="niche", name="ea2", children={"network": ["net"], "channel": ["ourch"]})
    ok({e["type"] for e in d2["entities"]} == {"niche", "network", "channel"},
       f"созданы ровно три уровня ({sorted({e['type'] for e in d2['entities']})})")
    orph2, childless2, _ = status()
    ok(under(orph2, "ea2") == [], "висящих в этом сценарии нет")
    ok([c["name"] for c in under(childless2, "ea2")] == ["ourch"],
       f"наш канал помечен как оставшийся без конкурента ({[c['name'] for c in under(childless2, 'ea2')]})")

    print("== E-A3. Ниша+сетка+конкурент, без нашего канала → ORPHAN с рецептом ==")
    d3, f3, _ = create(type="niche", name="ea3",
                       children={"network": ["net"], "competitor_channel": ["rival"]})
    ok("EntityOrphaned" in f3, "конкурент без канала объявлен висящим прямо в ответе на создание")
    orph3, childless3, _ = status()
    mine3 = under(orph3, "ea3")
    ok([o["name"] for o in mine3] == ["rival"], f"висит ровно конкурент ({[o['name'] for o in mine3]})")
    ok(mine3 and mine3[0]["needs_parent_type"] == "channel",
       "в уведомлении сказано, родителя КАКОГО типа не хватает")
    ok(under(childless3, "ea3") == [], "своих каналов в сценарии нет — и мягкого уведомления тоже")
    comp3 = next(e for e in d3["entities"] if e["type"] == "competitor_channel")
    ok(comp3["path"] == "niches/ea3/networks/net/competitors/rival",
       f"без якоря сегмент группировки ОПУЩЕН, а не подставлен заглушкой ({comp3['path']})")
    # F102: канал из соседней ниши — не кандидат. Имена в иерархии повторяются, и предложение
    # связать сущности разных проектов хуже честного «в твоей ветке якоря нет».
    plan3 = rpc.call_tool("structure_reconcile", {"entity_id": comp3["id"]})["data"]
    ok(len(plan3["needs_decision"]) == 1 and not plan3["reconciled"],
       f"сведение НЕ состоялось: якоря в своей ветке нет ({plan3})")
    need3 = (plan3["needs_decision"] or [{"candidates": ["<сведено вопреки ожиданию>"], "reason": ""}])[0]
    ok(need3["candidates"] == [],
       f"кандидаты ищутся в СВОЕЙ ветке, чужие ниши не предлагаются ({need3['candidates']})")
    ok("niches/ea3/networks/net" in need3["reason"],
       f"в причине названа ветка, где искали ({need3['reason']})")
    ok(plan3["entities_moved"] == 0, "при неоднозначности не сдвинуто ничего")

    print("== E-A4. Всё сразу: имя, легшее в путь, стало и связью (F101) ==")
    d4, f4, _ = create(type="niche", name="ea4",
                       children={"network": ["net"], "channel": ["ourch"],
                                 "competitor_channel": ["rival"]})
    comp4 = next(e for e in d4["entities"] if e["type"] == "competitor_channel")
    ch4 = next(e for e in d4["entities"] if e["type"] == "channel")
    ok(comp4["path"] == "niches/ea4/networks/net/competitors/ourch/rival",
       f"конкурент физически лёг ПОД нашим каналом ({comp4['path']})")
    orph4, childless4, _ = status()
    ok(under(orph4, "ea4") == [],
       f"и НЕ висит: путь сгруппирован — связь проставлена ({[o['name'] for o in under(orph4, 'ea4')]})")
    ok(under(childless4, "ea4") == [],
       "канал не числится «без конкурента» — сервер не сообщает двух противоположных фактов")
    ok("EntityOrphaned" not in f4, "в ответе на создание нет уведомления о висящем")
    rec4 = rpc.call_tool("structure_reconcile", {})["data"]
    touched4 = {r["id"] for r in rec4["reconciled"]} | {n["id"] for n in rec4["needs_decision"]}
    ok(comp4["id"] not in touched4 and rec4["entities_moved"] == 0,
       f"сведению в этом сценарии нечего делать — состояние уже целостно ({rec4})")
    ok(ch4["path"] == "niches/ea4/networks/net/channels/ourch", "наш канал лежит в своей ветке")

    print("== E-B. Четыре точки входа × контроль глубины: спуск ровно на ОДИН уровень ==")
    # ИИ начинает работу не всегда с ниши: он приходит на любой уровень существующего дерева.
    # Инвариант один на все входы — названный ребёнок материализуется, ЕГО дети откладываются.
    base_b = "niches/eb/networks/net"
    create(type="niche", name="eb", children={"network": ["net"]})
    CASES = [
        ("niche",              "niches/",               "b1", ["network"],                   "network",          ["channel", "competitor_channel"]),
        ("network",            "niches/eb/networks/",   "b2", ["channel", "competitor_channel"], "channel",      ["video"]),
        ("channel",            f"{base_b}/channels/",   "b3", ["video"],                     "video",            []),
        ("competitor_channel", f"{base_b}/competitors/", "b4", ["competitor_video"],         "competitor_video", []),
    ]
    for etype, parent, tag, deferred, named, grandchildren in CASES:
        bare, fb, _ = create(type=etype, name=f"{tag}bare", parent_path=parent)
        ok([c["type"] for c in bare["deferred_children"]] == deferred,
           f"{etype} без детей: отложены {deferred} ({[c['type'] for c in bare['deferred_children']]})")
        ok(bare["children"] == [],
           f"{etype}: ни один ребёнок не развёрнут сам собой ({[c['type'] for c in bare['children']]})")
        ok(fb.count("ChildDeferred") == len(deferred),
           f"{etype}: каждый отложенный объявлен фактом ({fb.count('ChildDeferred')} против {len(deferred)})")

        full, _, _ = create(type=etype, name=f"{tag}full", parent_path=parent,
                            children={named: [f"{tag}kid"]} if named else {})
        got = [c["type"] for c in full["children"]]
        ok(got == [named], f"{etype}: названный ребёнок {named} материализован ({got})")
        deep = [c["type"] for c in full["children"][0]["deferred_children"]] if full["children"] else []
        ok(deep == grandchildren,
           f"{etype}: внуки отложены, а не созданы — глубина ровно 1 ({deep})")

    print("== E-B. Книги: материализуются книги СОЗДАННЫХ сущностей, не объявленных вглубь ==")
    ch_b, _, _ = create(type="channel", name="bbooks", parent_path=f"{base_b}/channels/")
    mat_b = {m["path"].split("/")[-1] for m in ch_b["tables_materialized"]}
    ok(mat_b == {"channel_data.xlsx"},
       f"у канала без видео создана только его книга ({sorted(mat_b)})")
    ok(ch_b["tables_deferred"] == [],
       f"честно отложенных книг нет — все объявленные созданы ({ch_b['tables_deferred']})")

    print(f"\n{'='*50}")
    print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
    if _fails:
        print("ПРОВАЛЫ:")
        for f in _fails:
            print(f"  - {f}")
        sys.exit(1)
    print("ВСЁ ЗЕЛЁНОЕ ✅")
