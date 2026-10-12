"""
tests/quick/test_table_materializer.py — A1′: материализация книг по .schema.yaml (фаза ТАБЛИЦЫ).

Проверяет: e2e proof-схема network_config → .xlsx (листы/столбцы/enum-валидация/формулы),
контракт ошибок (нет схемы, битая схема, книга уже есть), фаза по tables_pending
(отказ одной книги не роняет соседние).
"""
import re
import sys
import tempfile
import warnings
from pathlib import Path

import openpyxl

warnings.simplefilter("error", UserWarning) # чужой код ошибки → падение

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.engine import TableMaterializer, TableMaterializerError
from core.excel import ExcelError
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

print("== 0. Дверь книги отдаёт модель: читатель получает неизменяемую схему, а не словарь ==")
# Перед e2e: дверь, отдавшая словарь, уронила бы §1 падением, а не названной проверкой.
from core.declaration_schemas import BookSchema
try:
    _door0 = mat.load_schema("network_config")
except Exception as _e0:  # мутант двери: падение вместо модели видно проверке
    _door0 = _e0
ok(isinstance(_door0, BookSchema) and BookSchema.model_config.get("frozen") is True,
   f"load_schema отдаёт неизменяемую модель BookSchema ({type(_door0).__name__})")

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
_broken_dir = Path(tempfile.mkdtemp(prefix="schemas_broken_"))
(_broken_dir / "b.schema.yaml").write_text("sheets: [name: x\n", encoding="utf-8")
try:
    TableMaterializer(ExcelEngine(Path(tempfile.mkdtemp(prefix="ws_broken_"))), _broken_dir).load_schema("b")
    ok(False, "битый YAML схемы книги должен давать отказ двери")
except TableMaterializerError as e:
    ok(e.code == "DECLARATION_INVALID" and "yaml_syntax" in e.facts.get("details", ""),
       f"битый YAML схемы → отказ двери с местом ошибки, а не сырое исключение ({e.code}, {e.facts})")

def _book_refusal(text: str, rules: str = "") -> tuple[str, str]:
    """Код и детали отказа на схему книги `text` (и общие правила `rules`, если даны); любое иное исключение — его имя:
    проверка назовёт, а не упадёт."""
    probe_dir = Path(tempfile.mkdtemp(prefix="schemas_probe_"))
    (probe_dir / "p.schema.yaml").write_text(text, encoding="utf-8")
    if rules:
        (probe_dir / "_defaults.yaml").write_text(rules, encoding="utf-8")
    try:
        TableMaterializer(ExcelEngine(Path(tempfile.mkdtemp(prefix="ws_probe_"))), probe_dir).materialize("p", "p.xlsx")
        return "", ""
    except TableMaterializerError as e:
        return e.code, e.facts.get("details", "")
    except Exception as e:  # мутант снял проверку схемы — падение вместо отказа видно проверке
        return type(e).__name__, str(e)


_no_sheets = _book_refusal("book: broken\nlevel: video\nsource: test\nsheets: []\n")
ok(_no_sheets[0] == "DECLARATION_INVALID" and "sheets" in _no_sheets[1],
   f"схема без листов → отказ двери схемой книги с путём sheets ({_no_sheets})")
_bad_type = _book_refusal("book: b\nlevel: video\nsource: test\nsheets:\n  - name: s\n    columns:\n"
                          "      - {name: c, type: strnig, flag: W}\n")
ok(_bad_type[0] == "DECLARATION_INVALID" and "sheets.0.columns.0.type" in _bad_type[1],
   f"неизвестный тип столбца → отказ двери с путём столбца, а не столбец без правила деградации ({_bad_type})")

