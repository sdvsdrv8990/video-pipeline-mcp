"""core/tables/tables_core.py — слой ДАННЫХ таблиц поверх состояния рабочей области.

Форму книги не трогает — это соседний движок структуры; в `.xlsx` ничего не пишет, и отсутствие
синка честно помечено в ответе. Единица хранения — строка по ID, столбец лишь проекция поверх
строк. Запись идёт ТОЛЬКО через очередь, чтения в неё не кладутся. Значение вне объявленного
перечня и запись в вычисляемый столбец отбиваются здесь: сервер пишет в обход дропдауна книги, и
проверить это больше некому. Состояния между вызовами ядро не помнит, факты возвращает
вызывающему.
"""

from __future__ import annotations
import re
from datetime import date
from typing import Literal, get_args

from core.contracts import ContractError
from core.formulas import computed, declared_language

# Действия, которые можно класть в очередь (пишущие примитивы).
# get_column/get_row — чтения, в очередь НЕ кладутся (ИНСТРУКЦИЯ §4).
# Перечень объявлен ОДИН раз: и проверка входа, и обещание клиенту в описании инструмента берут
# его отсюда, а не переписывают рядом прозой, которая разойдётся молча.
QueueAction = Literal["set", "update", "append", "delete"]
QUEUEABLE_ACTIONS: tuple[QueueAction, ...] = get_args(QueueAction)


class TableError(ContractError):
    """Ошибка слоя данных. Несёт код из server_reactions.yaml + подсказку.

    Обёртка в server.py ловит и собирает ErrorDetail(code, message, recovery).
    """


