#!/usr/bin/env python3
"""tests/quick/test_search.py — покрытие core/search (FsSearcher + QueryPlanner).

Постоянный набор (регрессия+coverage, НЕ удалять). Реальное поведение на временном
workspace: поиск (ext/name/content/limit), контракт FileResult, личность из реестра,
_detect_entity_type по объявленной раскладке, traversal-containment, QueryPlanner.
Тесты 6 SERVER-инструментов против живого сервера (L2) — долг (DNS pending).
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.search.fs_searcher import FsSearcher, FsSearchTask, FsSearchError
from core.ids import LinkRegistry, Taxonomy

TPL_DIR = Path(__file__).resolve().parents[2] / "config" / "templates" / "workspace"
from core.search.query_planner import QueryPlanner, QueryPlan, SearchError

_checks = 0
_fails = []


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def _mkworkspace():
    ws = Path(tempfile.mkdtemp(prefix="vpm_search_"))
    (ws / "docs").mkdir(parents=True)
    (ws / "docs" / "a.txt").write_text("alpha ROOT marker line", encoding="utf-8")
    (ws / "docs" / "b.md").write_text("beta content", encoding="utf-8")
    (ws / "nested" / "deep").mkdir(parents=True)
    (ws / "nested" / "deep" / "c.txt").write_text("gamma", encoding="utf-8")
    vid = ws / "niches" / "gaming" / "networks" / "n1" / "channels" / "ch" / "videos" / "v"
    vid.mkdir(parents=True)
    (vid / ("VID_" + "a" * 32 + ".xlsx")).write_text("x", encoding="utf-8")
    return ws


def main():
    ws = _mkworkspace()
    try:
        fs = FsSearcher(ws)

        print("== FsSearcher: happy-path поиск ==")
        r_txt = fs.search(FsSearchTask(id="t", root="", extensions=[".txt"]))
        check("поиск по extension .txt → 2 файла (a.txt, c.txt)", len(r_txt) == 2,
              f"got {[x.path for x in r_txt]}")
        r_name = fs.search(FsSearchTask(id="t", root="", name_pattern=r"^b\.md$"))
        check("поиск по name_pattern → b.md", len(r_name) == 1 and r_name[0].name == "b.md")
        r_cont = fs.search(FsSearchTask(id="t", root="", content_keywords=["alpha"]))
        check("поиск по content_keywords ['alpha'] → только a.txt",
              len(r_cont) == 1 and r_cont[0].name == "a.txt")
        r_lim = fs.search(FsSearchTask(id="t", root="", limit=1))
        check("limit=1 → не больше 1 результата", len(r_lim) == 1)
        r_sub = fs.search(FsSearchTask(id="t", root="docs", extensions=[".txt"]))
        check("root='docs' сужает область → 1 (a.txt, не c.txt)", len(r_sub) == 1)

        # Эти четыре фильтра модель умела всегда, но обёртка `fs_smart_search` их не отдавала —
        # объявленная возможность, недоступная клиенту. Проверяем саму способность фильтровать.
        r_big = fs.search(FsSearchTask(id="t", root="docs", size_min=15))
        check("size_min отсекает мелкие файлы", [x.name for x in r_big] == ["a.txt"],
              [(x.name, x.size) for x in r_big])
        r_small = fs.search(FsSearchTask(id="t", root="docs", size_max=14))
        check("size_max отсекает крупные", [x.name for x in r_small] == ["b.md"],
              [(x.name, x.size) for x in r_small])
        r_after = fs.search(FsSearchTask(id="t", root="docs", modified_after="2000-01-01"))
        check("modified_after пропускает свежие файлы", len(r_after) == 2, len(r_after))
        r_before = fs.search(FsSearchTask(id="t", root="docs", modified_before="2000-01-01"))
        check("modified_before отсекает их же", r_before == [], [x.name for x in r_before])

        print("== FsSearcher: контракт результата (FileResult) ==")
        fr = r_cont[0]
        check("FileResult.path относителен workspace (не абсолютный)", not fr.path.startswith("/"))
        check("FileResult поля заполнены (name/size/entity_type/parent_path)",
              bool(fr.name) and fr.size > 0 and fr.entity_type and fr.parent_path is not None)

        print("== FsSearcher: личность файла из РЕЕСТРА, не из имени (F60) ==")
        # Личность файла даёт реестр по вместимости, поэтому разбор имени запрещён насовсем.
        check("метод разбора имени удалён (имя больше не источник ID)", not hasattr(fs, "_extract_id"))
        vid_id = "VID_" + "a" * 32
        vpath = "niches/gaming/networks/n1/channels/ch/videos/v"
        (ws / vpath).mkdir(parents=True, exist_ok=True)
        (ws / vpath / "read.json").write_text("{}")
        reg_ws = LinkRegistry(ws)
        reg_ws.register({"id": vid_id, "type": "video", "name": "v", "path": vpath, "parent_ids": []})
        fs_reg = FsSearcher(ws, registry=reg_ws, taxonomy=Taxonomy(TPL_DIR))
        hits = fs_reg.search(FsSearchTask(id="t", root="", id_pattern=vid_id))
        check("поиск по РЕАЛЬНОМУ ID находит файлы сущности (раньше 0)", len(hits) == 2, f"got {len(hits)}")
        check("имя файла больше ни при чём — найдены оба файла видео",
              {h.name for h in hits} == {"read.json", "VID_" + "a" * 32 + ".xlsx"})
        check("у результата есть владелец", hits[0].owner_id == vid_id, hits[0].owner_id)
        check("owner_id-фильтр отдаёт то же", len(fs_reg.search(
            FsSearchTask(id="t", root="", owner_id=vid_id))) == 2)
        check("несуществующий ID → пусто, а не всё подряд",
              fs_reg.search(FsSearchTask(id="t", root="", id_pattern="VID_нет")) == [])
        # chain_prefix = поддерево: цепочка владельцев начинается с указанного префикса
        reg_ws.register({"id": "NICHE_" + "b" * 32, "type": "niche", "name": "gaming",
                         "path": "niches/gaming", "parent_ids": []})
        reg_ws.register({"id": "NET_" + "c" * 32, "type": "network", "name": "n1",
                         "path": "niches/gaming/networks/n1", "parent_ids": ["NICHE_" + "b" * 32]})
        fs_reg2 = FsSearcher(ws, registry=reg_ws, taxonomy=Taxonomy(TPL_DIR))
        in_net = fs_reg2.search(FsSearchTask(id="t", root="", chain_prefix="NICHE_" + "b" * 32 + "/NET_" + "c" * 32))
        check("chain_prefix отбирает поддерево сетки", len(in_net) == 2, f"got {len(in_net)}")
        check("chain_prefix чужой сетки → пусто",
              fs_reg2.search(FsSearchTask(id="t", root="", chain_prefix="NET_" + "d" * 32)) == [])
        check("цепочка владельцев доехала в результат",
              in_net[0].chain.startswith("NICHE_" + "b" * 32 + "/NET_"), in_net[0].chain)
        check("тип сущности выводится из таксономии", hits[0].entity_type == "video", hits[0].entity_type)
        check("без таксономии тип не выдумывается",
              FsSearcher(ws).search(FsSearchTask(id="t", root="docs"))[0].entity_type == "unknown")

        print("== FsSearcher: adversarial D36 (path traversal) ==")
        def esc(root):
            try:
                fs.search(FsSearchTask(id="t", root=root, content_keywords=["root"]))
                return False
            except FsSearchError as e:
                return getattr(e, "code", "") == "PATH_ESCAPE"
        check("D36 root='/etc' → PATH_ESCAPE", esc("/etc"))
        check("D36 root='../../../../etc' → PATH_ESCAPE", esc("../../../../etc"))
        check("D36 root='docs/../../..' → PATH_ESCAPE", esc("docs/../../.."))

        print("== FsSearcher: edge ==")
        try:
            fs.search(FsSearchTask(id="t", root="no_such_dir"))
            check("несуществующий root → PATH_NOT_FOUND", False, "не бросил")
        except FsSearchError as e:
            check("несуществующий root → PATH_NOT_FOUND", getattr(e, "code", "") == "PATH_NOT_FOUND")

        print("== FsSearcher: нечитаемое считается, а не исчезает ==")
        # Файл без прав на чтение при фильтре по содержимому: молчаливый пропуск неотличим
        # от «не совпало», и ИИ решает, что искомого в проекте нет.
        _locked = ws / "docs" / "закрытый.txt"
        _locked.write_text("root секрет", encoding="utf-8")
        _locked.chmod(0o000)
        try:
            _task = FsSearchTask(id="t", root="docs", content_keywords=["root"])
            _hits = fs.search(_task)
            _readable = os.access(_locked, os.R_OK)   # под root права не мешают — тогда пропуск
            if _readable:
                check("нечитаемое: пропущено (запуск от root, права не действуют)", True, "skip")
            else:
                check("нечитаемый файл не попал в результаты",
                      all(h.name != "закрытый.txt" for h in _hits), [h.name for h in _hits])
                check("нечитаемый файл ПОИМЕНОВАН в потерях, а не исчез",
                      any("закрытый.txt" in u for u in _task.unreadable), _task.unreadable)
        finally:
            _locked.chmod(0o644)
            _locked.unlink()

        print("== FsSearcher: load_query (YAML-строка, не путь) ==")
        task = fs.load_query("root: docs\nextensions: ['.txt']\nname_pattern: 'a'\nlimit: 5")
        check("load_query парсит YAML-контент в FsSearchTask",
              task.root == "docs" and task.extensions == [".txt"] and task.limit == 5)

        print("== QueryPlanner: load_query_from_dict + containment ==")
        qp = QueryPlanner(table_engine=None, workspace=ws)
        plan = qp.load_query_from_dict({"name": "q", "description": "d",
                                        "reads": [{"table": "t1", "sheet": "META", "columns": ["a"]}]})
        check("load_query_from_dict → QueryPlan с reads", isinstance(plan, QueryPlan) and len(plan.reads) == 1)
        check("ReadTask поля распарсились", plan.reads[0].table == "t1" and plan.reads[0].sheet == "META")
        try:
            qp.load_query("/etc/passwd")
            check("D36 QueryPlanner.load_query('/etc/passwd') → PATH_ESCAPE", False, "не бросил")
        except SearchError as e:
            check("D36 QueryPlanner.load_query('/etc/passwd') → PATH_ESCAPE",
                  getattr(e, "code", "") == "PATH_ESCAPE")

        print("== QueryPlanner: фильтры и сортировка в СВОЕЙ зоне (F55, слепая зона 3) ==")
        # Фильтрацию и сортировку проверяет ХОЗЯИН зоны: регрессия на разнотипных значениях
        # живёт в `test_audit_fixes`, и снятие фильтра здесь не покраснело бы вовсе.
        class _Snapshot:
            """Источник данных планировщика: лист как его отдаёт движок таблиц."""

            @staticmethod
            def load_snapshot(_table):
                return {"META": {"rows": {
                    "R1": {"name": "альфа", "views": 100, "tag": "игры"},
                    "R2": {"name": "бета", "views": 20, "tag": "музыка"},
                    "R3": {"name": "гамма", "views": "много", "tag": "игры"},
                }}}

        qp2 = QueryPlanner(table_engine=_Snapshot(), workspace=ws)

        def _rows(read_extra=None, sort=None):
            data = {"name": "q", "description": "d",
                    "reads": [{"table": "t", "sheet": "META", **(read_extra or {})}]}
            if sort:
                data["sort"] = sort
            return qp2.execute_plan(qp2.load_query_from_dict(data))["rows"]

        got = _rows({"filter": {"tag": "игры"}})
        check("фильтр по равенству отбирает строки, а не отдаёт всё",
              sorted(r["_row_id"] for r in got) == ["R1", "R3"], [r["_row_id"] for r in got])
        got = _rows({"filter": {"views": {"gt": 50}}})
        check("фильтр gt отбирает по числу и не падает на строковом значении (F42)",
              [r["_row_id"] for r in got] == ["R1"], [r["_row_id"] for r in got])
        got = _rows({"filter": {"name": {"contains": "ам"}}})
        check("фильтр contains ищет подстроку", [r["_row_id"] for r in got] == ["R3"],
              [r["_row_id"] for r in got])
        got = _rows({"filter": {"tag": {"in": ["музыка"]}}})
        check("фильтр in сужает по списку", [r["_row_id"] for r in got] == ["R2"],
              [r["_row_id"] for r in got])
        got = _rows({"filter": {"_row_id": {"eq": "R2"}}})
        check("поиск по идентификатору строки находит её, а не отдаёт ноль (F57)",
              [r["_row_id"] for r in got] == ["R2"], [r["_row_id"] for r in got])
        got = _rows({"filter": {"_row_id": {"in": ["R1", "R3"]}}})
        check("идентификатор работает во всех операциях фильтра, не только eq",
              sorted(r["_row_id"] for r in got) == ["R1", "R3"], [r["_row_id"] for r in got])
        got = _rows({"filter": {"_row_id": {"in": ["R1", "R3"]}, "tag": "игры"}})
        check("идентификатор комбинируется с другими фильтрами в один набор",
              sorted(r["_row_id"] for r in got) == ["R1", "R3"], [r["_row_id"] for r in got])
        got = _rows({"filter": {"_row_id": {"eq": "НЕТ_ТАКОЙ"}}})
        check("несуществующий идентификатор по-прежнему даёт пусто, а не всё подряд",
              got == [], got)
        # Снапшот у движка ОДИН объект и переиспользуется, поэтому фикстура обязана отдавать
        # его по ссылке: с фикстурой, собирающей словарь заново, запись в строку не видна вовсе.
        class _SharedSnapshot:
            """Тот же лист, но всегда та же память — как отдаёт движок таблиц."""

            data = {"META": {"rows": {"R1": {"name": "альфа", "views": 100, "tag": "игры"}}}}

            @classmethod
            def load_snapshot(cls, _table):
                return cls.data

        qp3 = QueryPlanner(table_engine=_SharedSnapshot(), workspace=ws)
        qp3.execute_plan(qp3.load_query_from_dict(
            {"name": "q", "reads": [{"table": "t", "sheet": "META"}]}))
        check("чтение не пишет в строку снапшота: идентификатор попадает в копию",
              "_row_id" not in _SharedSnapshot.data["META"]["rows"]["R1"],
              sorted(_SharedSnapshot.data["META"]["rows"]["R1"]))

        got = _rows(sort={"column": "views", "order": "desc"})
        check("сортировка убыванием: наибольшее первым, чужеродное в хвосте (F90)",
              [r["_row_id"] for r in got] == ["R1", "R2", "R3"], [r["_row_id"] for r in got])
        got = _rows(sort={"column": "views", "order": "asc"})
        check("сортировка возрастанием: порядок чисел развернулся, хвост остался хвостом (F90)",
              [r["_row_id"] for r in got] == ["R2", "R1", "R3"], [r["_row_id"] for r in got])
        got = _rows(sort={"column": "name", "order": "desc"})
        check("текстовый столбец сортируется по-прежнему по алфавиту в обе стороны",
              [r["_row_id"] for r in got] == ["R3", "R2", "R1"], [r["_row_id"] for r in got])

        print("== FsSearcher: _detect_entity_type (D37 FIXED — все уровни иерархии) ==")
        fs_tx = FsSearcher(ws, taxonomy=Taxonomy(TPL_DIR))
        def det(rel):
            return fs_tx._detect_entity_type(ws / rel / "x.yaml")
        check("D37 niche", det("niches/gaming") == "niche", det("niches/gaming"))
        check("D37 network", det("niches/gaming/networks/n1") == "network", det("niches/gaming/networks/n1"))
        check("D37 channel", det("niches/gaming/networks/n1/channels/ch") == "channel",
              det("niches/gaming/networks/n1/channels/ch"))
        check("D37 video", det("niches/gaming/networks/n1/channels/ch/videos/v") == "video",
              det("niches/gaming/networks/n1/channels/ch/videos/v"))
        check("D37 competitor_channel", det("niches/gaming/networks/n1/competitors/c1") == "competitor_channel",
              det("niches/gaming/networks/n1/competitors/c1"))
        check("D37 competitor_video", det("niches/gaming/networks/n1/competitors/c1/videos/v") == "competitor_video",
              det("niches/gaming/networks/n1/competitors/c1/videos/v"))
        check("D37 вне niches → unknown", fs_tx._detect_entity_type(ws / "docs" / "a.txt") == "unknown")
        # §4: конкурент может лежать под сегментом НАШЕГО канала — раскладка та же декларация
        check("D37 конкурент под нашим каналом",
              det("niches/gaming/networks/n1/competitors/chA/c1") == "competitor_channel",
              det("niches/gaming/networks/n1/competitors/chA/c1"))
        check("D37 видео конкурента под нашим каналом",
              det("niches/gaming/networks/n1/competitors/chA/c1/videos/v") == "competitor_video",
              det("niches/gaming/networks/n1/competitors/chA/c1/videos/v"))

        print(f"\n{'=' * 50}")
        passed = _checks - len(_fails)
        print(f"РЕЗУЛЬТАТ: {passed}/{_checks} прошло")
        if _fails:
            print("ПРОВАЛЫ:")
            for f in _fails:
                print(f"  - {f}")
            sys.exit(1)
        print("ВСЁ ЗЕЛЁНОЕ ✅")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    main()