# Род порчи → путь ключа, который обязан назвать отказ двери: у каждого обязательного ключа схемы своя строка.
_SHEET = "sheets:\n  - name: s\n    columns:\n      - {name: c, type: string, flag: W}\n"
_BROKEN_BOOKS = {
    "неизвестный флаг столбца": ("book: b\nlevel: video\nsource: test\nsheets:\n  - name: s\n    columns:\n"
                                 "      - {name: c, type: string, flag: X}\n", "", "sheets.0.columns.0.flag"),
    "книга без level": ("book: b\nsource: test\n" + _SHEET, "", "level (missing)"),
    "книга без source": ("book: b\nlevel: video\n" + _SHEET, "", "source (missing)"),
    "опечатка типа в общих правилах": ("book: b\nlevel: video\nsource: test\n" + _SHEET, "degrade: {flaot: 0}\n",
                                       "degrade.flaot"),
}
# Имя листа судит библиотека записи книги, а не число в коде. Независимая истина события — документация формата:
# Microsoft «Rename a worksheet» — имя листа не длиннее 31 символа и без / \ ? * : [ ].
_DOC_TITLE = 31
_BOOK_HEAD = "book: b\nlevel: video\nsource: test\n"
_BROKEN_BOOKS["имя листа длиннее документированного предела"] = (
    _BOOK_HEAD + _SHEET.replace("name: s", "name: " + "Л" * (_DOC_TITLE + 1)), "", "sheets.0.name (xlsx_sheet_title)")
_BROKEN_BOOKS["запретный знак в имени листа"] = (
    _BOOK_HEAD + _SHEET.replace("name: s", "name: 'a/b'"), "", "sheets.0.name (xlsx_sheet_title)")
for _case, (_text, _rules, _path) in _BROKEN_BOOKS.items():
    _got = _book_refusal(_text, _rules)
    ok(_got[0] == "DECLARATION_INVALID" and _path in _got[1], f"{_case} → отказ двери с путём {_path} ({_got})")
_edge = _book_refusal(_BOOK_HEAD + _SHEET.replace("name: s", "name: " + "Л" * _DOC_TITLE))
ok(_edge == ("", ""), f"имя листа ровно на документированном пределе проходит дверь и ложится в книгу ({_edge})")

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
   "книги без схемы честно отчитались TEMPLATE_NOT_FOUND не молчаливый успех)")

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
   "книги без декларации отложены честно, а не «созданы»")
ok(any(f.type == "TableMaterialized" for f in res.facts),
   "факт TableMaterialized доехал в контракт (тип заведён в KNOWN_FACT_TYPES)")


print("== 7b. Строки-дефолты из схемы доезжают В КНИГУ, а не только в декларацию ==")
ws5 = tempfile.mkdtemp(prefix="tm5_")
r5 = new_materializer(ws5).materialize("channel_data", "ch.xlsx")
wb5 = openpyxl.load_workbook(Path(ws5) / "ch.xlsx")
# Считаем по самой схеме: новый провайдер добавляет строки (фон и апскейл), и сверка с
# числом, вписанным сюда однажды, ловила бы не потерю дефолтов, а факт их добавления.
import yaml
_declared_rows = sum(len(s.get("rows") or []) for s in yaml.safe_load(
    (ROOT / "config/templates/tables/channel_data.schema.yaml").read_text(encoding="utf-8"))["sheets"])
ok(r5["rows_total"] == _declared_rows,
   f"все строки-дефолты схемы доехали в книгу ({r5['rows_total']} из {_declared_rows})")
_sp = wb5["SCENE_PROFILE"]
_vals = {row[0]: row[1] for row in _sp.iter_rows(min_row=2, values_only=True) if row[0]}
_sp_declared = [s for s in yaml.safe_load(
    (ROOT / "config/templates/tables/channel_data.schema.yaml").read_text(encoding="utf-8"))["sheets"]
    if s["name"] == "SCENE_PROFILE"][0]["rows"]
ok(len(_vals) == len(_sp_declared),
   f"SCENE_PROFILE: строк на листе столько же, сколько объявлено ({len(_vals)} из {len(_sp_declared)})")
ok(_vals.get("sound") is False and _vals.get("layer_bg") is True,
   "значения дефолтов легли как есть: выключенный тип остался выключенным (тихий столбец)")