class TableEngine:
    """Generic-движок данных таблиц.

    Один на категорию; различия между таблицами — только в схемах (нет
    `if table == ...`). Поведение задаётся схемой листа, не Python-кодом.

    Attributes:
        state: StateManager — доступ к read.json/write.json с containment.
        ids: IDGenerator — сервер присваивает ID новым строкам (table_append).
    """

    def __init__(self, state_manager, id_generator):
        self.state = state_manager
        self.ids = id_generator

    # ═══ Загрузка/навигация по снапшоту ═══

    def _load(self, table: str) -> dict:
        """Читает read.json. TABLE_NOT_FOUND если сущности/снапшота нет.

        Отказ двери пути (PathRefused) прокидывается наружу — клиент получает его код и факт `path`.
        """
        snapshot = self.state.read_snapshot(table)
        if snapshot is None:
            raise TableError("TABLE_NOT_FOUND", facts={"table": table})
        return snapshot

    def load_snapshot(self, table: str) -> dict:
        """Публичный read снапшота таблицы (для кросс-модульных читателей, напр. поиск)."""
        return self._load(table)

    def _sheet(self, snapshot: dict, sheet: str) -> dict:
        """Достаёт лист (`sheet_of`)."""
        return sheet_of(snapshot, sheet)

    @staticmethod
    def _rows(sheet_obj: dict) -> dict:
        return sheet_obj.get("rows", {})

    @staticmethod
    def _schema(sheet_obj: dict) -> dict:
        return sheet_obj.get("schema", {})

    @staticmethod
    def _column(schema: dict, sheet: str, *columns: str) -> None:
        """Столбцы есть в схеме листа — иначе COLUMN_NOT_FOUND с именами и листом; лист без схемы не судится."""
        unknown = [c for c in columns if schema and c not in schema]
        if unknown:
            raise TableError("COLUMN_NOT_FOUND", facts={"column": ", ".join(unknown), "sheet": sheet})

    @staticmethod
    def _row(rows: dict, sheet: str, row_id: str) -> None:
        """Строка есть в листе — иначе ROW_NOT_FOUND с ID и листом."""
        if row_id not in rows:
            raise TableError("ROW_NOT_FOUND", facts={"row": row_id, "sheet": sheet})

    # ═══ ЧТЕНИЕ (проекции) ═══

    def get_column(self, table: str, sheet: str, column: str) -> dict:
        """Проекция столбца: {row_id: value}. Точечно, экономно по токенам."""
        sheet_obj = self._sheet(self._load(table), sheet)
        self._column(self._schema(sheet_obj), sheet, column)
        # «Тихий столбец»: если ячейки нет в строке — её нет в проекции (не None).
        return {rid: row[column] for rid, row in self._rows(sheet_obj).items() if column in row}

    def get_row(self, table: str, sheet: str, row_id: str) -> dict:
        """Одна строка целиком: {column: value}."""
        sheet_obj = self._sheet(self._load(table), sheet)
        rows = self._rows(sheet_obj)
        self._row(rows, sheet, row_id)
        return dict(rows[row_id])

    def find_row(self, table: str, sheet: str, where: dict, limit: int = 20) -> dict:
        """ID строк по ЗНАЧЕНИЯМ столбцов: {столбец: значение} → [row_id].

        Закрывает разрыв «ИИ знает значение, а писать надо по ID»: без этого единственным
        путём был скан всего листа. Условия соединяются И; сравнение строгое по значению.
        """
        if not where:
            raise TableError("VALIDATION_ERROR", errors=[{"field": "where", "rule": "required"}])
        sheet_obj = self._sheet(self._load(table), sheet)
        self._column(self._schema(sheet_obj), sheet, *where)
        found = [rid for rid, row in self._rows(sheet_obj).items()
                 if all(c in row and row[c] == v for c, v in where.items())]
        return {"sheet": sheet, "where": dict(where), "row_ids": found[:limit],
                "count": len(found), "truncated": len(found) > limit}


    def _validate_write(self, sheet_obj: dict, sheet: str, column: str, value) -> None:
        """Общая проверка перед записью поля: computed-защита, enum, объявленный тип и формат строки — формулы снимка
        получают только значения своего типа. Пусто (None, "") — неполные данные, его судит запасное значение формулы.

        Немедленный отказ (до постановки в очередь) — как велит «защита формул».
        """
        schema = self._schema(sheet_obj)
        if not schema:
            return  # неструктурированный лист — пропускаем (ЗАКОН СИНХРОНИЗАЦИИ на стороне схемы)
        self._column(schema, sheet, column)
        col = schema[column] or {}
        # Защита формул: computed:true ЛИБО writable:false → отказ.
        if col.get("computed") or col.get("writable") is False:
            raise TableError("COMPUTED_READONLY", facts={"column": column, "sheet": sheet})
        # Enum-валидация на стороне сервера (не только дропдаун Excel).
        if col.get("type") == "enum":
            allowed = col.get("enum", [])
            if allowed and value not in allowed:
                raise TableError("ENUM_VIOLATION", facts={
                    "column": column, "sheet": sheet, "allowed": ", ".join(map(str, allowed))})
        if value is None or value == "":
            return
        if (kind := col.get("type")) in _ACCEPTS and not _ACCEPTS[kind](value):
            raise TableError("VALIDATION_ERROR", errors=[{"field": f"{sheet}.{column}", "rule": "type",
                                                          "expected": kind, "got": type(value).__name__}])
        if (pattern := col.get("pattern")) and not (isinstance(value, str) and re.fullmatch(pattern, value)):
            raise TableError("VALIDATION_ERROR", errors=[{"field": f"{sheet}.{column}", "rule": "format",
                                                          "expected": pattern}])

    # ═══ АНАЛИЗ ДАННЫХ ═══

    def get_unique_values(self, table: str, sheet: str, column: str, limit: int = 100) -> dict:
        """Уникальные значения столбца."""
        values = list(self.get_column(table, sheet, column).values())
        unique = list(dict.fromkeys(values))
        truncated = len(unique) > limit
        return {
            "column": column,
            "unique": unique[:limit],
            "count": len(unique),
            "truncated": truncated,
        }

    def get_value_counts(self, table: str, sheet: str, column: str, limit: int = 10) -> dict:
        """Частотный анализ: top-N наиболее частых значений."""
        values = list(self.get_column(table, sheet, column).values())
        counts: dict = {}
        for v in values:
            key = str(v) if v is not None else "(пусто)"
            counts[key] = counts.get(key, 0) + 1
        sorted_counts = sorted(counts.items(), key=lambda x: -x[1])[:limit]
        return {
            "column": column,
            "total": len(values),
            "counts": dict(sorted_counts),
        }

    def find_duplicates(self, table: str, sheet: str, columns: list[str] | None = None) -> dict:
        """Поиск дубликатов по указанным столбцам (или всем)."""
        sheet_obj = self._sheet(self._load(table), sheet)
        rows = self._rows(sheet_obj)
        schema = self._schema(sheet_obj)
        cols = columns or list(schema.keys())
        seen: dict[str, list[str]] = {}
        for rid, row in rows.items():
            key_parts = [str(row.get(c, "")) for c in cols]
            key = "||".join(key_parts)
            if key not in seen:
                seen[key] = []
            seen[key].append(rid)
        duplicates = {k: v for k, v in seen.items() if len(v) > 1}
        return {
            "sheet": sheet,
            "columns": cols,
            "duplicate_groups": len(duplicates),
            "duplicate_rows": sum(len(v) for v in duplicates.values()),
            "details": duplicates,
        }

    def find_nulls(self, table: str, sheet: str) -> dict:
        """Поиск пустых значений по всем столбцам."""
        sheet_obj = self._sheet(self._load(table), sheet)
        rows = self._rows(sheet_obj)
        schema = self._schema(sheet_obj)
        nulls: dict[str, dict] = {}
        for col in schema:
            null_count = 0
            null_ids = []
            for rid, row in rows.items():
                if col not in row or row[col] is None or row[col] == "":
                    null_count += 1
                    null_ids.append(rid)
                    if len(null_ids) >= 10:
                        null_ids.append("...")
                        break
            if null_count > 0:
                nulls[col] = {
                    "count": null_count,
                    "percent": round(null_count / len(rows) * 100, 1) if rows else 0,
                    "row_ids": null_ids,
                }
        return {
            "sheet": sheet,
            "total_rows": len(rows),
            "columns_with_nulls": len(nulls),
            "nulls": nulls,
        }

    # ═══ ЗАПИСЬ (через очередь) ═══

    def set(self, table: str, sheet: str, row_id: str, column: str, value) -> dict:
        """Изменить поле (RMW по строке). Кладёт операцию в очередь.

        Валидация (существование листа/строки/столбца, computed, enum) —
        немедленная, против текущего снапшота. Применение — при execute_queue.
        """
        snapshot = self._load(table)
        sheet_obj = self._sheet(snapshot, sheet)
        self._row(self._rows(sheet_obj), sheet, row_id)
        self._validate_write(sheet_obj, sheet, column, value)
        self._key_free(table, sheet_obj, sheet, {column: value}, self._pending(table, sheet, self._rows(sheet_obj)), row_id)
        op = {"action": "set", "sheet": sheet, "row_id": row_id, "column": column, "value": value}
        self.state.push_to_queue(table, op)
        return op

    def update(self, table: str, sheet: str, row_id: str, data: dict) -> dict:
        """Изменить НЕСКОЛЬКО полей строки ОДНОЙ операцией очереди.

        Отличие от пяти вызовов `set`: пять операций применяются по одной, и отказ на
        третьей оставляет строку наполовину изменённой. Здесь операция одна и применяется
        целиком или не применяется вовсе — частичного состояния не бывает.
        """
        if not data:
            raise TableError("VALIDATION_ERROR", errors=[{"field": "data", "rule": "required"}])
        snapshot = self._load(table)
        sheet_obj = self._sheet(snapshot, sheet)
        self._row(self._rows(sheet_obj), sheet, row_id)
        for column, value in data.items():
            self._validate_write(sheet_obj, sheet, column, value)
        self._key_free(table, sheet_obj, sheet, data, self._pending(table, sheet, self._rows(sheet_obj)), row_id)
        op = {"action": "update", "sheet": sheet, "row_id": row_id, "data": dict(data)}
        self.state.push_to_queue(table, op)
        return op

    def append(self, table: str, sheet: str, data: dict, id_prefix: str = "ROW") -> dict:
        """Новая строка. ID присваивает СЕРВЕР (не Claude), приходит в факте.

        Валидирует каждое поле data против схемы (computed/enum) до очереди.
        """
        snapshot = self._load(table)
        sheet_obj = self._sheet(snapshot, sheet)
        for column, value in data.items():
            self._validate_write(sheet_obj, sheet, column, value)
        self._key_free(table, sheet_obj, sheet, data, self._pending(table, sheet, self._rows(sheet_obj)))
        row_id = self.ids.generate_simple(id_prefix)
        op = {"action": "append", "sheet": sheet, "row_id": row_id, "data": dict(data)}
        self.state.push_to_queue(table, op)
        return op

    def delete(self, table: str, sheet: str, row_id: str) -> dict:
        """Удалить строку (кладёт операцию в очередь)."""
        snapshot = self._load(table)
        sheet_obj = self._sheet(snapshot, sheet)
        self._row(self._rows(sheet_obj), sheet, row_id)
        op = {"action": "delete", "sheet": sheet, "row_id": row_id}
        self.state.push_to_queue(table, op)
        return op

    def _pending(self, table: str, sheet: str, rows: dict) -> dict:
        """Строки листа, какими их сделает очередь: снимок, поверх — ещё не применённые операции того же листа по порядку
        (append, update и set дают значения, delete убирает строку)."""
        held = {rid: dict(row) for rid, row in rows.items()}
        for item in self.state.read_queue(table):
            op = item.get("operation") or {}
            rid = op.get("row_id")
            if op.get("sheet") != sheet or not rid:
                continue
            if op.get("action") == "delete":
                held.pop(rid, None)
            elif op.get("action") == "set":
                held.setdefault(rid, {})[op.get("column")] = op.get("value")
            else:
                held.setdefault(rid, {}).update(op.get("data") or {})
        return held

    def _key_free(self, table: str, sheet_obj: dict, sheet: str, data: dict, held: dict, row_id: str = "") -> None:
        """Значение ключа (столбец схемы с меткой `key`), которое пишет запись, не держит другая строка `held` — иначе отказ
        с ID строки, что его держит: новой строке (append, `row_id` пуст) — ROW_KEY_EXISTS, правке строки `row_id` —
        ROW_KEY_TAKEN. Строка со своим же ключом и пустой ключ не судятся."""
        for column, spec in self._schema(sheet_obj).items():
            value = data.get(column)
            if not (spec or {}).get("key") or value in (None, ""):
                continue
            holder = next((rid for rid, row in held.items() if rid != row_id and row.get(column) == value), None)
            if holder:
                raise TableError("ROW_KEY_TAKEN" if row_id else "ROW_KEY_EXISTS", facts={
                    "table": table, "sheet": sheet, "column": column, "value": value, "row_id": holder})

    # ═══ ОЧЕРЕДЬ (json_*) ═══

    def push_to_queue(self, table: str, action: dict) -> dict:
        """Универсальная постановка в очередь. action = ОДИН из пишущих примитивов.

        Псевдонимы-сахар допустимы на стороне Claude, но сюда приходит уже
        нормализованный {action: set|append|delete, ...}. Чтения (get_*) в
        очередь не кладутся.
        """
        if not isinstance(action, dict) or "action" not in action:
            raise TableError("VALIDATION_ERROR", errors=[{"field": "action", "rule": "object_with_action"}])
        kind = action.get("action")
        if kind not in QUEUEABLE_ACTIONS:
            raise TableError("INVALID_ACTION", facts={"action": str(kind), "allowed": ", ".join(sorted(QUEUEABLE_ACTIONS))})
        sheet = action.get("sheet")
        if not sheet:
            raise TableError("VALIDATION_ERROR", errors=[{"field": "action.sheet", "rule": "required"}])
        # Дублируем валидацию типизированных методов (единый вход в очередь).
        if kind == "set":
            return self.set(table, sheet, action["row_id"], action["column"], action.get("value"))
        if kind == "update":
            return self.update(table, sheet, action["row_id"], action.get("data", {}))
        if kind == "append":
            return self.append(table, sheet, action.get("data", {}), action.get("id_prefix", "ROW"))
        return self.delete(table, sheet, action["row_id"])

    def read_queue(self, table: str) -> dict:
        """Что стоит в очереди и ещё НЕ применено: до сегодня заглянуть туда было нечем.

        Отвечает и списком операций, и отдельно списком затронутых строк: клиент спрашивает
        «моя правка ещё ждёт?» по ID строки, а не по номеру операции в очереди.
        """
        self._load(table)
        queued = [item.get("operation") or {} for item in self.state.read_queue(table)]
        return {"table": table, "queued": queued, "count": len(queued),
                "row_ids": [str(op.get("row_id")) for op in queued if op.get("row_id")]}

    def execute_queue(self, table: str) -> dict:
        """Применить очередь к read.json (RMW). Забирает и очищает write.json.

        .xlsx-материализация ОТЛОЖЕНА (следующий блок) — применяем к снапшоту
        read.json, а факт синка в книгу честно помечаем xlsx_synced=False.
        Защита формул перепроверяется на применении (schema мог измениться).
        """
        queued = self.state.execute_queue(table)  # [{timestamp, operation}, ...], очередь очищена
        if not queued:
            return {"applied": 0, "skipped": [], "xlsx_synced": False,
                    "note": "Очередь пуста — применять нечего."}

        applied = 0
        skipped: list[dict] = []
        # Чтение—правка—запись под замком снимка: учёт расхода и вторая очередь того же снимка не затрут применённое.
        with self.state.edit_snapshot(table) as snapshot:
            for item in queued:
                op = item.get("operation", {})
                kind = op.get("action")
                sheet = op.get("sheet")
                sheet_obj = snapshot.setdefault(sheet, {"schema": {}, "rows": {}})
                sheet_obj.setdefault("rows", {})
                rows = sheet_obj["rows"]
                try:
                    if kind == "set":
                        col, rid = op["column"], op["row_id"]
                        self._row(rows, sheet, rid)             # строка могла исчезнуть до применения
                        self._validate_write(sheet_obj, sheet, col, op.get("value"))
                        self._key_free(table, sheet_obj, sheet, {col: op.get("value")}, rows, rid)
                        rows[rid][col] = op.get("value")
                    elif kind == "update":
                        rid, data = op["row_id"], op.get("data", {})
                        self._row(rows, sheet, rid)
                        # Сперва проверяем ВСЕ поля, и только потом пишем хоть одно: иначе отказ
                        # на третьем поле оставил бы строку наполовину изменённой.
                        for col, value in data.items():
                            self._validate_write(sheet_obj, sheet, col, value)
                        self._key_free(table, sheet_obj, sheet, data, rows, rid)
                        rows[rid].update(data)
                    elif kind == "append":
                        self._key_free(table, sheet_obj, sheet, op.get("data", {}), rows)
                        rows[op["row_id"]] = dict(op.get("data", {}))
                    elif kind == "delete":
                        rows.pop(op.get("row_id"), None)
                    else:
                        raise TableError("INVALID_ACTION", facts={"action": str(kind),
                                                                  "allowed": ", ".join(sorted(QUEUEABLE_ACTIONS))})
                    applied += 1
                except TableError as e:
                    skipped.append({"op": op, "code": e.code, "facts": dict(e.facts)})
            self._recompute(snapshot)   # формулы — по уже применённым строкам, в той же записи снимка

        return {"applied": applied, "skipped": skipped, "xlsx_synced": False,
                "note": "Применено к read.json. Синк в .xlsx отложен (следующий блок excel_*)."}

    @staticmethod
    def _recompute(snapshot: dict) -> None:
        """Вычисляемые столбцы снимка — заново по формулам их схемы (узел формул, движок ironcalc). Снимок без вычисляемых
        столбцов не трогается, и движок ему не нужен."""
        if not any((spec or {}).get("computed") for sheet_obj in snapshot.values() if isinstance(sheet_obj, dict)
                   for spec in (sheet_obj.get("schema") or {}).values()):
            return
        _, language = declared_language(TableError)
        for sheet, rows in computed(snapshot, language).items():
            for row_id, values in rows.items():
                snapshot[sheet]["rows"][row_id].update(values)

    def clear_queue(self, table: str) -> dict:
        """Очистить очередь без применения (отладка/сброс)."""
        count = self.state.clear_queue(table)
        return {"cleared": count}

    def declare_sheets(self, table: str, book, fallbacks: dict | None = None, patterns: dict | None = None) -> list[str]:
        """Схемы листов снимка — проекция декларации книги (`BookSchema`): тип, перечень, формат строки (выражение
        `patterns[(лист, столбец)]` — его судит запись), вычисляемый (столбец F с формулой: сама формула имён и запасное
        значение `fallbacks[(лист, столбец)]` — по ним считает узел формул), лист-запись. Строки листа не трогаются;
        листа нет — рождается пустым со схемой."""
        with self.state.edit_snapshot(table) as snapshot:
            for sheet in book.sheets:
                sheet_obj = snapshot.setdefault(sheet.name, {"schema": {}, "rows": {}})
                sheet_obj["schema"] = {col.name: _declared_column(col, (fallbacks or {}).get((sheet.name, col.name)),
                                                                  (patterns or {}).get((sheet.name, col.name)))
                                       for col in sheet.columns}
                if sheet.record:
                    sheet_obj["record"] = True
        return [sheet.name for sheet in book.sheets]


