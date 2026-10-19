#!/usr/bin/env python3
"""tests/quick/test_snapshot_schema.py — снимок таблицы рождается со схемой листов из декларации книги, и запись судится ею.

Прежде схему листа снимка писали только тесты руками: рабочие писатели заводили лист с пустой схемой, и защита формул,
перечней и столбцов в `TableEngine._validate_write` не действовала. Книги не перечисляются — каталог резолвера; оракул
схемы — сама декларация, прочитанная YAML здесь, а не проекцией проверяемого кода. Следствие судится на двери движка
таблиц: неизвестный столбец, значение вне перечня, запись в формулу — отказ кодом; законная запись — в очередь.
"""
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.declaration import declarations_root
from core.engine import TableMaterializer
from core.engine.template_resolver import server_tables
from core.excel import ExcelEngine
from core.ids import IDGenerator
from core.state import StateManager
from core.tables import TableEngine, TableError

_checks = 0
_fails: list[str] = []


def check(label: str, cond: bool, detail: object = "") -> None:
    global _checks
    _checks += 1
    print(f"  {'✓' if cond else '✗'} {label}  {detail if not cond else ''}".rstrip())
    if not cond:
        _fails.append(label)


def expected(column: dict, degrade: dict) -> dict:
    """Столбец схемы снимка по декларации: тип, перечень; у вычисляемого (F с формулой) — формула и запасное значение
    (`on_empty` столбца, иначе `degrade` по типу: книга поверх `_defaults.yaml`)."""
    out = {"type": column["type"]}
    if column.get("enum"):
        out["enum"] = list(column["enum"])
    if column.get("format"):   # выражение формата — из `_defaults.yaml formats`, его судит запись
        out["pattern"] = FORMATS[column["format"]]["pattern"]
    if column.get("flag") == "F" and column.get("formula"):
        out |= {"computed": True, "formula": column["formula"],
                "fallback": column["on_empty"] if column.get("on_empty") is not None else degrade.get(column["type"])}
    if column.get("flag") == "id":   # ключ строки: двух строк с одним значением лист не держит
        out["key"] = True
    return out


def said(call) -> str:
    return refusal(call)[0]


def refusal(call) -> tuple[str, dict]:
    try:
        call()
        return "принято", {}
    except TableError as e:
        return e.code, dict(e.facts)