ok(_vals.get("layer_character_variants") is False,
   "вариативность персонажа приезжает в канал ВЫКЛЮЧЕННОЙ: включает человек, а не установка")
_wf = wb5["WORKFLOW_SEQUENCES"]
_hdr = [c.value for c in _wf[1]]
_first = next(_wf.iter_rows(min_row=2, values_only=True), ())     # строк может не быть вовсе
_row2 = {_hdr[i]: v for i, v in enumerate(_first)}
ok(_row2.get("allowed_next_tools") == "prepare_tts_input,trigger_tts_generation,human_approve",
   "список в ячейке — через запятую, значения не потеряны")
ok(isinstance(_row2.get("requires_human_approval"), bool),
   "булево осталось булевым, а не строкой 'False'")

print("== 7c. F30: формулы деградируют на неполных данных, а не ломаются ==")
import shutil as _shutil2
import yaml as _y2

_sch30 = Path(tempfile.mkdtemp(prefix="deg_"))
_shutil2.copy(SCHEMAS / "_defaults.yaml", _sch30 / "_defaults.yaml")
(_sch30 / "probe.schema.yaml").write_text(_y2.safe_dump({
    "book": "probe", "level": "video", "source": "test",
    "sheets": [{"name": "CALC", "columns": [
        {"name": "views", "type": "integer", "flag": "W"},
        {"name": "likes", "type": "integer", "flag": "W"},
        {"name": "like_rate", "type": "float", "flag": "F", "formula": "=B2/A2*100"},
        {"name": "verdict", "type": "string", "flag": "F", "formula": "=B2/A2"},
        {"name": "note", "type": "string", "flag": "F", "formula": "=B2/A2", "on_empty": "НЕТ ДАННЫХ"},
    ]}]}, allow_unicode=True), encoding="utf-8")
_ws30 = tempfile.mkdtemp(prefix="deg_ws_")
_e30 = ExcelEngine(_ws30)
_r30 = TableMaterializer(_e30, _sch30).materialize("probe", "p.xlsx")
ok(_r30["formulas_guarded"] == 3, f"объявленные формулы обёрнуты (получено {_r30['formulas_guarded']})")
_f30 = [c.value for c in openpyxl.load_workbook(Path(_ws30) / "p.xlsx")["CALC"][2]]
ok(all(str(v).startswith("=IFERROR(") for v in _f30[2:]), f"в книгу легла защищённая формула: {_f30[2]}")

# Пересчёт РЕАЛЬНЫМ движком: без него это была бы проверка строки, а не поведения.
try:
    _calc30 = _e30._recalc_via_lo(Path(_ws30) / "p.xlsx")
    _vals30 = [c.value for c in openpyxl.load_workbook(_calc30, data_only=True)["CALC"][2]]
    ok(_vals30[2] == 0, f"деление на пустое → 0 для float, а не #DIV/0! (получено {_vals30[2]!r})")
    ok(_vals30[3] == "PENDING", f"строковый столбец → PENDING (получено {_vals30[3]!r})")
    ok(_vals30[4] == "НЕТ ДАННЫХ", f"on_empty столбца перекрывает правило типа (получено {_vals30[4]!r})")
    # Контроль: та же формула БЕЗ деградации обязана дать ошибку — иначе проверка холостая.
    _wsc = tempfile.mkdtemp(prefix="deg_ctl_")
    _ec = ExcelEngine(_wsc)
    _ec.create_workbook("c.xlsx", sheet="CALC")
    for _c in ("views", "likes"):
        _ec.add_column("c.xlsx", "CALC", _c)
    _ec.add_column("c.xlsx", "CALC", "like_rate", formula="=B2/A2*100")
    _ctl = [c.value for c in openpyxl.load_workbook(_ec._recalc_via_lo(Path(_wsc) / "c.xlsx"),
                                                    data_only=True)["CALC"][2]]
    ok(_ctl[2] == "#DIV/0!", f"контроль: без деградации та же формула даёт #DIV/0! (получено {_ctl[2]!r})")
