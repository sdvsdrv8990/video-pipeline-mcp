"""
tests/quick/test_table_materializer.py — A1′: материализация книг по .schema.yaml (фаза ТАБЛИЦЫ).

Standalone-прогон:  python tests/quick/test_table_materializer.py
Проверяет: e2e proof-схема network_config → .xlsx (листы/столбцы/enum-валидация/формулы),
контракт ошибок (нет схемы, битая схема, книга уже есть), фаза по tables_pending
(отказ одной книги не роняет соседние).
"""
import sys
import tempfile
import warnings
from pathlib import Path

import openpyxl

warnings.simplefilter("error", UserWarning)  # чужой код ошибки (D25/G14) → падение

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.engine import TableMaterializer, TableMaterializerError
from core.excel import ExcelEngine

SCHEMAS = ROOT / "config" / "templates" / "tables"

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


def new_materializer(ws: str):
    return TableMaterializer(ExcelEngine(ws), SCHEMAS)


ws = tempfile.mkdtemp(prefix="tm_")
mat = new_materializer(ws)

print("== 1. e2e: proof-схема network_config → книга ==")
res = mat.materialize("network_config", "networks/net_1/network_config.xlsx")
book = Path(ws) / "networks" / "net_1" / "network_config.xlsx"
ok(book.exists(), "книга материализована на диске")
ok(res["book"] == "network_config" and res["level"] == "network", "в результате книга и уровень из схемы")
ok(len(res["sheets"]) == 4, f"4 листа из схемы (получено {len(res['sheets'])})")
ok(res["columns_total"] == 17, f"17 столбцов суммарно (5+5+3+4 по схеме) (получено {res['columns_total']})")

print("== 2. Форма книги в .xlsx соответствует декларации ==")
wb = openpyxl.load_workbook(book)
ok(wb.sheetnames == ["NETWORK_SCHEDULE_MASTER", "SHARED_RESOURCES", "CROSS_CHANNEL_RULES", "NETWORK_GATES"],
   f"порядок листов как в схеме: {wb.sheetnames}")
headers = [c.value for c in wb["NETWORK_SCHEDULE_MASTER"][1]]
ok(headers == ["date", "channel_id", "planned_topic", "status", "conflict_check"],
   f"заголовки первого листа: {headers}")
ok(len(wb["SHARED_RESOURCES"].data_validations.dataValidation) == 0,
   "лист без enum-колонок не получил лишних валидаций")

print("== 3. type: enum → выпадающий список (set_validation) ==")
dvs = wb["NETWORK_SCHEDULE_MASTER"].data_validations.dataValidation
ok(len(dvs) == 1, f"одна валидация на лист с одним enum (получено {len(dvs)})")
ok(dvs and "PLANNED" in dvs[0].formula1 and "SCHEDULED" in dvs[0].formula1,
   "значения enum из схемы попали в дропдаун")
dvs_rules = wb["CROSS_CHANNEL_RULES"].data_validations.dataValidation
ok(dvs_rules and "HARD" in dvs_rules[0].formula1, "enum второго листа (HARD/SOFT) тоже материализован")

print("== 4. Ошибки идут кодом реестра, не текстом ==")
try:
    mat.materialize("нет_такой_книги", "x.xlsx")
    ok(False, "несуществующая схема должна падать")
except TableMaterializerError as e:
    ok(e.code == "TEMPLATE_NOT_FOUND", f"нет схемы → TEMPLATE_NOT_FOUND (получено {e.code})")
    ok(bool(e.reason), "в ошибке есть actionable reason")

bad = SCHEMAS / "_tmp_broken.schema.yaml"
bad.write_text("book: broken\nsheets: []\n", encoding="utf-8")
try:
    mat.materialize("_tmp_broken", "broken.xlsx")
    ok(False, "схема без листов должна падать")
except TableMaterializerError as e:
    ok(e.code == "SCHEMA_INVALID", f"схема без листов → SCHEMA_INVALID (получено {e.code})")
finally:
    bad.unlink()

try:
    mat.materialize("network_config", "networks/net_1/network_config.xlsx")
    ok(False, "существующая книга не должна молча перезаписываться")
except TableMaterializerError as e:
    ok(e.code == "FILE_EXISTS", f"книга уже есть → FILE_EXISTS (получено {e.code})")

print("== 5. Фаза ТАБЛИЦЫ: обход tables_pending от structure_create ==")
ws2 = tempfile.mkdtemp(prefix="tm2_")
mat2 = new_materializer(ws2)
pending = [
    {"path": "networks/n1/network_config.xlsx", "table_template": "network_config",
     "required": True, "file_id": "FILE_1"},
    {"path": "networks/n2/network_config.xlsx", "table_template": "network_config",
     "required": True, "file_id": "FILE_2"},
    {"path": "networks/n3/unknown.xlsx", "table_template": "ещё_не_заведена",
     "required": False, "file_id": "FILE_3"},
    {"path": "networks/n4/nameless.xlsx", "required": False, "file_id": "FILE_4"},
]
phase = mat2.materialize_pending(pending)
ok(phase["created"] == 2 and phase["total"] == 4, f"2 из 4 книг созданы: {phase['created']}/{phase['total']}")
ok(len(phase["failed"]) == 2, "две записи отчитались отказом, а не исключением")
ok({f["code"] for f in phase["failed"]} == {"TEMPLATE_NOT_FOUND", "SCHEMA_INVALID"},
   f"коды отказов из реестра: {[f['code'] for f in phase['failed']]}")
ok(all(m["file_id"] for m in phase["materialized"]), "file_id из tables_pending доехал до результата")
ok((Path(ws2) / "networks" / "n2" / "network_config.xlsx").exists(),
   "отказ соседней книги не помешал материализации следующей")

print("== 6. Контракт с structure_create: pending РЕАЛЬНОГО движка шаблонов ==")
from core.engine import TemplateEngine
from core.ids import IDGenerator

ws3 = tempfile.mkdtemp(prefix="tm3_")
tpl = TemplateEngine(ws3, IDGenerator(), ROOT / "config" / "templates" / "workspace")
node = tpl.create_node("network", "net_A")
pending_real = node["tables_pending"]
ok(len(pending_real) >= 2, f"structure_create отложил книги сетки: {len(pending_real)}")
ok(any(p["table_template"] == "network_config" for p in pending_real),
   "среди отложенного есть network_config (единственная заведённая схема)")

phase_real = TableMaterializer(ExcelEngine(ws3), SCHEMAS).materialize_pending(pending_real)
ok(phase_real["created"] == 1, f"материализована ровно 1 книга — та, чья схема заведена ({phase_real['created']})")
ok(phase_real["materialized"] and (Path(ws3) / phase_real["materialized"][0]["path"]).exists(),
   "книга легла ровно по пути из tables_pending")
ok(all(f["code"] == "TEMPLATE_NOT_FOUND" for f in phase_real["failed"]),
   "книги без схемы честно отчитались TEMPLATE_NOT_FOUND (G16: не молчаливый успех)")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
