"""Сквозной smoke-тест табличных инструментов (Кат. 3 + Кат. 2)."""
import asyncio
import json
import tempfile
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
warnings.simplefilter("error", UserWarning)  # ловим незарегистрированные коды/факты как ошибку

from core.engine import Engine
from core.state import StateManager
from core.ids import IDGenerator
import server

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("[PASS] " if cond else "[FAIL] ") + name)

async def main():
    tmp = Path(tempfile.mkdtemp())
    ws = tmp / "workspace"; ws.mkdir()
    sm = StateManager(ws)
    eng = Engine(state_manager=sm)
    server.register_basic_tools(eng, IDGenerator(), sm)

    # tools/list: все новые инструменты + title
    names = {t["name"] for t in eng.list_tools()}
    expected = {"table_get_column","table_get_row","table_set","table_append","table_delete",
                "json_push_to_queue","json_execute_queue","json_clear_queue",
                "excel_create_workbook","excel_add_sheet","excel_rename_sheet","excel_delete_sheet",
                "excel_reorder_sheets","excel_add_column","excel_delete_column","excel_move_column",
                "excel_insert_formula","excel_apply_formatting","excel_set_validation",
                "excel_read_range","excel_validate_formulas"}
    check(f"all {len(expected)} tools registered", expected <= names)
    check("all tools have title", all(t.get("title") for t in eng.list_tools()))
    check("groups tables+excel present", {g["group"] for g in eng.list_tools_grouped()} >= {"tables","excel","filesystem"})

    # Подготовим таблицу видео: read.json со схемой (status enum, performance_score computed)
    vid = ws / "videos" / "v1"; vid.mkdir(parents=True)
    read = {
        "META": {
            "schema": {
                "title": {"type": "string", "writable": True},
                "status": {"type": "enum", "enum": ["draft","ready","published"], "writable": True},
                "performance_score": {"type": "float", "computed": True, "writable": False},
            },
            "rows": {"VID_1": {"title": "Hook", "status": "draft", "performance_score": 0.0}},
        }
    }
    (vid / "read.json").write_text(json.dumps(read), encoding="utf-8")
    T = "videos/v1"

    async def call(tool, **p):
        return await eng.call(tool, p)

    # ── Кат. 3 чтения ──
    r = await call("table_get_column", table=T, sheet="META", column="status")
    check("get_column status", r.status=="success" and r.data["values"]=={"VID_1":"draft"})
    r = await call("table_get_row", table=T, sheet="META", row_id="VID_1")
    check("get_row VID_1", r.status=="success" and r.data["row"]["title"]=="Hook")
    r = await call("table_get_column", table=T, sheet="NOPE", column="x")
    check("SHEET_NOT_FOUND", r.status=="error" and r.error.code=="SHEET_NOT_FOUND")
    r = await call("table_get_row", table=T, sheet="META", row_id="ZZZ")
    check("ROW_NOT_FOUND", r.status=="error" and r.error.code=="ROW_NOT_FOUND")

    # ── защита формул + enum (немедленный отказ) ──
    r = await call("table_set", table=T, sheet="META", row_id="VID_1", column="performance_score", value=9.9)
    check("COMPUTED_READONLY on set", r.status=="error" and r.error.code=="COMPUTED_READONLY")
    r = await call("table_set", table=T, sheet="META", row_id="VID_1", column="status", value="bogus")
    check("ENUM_VIOLATION on set", r.status=="error" and r.error.code=="ENUM_VIOLATION")

    # ── set (queue) → execute → снапшот обновился ──
    r = await call("table_set", table=T, sheet="META", row_id="VID_1", column="status", value="ready")
    check("set queued", r.status=="success" and r.data["queued"]["action"]=="set")
    # до execute снапшот НЕ изменён
    r = await call("table_get_row", table=T, sheet="META", row_id="VID_1")
    check("snapshot unchanged before execute", r.data["row"]["status"]=="draft")
    r = await call("json_execute_queue", table=T)
    check("execute applied=1, xlsx_synced False", r.status=="success" and r.data["applied"]==1 and r.data["xlsx_synced"] is False)
    r = await call("table_get_row", table=T, sheet="META", row_id="VID_1")
    check("snapshot updated after execute", r.data["row"]["status"]=="ready")

    # ── append (server ID) → execute ──
    r = await call("table_append", table=T, sheet="META", data={"title":"Act2","status":"draft"}, id_prefix="VID")
    new_id = r.data["row_id"]
    check("append server id VID_", r.status=="success" and new_id.startswith("VID_"))
    await call("json_execute_queue", table=T)
    r = await call("table_get_row", table=T, sheet="META", row_id=new_id)
    check("appended row present", r.status=="success" and r.data["row"]["title"]=="Act2")

    # ── delete → execute ──
    await call("table_delete", table=T, sheet="META", row_id=new_id)
    await call("json_execute_queue", table=T)
    r = await call("table_get_row", table=T, sheet="META", row_id=new_id)
    check("row deleted after execute", r.status=="error" and r.error.code=="ROW_NOT_FOUND")

    # ── clear_queue ──
    await call("table_set", table=T, sheet="META", row_id="VID_1", column="title", value="X")
    r = await call("json_clear_queue", table=T)
    check("clear_queue cleared>=1", r.status=="success" and r.data["cleared"]>=1)
    r = await call("json_execute_queue", table=T)
    check("execute empty after clear", r.data["applied"]==0)

    # ── push_to_queue with read action → INVALID_ACTION ──
    r = await call("json_push_to_queue", table=T, action={"action":"get_row","sheet":"META","row_id":"VID_1"})
    check("INVALID_ACTION for read", r.status=="error" and r.error.code=="INVALID_ACTION")

    # ── PATH_ESCAPE ──
    r = await call("table_get_row", table="../../etc", sheet="META", row_id="x")
    check("PATH_ESCAPE", r.status=="error" and r.error.code=="PATH_ESCAPE")

    # ── Кат. 2 Excel ──
    r = await call("excel_create_workbook", path="videos/v1/video_data.xlsx", sheet="META")
    check("workbook created", r.status=="success")
    r = await call("excel_create_workbook", path="videos/v1/video_data.xlsx")
    check("FILE_EXISTS on recreate", r.status=="error" and r.error.code=="FILE_EXISTS")
    r = await call("excel_add_sheet", path="videos/v1/video_data.xlsx", sheet="PERFORMANCE")
    check("sheet added", r.status=="success" and "PERFORMANCE" in r.data["sheets"])
    r = await call("excel_add_column", path="videos/v1/video_data.xlsx", sheet="META", column="status")
    check("column added", r.status=="success")
    r = await call("excel_add_column", path="videos/v1/video_data.xlsx", sheet="META", column="status")
    check("COLUMN_EXISTS", r.status=="error" and r.error.code=="COLUMN_EXISTS")
    r = await call("excel_insert_formula", path="videos/v1/video_data.xlsx", sheet="PERFORMANCE", cell="B2", formula="=A2*2")
    check("formula inserted", r.status=="success")
    r = await call("excel_insert_formula", path="videos/v1/video_data.xlsx", sheet="PERFORMANCE", cell="B2", formula="=A2*3")
    check("FORMULA_PROTECTED", r.status=="error" and r.error.code=="FORMULA_PROTECTED")
    r = await call("excel_insert_formula", path="videos/v1/video_data.xlsx", sheet="PERFORMANCE", cell="B2", formula="=A2*3", overwrite=True)
    check("formula overwrite ok", r.status=="success")
    r = await call("excel_set_validation", path="videos/v1/video_data.xlsx", sheet="META", column="status", allowed=["draft","ready"])
    check("validation set", r.status=="success")
    r = await call("excel_apply_formatting", path="videos/v1/video_data.xlsx", sheet="META", target="A1:B1", fill="FFCC00", bold=True)
    check("formatting applied 2 cells", r.status=="success" and r.data["cells"]==2)
    r = await call("excel_read_range", path="videos/v1/video_data.xlsx", sheet="META", cell_range="A1:B1")
    check("read_range debug", r.status=="success" and r.data["values"][0][0]=="status")
    r = await call("excel_validate_formulas", path="videos/v1/video_data.xlsx")
    check("validate_formulas ok", r.status=="success" and r.data["ok"] is True)
    r = await call("excel_add_sheet", path="videos/v1/nope.xlsx", sheet="X")
    check("WORKBOOK_NOT_FOUND", r.status=="error" and r.error.code=="WORKBOOK_NOT_FOUND")

    print(f"\n=== ИТОГО: {len(PASS)}/{len(PASS)+len(FAIL)} ===")
    if FAIL:
        print("FAILED:", FAIL); sys.exit(1)

asyncio.run(main())
