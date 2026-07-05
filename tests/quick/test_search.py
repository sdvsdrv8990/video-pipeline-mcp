#!/usr/bin/env python3
"""tests/quick/test_search.py — покрытие core/search (FsSearcher + QueryPlanner).

Постоянный набор (регрессия+coverage, НЕ удалять). Реальное поведение на временном
workspace: поиск (ext/name/content/limit), контракт FileResult, _extract_id,
_detect_entity_type по всей иерархии, D36 traversal-containment, QueryPlanner.
Тесты 6 SERVER-инструментов против живого сервера (L2) — долг (DNS pending).
"""
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.search.fs_searcher import FsSearcher, FsSearchTask, FsSearchError
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

        print("== FsSearcher: контракт результата (FileResult) ==")
        fr = r_cont[0]
        check("FileResult.path относителен workspace (не абсолютный)", not fr.path.startswith("/"))
        check("FileResult поля заполнены (name/size/entity_type/parent_path)",
              bool(fr.name) and fr.size > 0 and fr.entity_type and fr.parent_path is not None)

        print("== FsSearcher: _extract_id (PREFIX_<32hex>) ==")
        eid = fs._extract_id(ws / "niches/gaming/networks/n1/channels/ch/videos/v" / ("VID_" + "a" * 32 + ".xlsx"))
        check("извлекает VID_<32hex> из имени", eid == "VID_" + "a" * 32, f"got {eid!r}")
        check("нет ID в обычном имени → ''", fs._extract_id(ws / "docs" / "a.txt") == "")

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

        print("== FsSearcher: _detect_entity_type (D37 FIXED — все уровни иерархии) ==")
        def det(rel):
            return fs._detect_entity_type(ws / rel / "x.yaml")
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
        check("D37 вне niches → unknown", fs._detect_entity_type(ws / "docs" / "a.txt") == "unknown")

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