except ExcelError as _e:
    print(f"    (пересчёт недоступен: {_e.code} — проверки значений пропущены)")

# Правила деградации — декларация, а не код: в движке нет ни одного зашитого запасного значения.
_src30 = (ROOT / "core/engine/table_materializer.py").read_text(encoding="utf-8")
ok("PENDING" not in _src30, "запасные значения не зашиты в код — только читаются из _defaults.yaml")

print("== 7d. Приоритет запасного значения — как объявлено: on_empty столбца → degrade книги → _defaults.yaml ==")
# Истина — по построению: формула в ячейке несёт то запасное значение, которое объявлено ближе всех к столбцу;
# пересчёт не нужен, проверка не зависит от LibreOffice.
_sch7d = Path(tempfile.mkdtemp(prefix="deg7d_"))
_shutil2.copy(SCHEMAS / "_defaults.yaml", _sch7d / "_defaults.yaml")
(_sch7d / "p.schema.yaml").write_text(_y2.safe_dump({
    "book": "p", "level": "video", "source": "test", "degrade": {"string": "КНИГА"},
    "sheets": [{"name": "CALC", "columns": [
        {"name": "a", "type": "integer", "flag": "W"},
        {"name": "rate", "type": "float", "flag": "F", "formula": "=1/A2"},
        {"name": "label", "type": "string", "flag": "F", "formula": "=1/A2"},
        {"name": "note", "type": "string", "flag": "F", "formula": "=1/A2", "on_empty": "СВОЁ"},
    ]}]}, allow_unicode=True), encoding="utf-8")
_ws7d = tempfile.mkdtemp(prefix="deg7d_ws_")
TableMaterializer(ExcelEngine(_ws7d), _sch7d).materialize("p", "p.xlsx")
_f7d = dict(zip(("a", "rate", "label", "note"),
                (c.value for c in openpyxl.load_workbook(Path(_ws7d) / "p.xlsx")["CALC"][2])))
_rules7d = _y2.safe_load((SCHEMAS / "_defaults.yaml").read_text(encoding="utf-8"))["degrade"]
ok(_f7d["rate"] == f"=IFERROR(1/A2,{_rules7d['float']})",
   f"столбец без своего значения и без правила книги — общее правило типа ({_f7d['rate']})")
ok(_f7d["label"] == '=IFERROR(1/A2,"КНИГА")', f"degrade книги перекрывает общее правило того же типа ({_f7d['label']})")
ok(_f7d["note"] == '=IFERROR(1/A2,"СВОЁ")', f"on_empty столбца перекрывает и книгу, и общее правило ({_f7d['note']})")

print("== 8. Конвертер спека→схема: что разобрано, а что честно отдано человеку ==")
import importlib.util

_spec = importlib.util.spec_from_file_location("s2s", ROOT / "scripts" / "spec_to_schema.py")
s2s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s2s)

ok(s2s.verify() == 0, "разбор сходится с собранной РУКАМИ network_config (приёмка конвертера)")

# Пометка новизны рядом с флагом (`F 🆕`) не должна его съедать: пока съедала, вычисляемые столбцы
# приезжали записываемыми, а `variation_id` — без `id`. Проверяется и разбор, и результат на диске:
# сходимость спека↔схема, потому что схемы регенерируются не при каждой правке спеки.
_probe_flags = {"F 🆕": "F", "id 🆕": "id", "fk 🆕": "fk", "W 🆕": "W", "F": "F", "": "W"}
for _cell, _want in _probe_flags.items():
    _body = ("## Лист 1: `PROBE`\n\n| Столбец | Тип | Флаг | Прим. |\n|---|---|---|---|\n"
             f"| `col_a` | string | {_cell} | без формулы |\n")
    _p = Path(tempfile.mkdtemp(prefix="s2sflag_")) / "probe.schema.md"
    _p.write_text(_body, encoding="utf-8")
    _got = s2s.parse_spec(_p)["sheets"][0]["columns"][0]["flag"]
    ok(_got == _want, f"флаг «{_cell or '—'}» разобран как {_want} (получено {_got})")