def _declared_column(col, fallback: object, pattern: str | None) -> dict:
    """Столбец схемы снимка из столбца декларации: F без формулы заполняет сервер или ИИ — он остаётся записываемым;
    id — ключ строки (`key`): двух строк с одним значением лист не держит."""
    base = {"type": col.type, **({"enum": list(col.enum)} if col.enum else {}), **({"pattern": pattern} if pattern else {}),
            **({"key": True} if col.flag == "id" else {})}
    if col.flag == "F" and col.formula:
        return {**base, "computed": True, "formula": col.formula, "fallback": fallback}
    return base


def _iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


# Что принимает запись по объявленному типу столбца (перечень судит своя проверка): bool — не число; строковый столбец
# несёт и числа (метрика — число или статус), список — строкой через запятую или списком строк.
_ACCEPTS = {
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "string": lambda v: isinstance(v, (str, int, float)),
    "date": _iso_date,
    "list": lambda v: isinstance(v, str) or (isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v)),
}


def sheet_of(snapshot: dict, sheet: str) -> dict:
    """Лист снимка таблицы; нет — SHEET_NOT_FOUND с именем и листами, что есть. Узел для движка и для читателей снимка
    из других модулей (поиск)."""
    if sheet not in snapshot:
        raise TableError("SHEET_NOT_FOUND", facts={"sheet": sheet, "known": ", ".join(sorted(snapshot)) or "—"})
    return snapshot[sheet]