TABLES = server_tables(declarations_root())
_RULES = yaml.safe_load((TABLES / "_defaults.yaml").read_text(encoding="utf-8"))
DEFAULTS, FORMATS = _RULES.get("degrade", {}), _RULES.get("formats", {})
work = Path(tempfile.mkdtemp(prefix="snapshot_schema_"))
try:
    print("== 1. Каждая книга: схема каждого листа снимка — проекция её декларации, прежние строки целы ==")
    probes: dict[str, tuple[str, str, dict]] = {}
    for path in sorted(TABLES.glob("*.schema.yaml")):
        name = path.name.removesuffix(".schema.yaml")
        declared = yaml.safe_load(path.read_text(encoding="utf-8"))
        entity = work / name
        entity.mkdir()
        first = declared["sheets"][0]["name"]
        (entity / "read.json").write_text(
            '{"%s": {"schema": {}, "rows": {"R0": {"было": 1}}}}' % first, encoding="utf-8")
        engine = TableEngine(StateManager(work), IDGenerator())
        TableMaterializer(ExcelEngine(work), TABLES, engine).materialize(name, f"{name}/{name}.xlsx")
        snapshot = engine.load_snapshot(name)
        formulas = any(c.get("flag") == "F" and c.get("formula") for s in declared["sheets"] for c in s["columns"])
        for sheet in declared["sheets"]:
            want = {c["name"]: expected(c, DEFAULTS | declared.get("degrade", {})) for c in sheet["columns"]}
            got = (snapshot.get(sheet["name"]) or {}).get("schema") or {}
            check(f"{name}.{sheet['name']}: схема снимка = декларация ({len(want)} столбцов)", got == want,
                  [(k, want.get(k), got.get(k)) for k in sorted(set(want) | set(got)) if want.get(k) != got.get(k)][:2])
            check(f"{name}.{sheet['name']}: лист-запись {bool(sheet.get('record'))} — как в декларации",
                  bool((snapshot.get(sheet["name"]) or {}).get("record")) == bool(sheet.get("record")))
            for column in sheet["columns"]:
                if column.get("enum") and "enum" not in probes:
                    probes["enum"] = (name, sheet["name"], column)
                if column.get("flag") == "F" and column.get("formula") and "computed" not in probes:
                    probes["computed"] = (name, sheet["name"], column)
                if column.get("flag") == "W" and column.get("type") == "string" and "legal" not in probes:
                    probes["legal"] = (name, sheet["name"], column)
                # Ключ — в книге без формул: применение очереди не зовёт движок пересчёта.
                if (column.get("flag") == "id" and column.get("type") == "string" and not column.get("format")
                        and not formulas and "key" not in probes):
                    probes["key"] = (name, sheet["name"], column)
        check(f"{name}: прежняя строка листа {first} цела", snapshot[first]["rows"].get("R0") == {"было": 1},
              snapshot[first].get("rows"))

    print("== 2. Запись судится схемой: отказ кодом на двери движка, законное — в очередь ==")
    engine = TableEngine(StateManager(work), IDGenerator())
    table, sheet, column = probes["enum"]
    check(f"значение вне перечня {table}.{sheet}.{column['name']} → ENUM_VIOLATION",
          said(lambda: engine.append(table, sheet, {column["name"]: "НЕТ_ТАКОГО"})) == "ENUM_VIOLATION")
    table, sheet, column = probes["computed"]
    check(f"запись в формулу {table}.{sheet}.{column['name']} → COMPUTED_READONLY",
          said(lambda: engine.append(table, sheet, {column["name"]: 1})) == "COMPUTED_READONLY")
    table, sheet, column = probes["legal"]
    check(f"столбец вне декларации листа {table}.{sheet} → COLUMN_NOT_FOUND",
          said(lambda: engine.append(table, sheet, {"нет_такого_столбца": "x"})) == "COLUMN_NOT_FOUND")
    check(f"законная запись {table}.{sheet}.{column['name']} → в очередь",
          said(lambda: engine.append(table, sheet, {column["name"]: "текст"})) == "принято")

    print("== 3. Ключ строки (flag: id): занятое значение — отказ с ID строки, что его держит; своя строка, пустой ключ — законно ==")
    check("в декларациях есть строковый ключ книги без формул", "key" in probes, sorted(probes))
    table, sheet, column = probes["key"]
    key = column["name"]
    engine = TableEngine(StateManager(work), IDGenerator())
    with engine.state.edit_snapshot(table) as snap:   # строка с ключом в снимке — засев мимо двери
        snap[sheet]["rows"]["R-KEY"] = {key: "К-0"}
    code, facts = refusal(lambda: engine.append(table, sheet, {key: "К-0"}))
    check(f"append ключа строки снимка {table}.{sheet}.{key} → ROW_KEY_EXISTS с её ID",
          (code, facts.get("row_id"), facts.get("column"), facts.get("value")) == ("ROW_KEY_EXISTS", "R-KEY", key, "К-0"),
          (code, facts))
    queued = engine.append(table, sheet, {key: "К-1"})["row_id"]
    check("append нового ключа → в очередь", queued != "R-KEY")
    code, facts = refusal(lambda: engine.append(table, sheet, {key: "К-1"}))
    check("append ключа из ещё не применённой очереди → ROW_KEY_EXISTS с ID строки очереди",
          (code, facts.get("row_id")) == ("ROW_KEY_EXISTS", queued), (code, facts))
    check("пустой ключ не судится: две строки без ключа приняты",
          [said(lambda: engine.append(table, sheet, {key: ""})) for _ in range(2)] == ["принято"] * 2)
    code, facts = refusal(lambda: engine.update(table, sheet, "R-KEY", {key: "К-1"}))
    check("update на ключ строки очереди → ROW_KEY_TAKEN с её ID", (code, facts.get("row_id")) == ("ROW_KEY_TAKEN", queued),
          (code, facts))
    code, facts = refusal(lambda: engine.set(table, sheet, "R-KEY", key, "К-1"))
    check("set на занятый ключ → ROW_KEY_TAKEN с ID строки, что его держит",
          (code, facts.get("row_id")) == ("ROW_KEY_TAKEN", queued), (code, facts))
    check("update строки её же ключом — законно", said(lambda: engine.update(table, sheet, "R-KEY", {key: "К-0"})) == "принято")
    check("set строки её же ключом — законно", said(lambda: engine.set(table, sheet, "R-KEY", key, "К-0")) == "принято")
    # Второй писатель мимо двери: дубль в очереди снимается на применении той же сверкой.
    engine.state.push_to_queue(table, {"action": "append", "sheet": sheet, "row_id": "R-DUP", "data": {key: "К-0"}})
    applied = engine.execute_queue(table)
    rows = engine.load_snapshot(table)[sheet]["rows"]
    check("дубль ключа мимо двери → на применении пропущен ROW_KEY_EXISTS, строки нет, прочее применено",
          [s["code"] for s in applied["skipped"] if s["op"].get("row_id") == "R-DUP"] == ["ROW_KEY_EXISTS"]
          and "R-DUP" not in rows and rows.get(queued) == {key: "К-1"}, (applied["skipped"], sorted(rows)))
finally:
    shutil.rmtree(work, ignore_errors=True)

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for _f in _fails:
        print(f"  - {_f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