_lost_flags: list[str] = []
for _sp in sorted((ROOT / "docs/roadmap/spec/schemas").glob("*.schema.md")):
    _y = ROOT / "config/templates/tables" / _sp.name.replace(".schema.md", ".schema.yaml")
    if not _y.exists():
        continue
    # Ключ — ПАРА «лист + столбец»: одно имя живёт в разных листах с разными ролями
    # (`thumbnail_url` — `W` у своего листа-хозяина и `F`-зеркало рядом с метриками),
    # и плоский ключ по имени склеил бы их в ложное расхождение.
    _want_f: dict[tuple[str, str], str] = {}
    _sheet = ""
    for _line in _sp.read_text(encoding="utf-8").splitlines():
        _h = re.match(r"^##\s*Лист[ыа]?\s*[\d–\-—, ]*:\s*`([^`]+)`", _line)
        if _h:
            _sheet = _h.group(1)
            continue
        _m = re.match(r"^\| *`([a-z][a-z0-9_]*)` *\| *[^|]*\| *(F|id|fk) 🆕", _line)
        if _m and _sheet:
            _want_f[(_sheet, _m.group(1))] = _m.group(2)
    _have_f = {(sh["name"], c["name"]): c.get("flag")
               for sh in yaml.safe_load(_y.read_text(encoding="utf-8"))["sheets"] for c in sh["columns"]}
    _lost_flags += [f"{_sp.stem}.{s}.{n}: спека {f}, схема {_have_f.get((s, n))}"
                    for (s, n), f in _want_f.items() if _have_f.get((s, n)) != f]
ok(not _lost_flags, f"объявленный в спеке флаг дожил до схемы: расходятся {_lost_flags[:4]}")

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


print("== 9. Инструмент фазы ТАБЛИЦЫ (F76: падал NameError при ЛЮБОМ вызове) ==")
import asyncio as _aio
import yaml as _yaml
from core.engine import Engine as _Eng
from core.ids import IDGenerator as _IDG
from core.state import StateManager as _SM
from tests.harness.inprocess import register_basic_tools as _register

_ws9 = Path(tempfile.mkdtemp(prefix="tm9_")) / "workspace"
_ws9.mkdir(parents=True)
_sm9 = _SM(_ws9)
_eng9 = _Eng(state_manager=_sm9)
_register(_eng9, _IDG(), _sm9)


def _call9(**params):
    return _aio.run(_eng9.call("structure_materialize_tables", params))


# Ядро материализатора было покрыто (§5–§7), а обёртка — нет: она звала имя, которого в ней нет.
_r9 = _call9(pending=[{"path": "networks/n1/network_config.xlsx",
                       "table_template": "network_config", "file_id": "F1"}])
ok(_r9.status == "success",
   f"инструмент отработал, а не упал на собственной проводке ({_r9.error.code if _r9.error else 'ok'})")
ok((_ws9 / "networks" / "n1" / "network_config.xlsx").is_file(),
   "книга легла на диск ЧЕРЕЗ инструмент, а не только через ядро")
ok(_r9.facts and _r9.facts[0].type == "TableMaterialized", "результат пришёл фактом контракта")

# mode=custom: схемы берутся из `.templates/` ПО АДРЕСУ книги. Без режима сюда молча приезжали
# бы серверные схемы — проект, который свои завёл, не узнал бы, что они не сработали.
_proj9 = _ws9 / "networks" / ".templates" / "tables"
_proj9.mkdir(parents=True)
(_proj9 / "network_config.schema.yaml").write_text(_yaml.safe_dump({
    "book": "network_config", "level": "network", "source": "test",
    "sheets": [{"name": "ТОЛЬКО_ПРОЕКТНЫЙ",
                "columns": [{"name": "col", "type": "string", "flag": "W"}]}]},
    allow_unicode=True), encoding="utf-8")
