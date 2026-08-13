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
   "среди отложенного есть network_config")

phase_real = TableMaterializer(ExcelEngine(ws3), SCHEMAS).materialize_pending(pending_real)
# Ожидание выводим из диска: заведённых схем становится больше по мере авторинга.
_with_schema = sum(1 for p in pending_real if (SCHEMAS / f"{p['table_template']}.schema.yaml").exists())
ok(phase_real["created"] == _with_schema,
   f"материализованы ровно книги с заведённой схемой ({phase_real['created']} из {len(pending_real)})")
ok(phase_real["materialized"] and (Path(ws3) / phase_real["materialized"][0]["path"]).exists(),
   "книга легла ровно по пути из tables_pending")
ok(all(f["code"] == "TEMPLATE_NOT_FOUND" for f in phase_real["failed"]),
   "книги без схемы честно отчитались TEMPLATE_NOT_FOUND (G16: не молчаливый успех)")

print("== 7. Директива владельца: клиент передаёт ИМЕНА, книги делает сервер ==")
import asyncio
from core.engine import Engine
from core.ids import LinkRegistry
from core.reactions import Reactions
from core.state import StateManager
from tools._context import ToolContext
from tools import structure as structure_group

ws4 = Path(tempfile.mkdtemp(prefix="tm4_"))
CFG = ROOT / "config"
sm = StateManager(ws4)
ids = IDGenerator()
eng = Engine(reactions=Reactions(CFG / "server_reactions.yaml"), state_manager=sm)
ctx = ToolContext(eng, ids, sm, None, ExcelEngine(ws4),
                  TemplateEngine(ws4, ids, CFG / "templates" / "workspace"),
                  LinkRegistry(ws4), ws4, CFG)
structure_group.register(eng, ctx)

res = asyncio.run(eng.tools["structure_create"].handler(
    type="network", name="fit_net",
    children={"channel": ["ch_A"], "competitor_channel": [f"comp_{i}" for i in range(20)]}))
data = res.data
ok(res.status == "success", "пакетное создание (сетка + канал + 20 конкурентов) одним вызовом")
ok(len(list(ws4.rglob("*.xlsx"))) == len(data["tables_materialized"]),
   "книг на диске ровно столько, сколько отчитано материализованными")
ok(data["tables_materialized"] and data["tables_materialized"][0]["book"] == "network_config",
   "книга создана у созданной сущности (сетка), без участия клиента")
ok(not list(ws4.rglob("videos/*/")),
   "видео не названы → сущностей видео нет, книг видео тоже нет (умное создание)")
ok(all(f["code"] == "TEMPLATE_NOT_FOUND" for f in data["tables_deferred"]),
   "книги без декларации отложены честно, а не «созданы» (G16)")
ok(any(f.type == "TableMaterialized" for f in res.facts),
   "факт TableMaterialized доехал в контракт (тип заведён в KNOWN_FACT_TYPES, D25)")


print("== 8. Конвертер спека→схема: что разобрано, а что честно отдано человеку ==")
import importlib.util

_spec = importlib.util.spec_from_file_location("s2s", ROOT / "scripts" / "spec_to_schema.py")
s2s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s2s)

ok(s2s.verify() == 0, "разбор сходится с собранной РУКАМИ network_config (приёмка конвертера)")

specs = Path(tempfile.mkdtemp(prefix="s2s_"))


def parse(body: str) -> dict:
    p = specs / "probe.schema.md"
    p.write_text(body, encoding="utf-8")
    return s2s.parse_spec(p)


def cols_of(res: dict, i: int = 0) -> list[dict]:
    """Столбцы листа, не роняя прогон: при мутации листа может не быть вовсе."""
    return res["sheets"][i]["columns"] if len(res["sheets"]) > i else []


# Диапазон в ДВУХ парах кавычек раньше проходил немо: имя бралось из первой пары,
# многоточие оставалось в хвосте заголовка, и 7 листов схлопывались в один без слова.
r = parse("## Листы 4–10: `ACT_1_HOOK` … `ACT_7_TRUTH` (7 идентичных)\n\n"
          "`act_name, text` — `W`.\n")
ok(not r["sheets"], "диапазон листов не превращается в один лист")
ok(any("не называет их поимённо" in w for w in r["warnings"]),
   "диапазон без поимённого перечисления — предупреждение, а не догадка об именах")

# Составное имя разворачивается ТОЛЬКО потому, что спека сама называет листы в «Дельтах».
r = parse("## Листы 5–6: `VISUAL_/SCRIPT_LIB` (шаблон)\n\n"
          "**Общие:** `solution_id (id), notes` — всё `W`.\n\n"
          "**Дельты:**\n"
          "- `VISUAL_LIB` (5): + `visual_solution`.\n"
          "- `SCRIPT_LIB` (6): + `pattern_description`.\n")
ok([s["name"] for s in r["sheets"]] == ["VISUAL_LIB", "SCRIPT_LIB"],
   "группа разворачивается в листы, ПОИМЕННО названные спекой")
ok([c["name"] for c in cols_of(r)] == ["solution_id", "notes", "visual_solution"],
   "у листа группы общие столбцы + собственная дельта")
ok(bool(cols_of(r)) and cols_of(r)[0]["flag"] == "id", "флаг из аннотации `(id)` доехал")

# Прозаическая форма: имена и флаги спека объявляет, тип — нет.
r = parse("## Лист 1: `META`\n\n"
          "`video_id (id), title, channel_id (fk), tier (HIGH/MED/LOW)` — `W`;\n"
          "`type` = `F` (фикс.).\n")
cols = {c["name"]: c for c in cols_of(r)}
ok(list(cols) == ["video_id", "title", "channel_id", "tier", "type"],
   "прозаический список даёт столбцы, включая одиночный `type` = `F` вне списка")
ok(cols.get("channel_id", {}).get("flag") == "fk" and cols.get("type", {}).get("flag") == "F",
   "флаги из прозы верны")
ok(cols.get("tier", {}).get("type") == "enum" and cols.get("tier", {}).get("enum") == ["HIGH", "MED", "LOW"],
   "enum-значения из скобок, а не выдуманные")
ok(bool(r["sheets"]) and r["sheets"][0]["prose"], "лист помечен как собранный из прозы (тип не объявлен спекой)")

# Не выдумываем: составной столбец `a_x/y` — это ДВА столбца, развести может только человек.
r = parse("## Лист 1: `S`\n\n`scene_id (id), color_primary/secondary, notes` — `W`.\n")
names = [c["name"] for c in cols_of(r)]
ok("color_primary/secondary" not in names and "color_primary" not in names,
   "составной столбец через `/` не разводится машиной и не попадает в схему как есть")
ok(any("не приняты за имена столбцов" in w for w in r["warnings"]),
   "отброшенный токен назван поимённо — это адрес для вычитки")

# Дашборд из секций собирается, но полнота набора под вопросом → сервер обязан сказать.
r = parse("## Лист 9: `ANALYTICS` — дашборд (Read-Only)\n\n"
          "> Весь `F`. Секции: **Топ** (`metric, delta`) · **Прочее** (trend, our_status).\n")
ok([c["flag"] for c in cols_of(r)] == ["F", "F"],
   "лист, объявленный целиком Read-Only, даёт столбцы с флагом F")
ok(any("проверить полноту" in w for w in r["warnings"]),
   "секционный дашборд помечен как возможно неполный, а не выдан за полный")


print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