_r9c = _call9(pending=[{"path": "networks/n2/network_config.xlsx",
                        "table_template": "network_config"}], mode="custom")
ok(_r9c.status == "success"
   and _r9c.data["templates"]["networks/n2/network_config.xlsx"] == "project",
   f"сказано, ЧЬИ шаблоны сработали ({_r9c.data.get('templates') if _r9c.status == 'success' else _r9c.error.code})")
ok(_r9c.status == "success"
   and [s["sheet"] for s in _r9c.data["materialized"][0]["sheets"]] == ["ТОЛЬКО_ПРОЕКТНЫЙ"],
   "проектная схема ПРИМЕНЕНА, а не только названа")
_r9d = _call9(pending=[{"path": "networks/n3/network_config.xlsx",
                        "table_template": "network_config"}])
ok(_r9d.status == "success" and len(_r9d.data["materialized"][0]["sheets"]) > 1,
   "без режима действуют серверные схемы — проектные не подмешиваются сами")
_r9m = _call9(pending=[], mode="нечто")
ok(_r9m.status == "error" and _r9m.error.code == "VALIDATION_ERROR",
   f"неизвестный режим → отказ кодом реестра ({_r9m.error.code if _r9m.error else 'успех!'})")

print("== 10. Схему книги читает одна дверь: у КАЖДОГО читателя битая книга — отказ, а не строки словарём ==")
# Причина рождается в файле книги (копия боевых деклараций), следствие судится там, где его видит клиент: ответ
# инструмента или единственный перевод исключения в отказ (Engine.refusal). Читатель — строка таблицы, а не функция.
from core.montage.book import SceneBook
from core.montage.ledger import RenderLedger
from core.reactions import Reactions as _Reactions10
from tests.harness.live_server import declarations_copy
from tools import media as _media10, uniqueness as _uniq10
from tools._context import build_context

_REACT10 = _Reactions10(ROOT / "config" / "server_reactions.yaml")


def _books(state: str) -> Path:
    """Копия боевых деклараций: книги целы, у каждой книги первый столбец первого листа с неизвестным типом, книг нет,
    или у каждой книги листы переименованы (книга цела, но названных декларациями листов в ней нет)."""
    cfg = declarations_copy()
    for schema_file in (cfg / "templates" / "tables").glob("*.schema.yaml"):
        if state == "нет книг":
            schema_file.unlink()
        elif state in ("битые", "листы переименованы"):
            data = _yaml.safe_load(schema_file.read_text(encoding="utf-8"))
            if state == "битые":
                data["sheets"][0]["columns"][0]["type"] = "strnig"
            else:   # короткие имена: событие мерит ссылку на лист, а не длину имени (её судит библиотека записи)
                for index, sheet in enumerate(data["sheets"]):
                    sheet["name"] = f"Л{index}"
            schema_file.write_text(_yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return cfg


def _said(cfg: Path, call) -> tuple[str, str]:
    """(код, детали) того, что увидит клиент; успех — ("", ""); чужое исключение — его имя: проверка назовёт, а не упадёт."""
    ws = Path(tempfile.mkdtemp(prefix="tm10_")) / "workspace"
    (ws / "v0").mkdir(parents=True)
    (ws / "v0" / "read.json").write_text("{}", encoding="utf-8")
    sm = _SM(ws)
    eng = _Eng(reactions=_REACT10, state_manager=sm)
    try:
        res = call(cfg, ws, sm, eng)
    except Exception as e:  # мутант читателя: падение вместо отказа видно проверке
        refused = eng.refusal(e)
        if refused is None:
            return type(e).__name__, str(e)[:160]
        res = refused
    if getattr(res, "status", "success") == "error":
        return res.error.code, res.error.facts.get("details", "")
    return "", ""


def _tool(group, name: str, **params):
    def call(cfg, ws, sm, eng):
        group.register(eng, build_context(eng, _IDG(), sm, cfg))
        return _aio.run(eng.call(name, params))
    return call


_READERS = {
    "материализатор (TableMaterializer.materialize)":
        lambda cfg, ws, sm, eng: TableMaterializer(ExcelEngine(ws), cfg / "templates" / "tables").materialize(
            "channel_data", "ch.xlsx"),
    "монтаж: профиль рендера (SceneBook)":
        lambda cfg, ws, sm, eng: SceneBook(sm, cfg, lambda p: (ws / p).resolve()).profile("v0", "long_16x9"),
    "монтаж: строка результата (RenderLedger)":
        lambda cfg, ws, sm, eng: RenderLedger(sm, cfg, _IDG()).record("v0", {"stage": "draft"}),
    "media_provider_status": _tool(_media10, "media_provider_status", table="v0"),
    "uniqueness_check": _tool(_uniq10, "uniqueness_check", table="v0"),
}
_CFG10 = {state: _books(state) for state in ("целые", "битые", "нет книг", "листы переименованы")}
# Какой ключ своей декларации читатель судит по листам книги; материализатор листы не называет, а строит все.
_NAMES_SHEET = {"монтаж: профиль рендера (SceneBook)": "profile.sheet",
                "монтаж: строка результата (RenderLedger)": "target.sheet",
                "media_provider_status": "source.sheet", "uniqueness_check": "sources.profile.sheet"}
for _reader, _call10 in _READERS.items():
    _whole, _broken, _absent, _renamed = (_said(_CFG10[s], _call10)
                                          for s in ("целые", "битые", "нет книг", "листы переименованы"))
    ok(_whole == ("", ""), f"{_reader}: целая книга читается моделью, ответ — успех ({_whole})")
    ok(_broken[0] == "DECLARATION_INVALID" and "sheets.0.columns.0.type" in _broken[1],
       f"{_reader}: битая книга → отказ двери с путём ключа, а не строки мимо схемы ({_broken})")
    ok(_absent[0] == "TEMPLATE_NOT_FOUND",
       f"{_reader}: объявленной книги нет → TEMPLATE_NOT_FOUND, а не молчаливое «строк нет» ({_absent})")
    _key10 = _NAMES_SHEET.get(_reader)
    ok(_renamed == ("", "") if _key10 is None else
       _renamed[0] == "DECLARATION_INVALID" and f"{_key10} (literal_error)" in _renamed[1],
       f"{_reader}: названного листа в книге нет → " + (f"отказ с путём {_key10}, а не «строк нет»" if _key10 else
                                                         "материализатор строит листы книги, какими они объявлены")
       + f" ({_renamed})")

# Какую книгу судит строка результата — объявлено в montage.yaml `target.book`; ключа нет — отказ двери, а не книга
# по умолчанию из кода.
_no_target_book = declarations_copy()
_montage10 = _yaml.safe_load((_no_target_book / "montage.yaml").read_text(encoding="utf-8"))
del _montage10["target"]["book"]
(_no_target_book / "montage.yaml").write_text(_yaml.safe_dump(_montage10, allow_unicode=True, sort_keys=False),
                                              encoding="utf-8")
_unnamed = _said(_no_target_book, _READERS["монтаж: строка результата (RenderLedger)"])
ok(_unnamed[0] == "DECLARATION_INVALID" and "target.book" in _unnamed[1],
   f"книга строки результата не объявлена → отказ двери с путём target.book, а не книга из кода ({_unnamed})")

# Строки дефолта уходят читателю его копией (`list[dict]`, как до двери): читатель дополняет свою строку, объявление
# остаётся неизменяемым.
from core.contracts import ContractError as _CE10
from core.declaration import Declaration as _Decl10
from core.engine.table_materializer import declared_rows as _rows10, named_sheet as _sheet10

_montage_door = _Decl10(CFG / "montage.yaml", _CE10, "монтажа", "—")
_copy10 = _rows10(CFG, _montage_door, "scene", "roles")
try:
    _copy10[0]["_row_id"] = "R1"
    _augmented = _copy10[0].get("_row_id")
except TypeError as _e10:  # мутант отдал строки объявления: правка копии — TypeError
    _augmented = type(_e10).__name__
ok(_augmented == "R1" and "_row_id" not in _sheet10(CFG, _montage_door, "scene", "roles").rows[0],
   f"строки дефолта — копия читателя: дополнение легло в копию, объявление не тронуто ({_augmented})")

print("== 11. Лист, названный декларацией, объявлен книгой: суд у двери того, кто назвал ==")
# Ссылка на лист книги — отображение с ключами `sheet` и книгой (`fallback_book` или `book`). Ссылки берутся из самих
# деклараций, а не из списка в тесте: новая ссылка судится, не дожидаясь правки набора.
_BOOK_KEYS = ("fallback_book", "book")


def _references(cfg: Path) -> list[tuple[str, tuple[str, ...], str]]:
    found: list[tuple[str, tuple[str, ...], str]] = []

    def walk(node, file: str, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            key = next((k for k in _BOOK_KEYS if isinstance(node.get(k), str)), None)
            if key and isinstance(node.get("sheet"), str):
                found.append((file, path, key))
            for name, value in node.items():
                walk(value, file, (*path, str(name)))

    for decl in sorted(cfg.glob("*.yaml")):
        walk(_yaml.safe_load(decl.read_text(encoding="utf-8")), decl.name, ())
    return found


def _named(cfg: Path, file: str, path: tuple[str, ...], key: str) -> tuple[str, str]:
    """(код, детали) суда ссылки; успех — ("", имя листа); чужое исключение — его имя."""
    try:
        return "", _sheet10(cfg, _Decl10(cfg / file, _CE10, file, "—"), *path, book=key).name
    except _CE10 as e:
        return e.code, f"{e.facts.get('file')}: {e.facts.get('details')}"
    except Exception as e:  # мутант суда: падение вместо отказа видно проверке
        return type(e).__name__, str(e)[:120]


_REFS = _references(CFG)
_live11 = [(f, p, k, _named(CFG, f, p, k)) for f, p, k in _REFS]
ok(len(_REFS) >= 10 and all(code == "" for *_, (code, _) in _live11),
   f"живая конфигурация: каждая ссылка на лист судится и проходит ({sum(not c for *_, (c, _) in _live11)}/{len(_REFS)}: "
   f"{[(f, '.'.join(p)) for f, p, _, (c, _) in _live11 if c]})")


def _broken_refs(change) -> Path:
    """Копия деклараций, где у каждой ссылки на лист сделана правка `change(отображение, ключ книги)`."""
    cfg = declarations_copy()
    for file in sorted({f for f, _, _ in _REFS}):
        data = _yaml.safe_load((cfg / file).read_text(encoding="utf-8"))
        for _f, path, key in (r for r in _REFS if r[0] == file):
            node = data
            for part in path:
                node = node[part]
            change(node, key)
        (cfg / file).write_text(_yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return cfg


_undeclared = _broken_refs(lambda node, key: node.update(sheet=node["sheet"] + "_НЕ_ОБЪЯВЛЕН"))
_unnamed11 = _broken_refs(lambda node, key: node.pop(key))
for _file, _path, _key in _REFS:
    _at = ".".join(_path)
    _sick = _named(_undeclared, _file, _path, _key)
    ok(_sick[0] == "DECLARATION_INVALID" and _sick[1] == f"{_file}: {_at}.sheet (literal_error)",
       f"{_file} {_at}: лист, которого книга не объявляет → отказ двери с путём {_at}.sheet, а не «строк нет» ({_sick})")
    _gone = _named(_unnamed11, _file, _path, _key)
    ok(_gone[0] == "DECLARATION_INVALID" and _gone[1] == f"{_file}: {_at}.{_key} (missing)",
       f"{_file} {_at}: книга не названа → отказ двери с путём {_at}.{_key}, а не пропуск ({_gone})")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
