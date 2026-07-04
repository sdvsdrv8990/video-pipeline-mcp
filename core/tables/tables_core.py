"""
core/tables/tables_core.py — Generic-движок табличных данных (Категория 3)

## Назначение
Слой ДАННЫХ таблиц (не структуры). Работает поверх StateManager на
read.json (снапшот) и write.json (очередь). Excel не трогает — материализация
в .xlsx отложена (json_execute_queue применяет к read.json; синк в книгу —
следующий блок, честно помечено note, G16).

## Модель данных (контракт read.json, sheet-dimensioned)
```
read.json = {
  "<SHEET>": {
    "schema": { "<col>": {"type": "string|enum|float|int|bool",
                          "computed": bool, "writable": bool,
                          "enum": [...], "id": bool} },
    "rows":   { "<row_id>": { "<col>": value, ... } }
  }
}
```
- Единица хранения — строка по ID (ДОГМА: не столбцы; RMW по строке).
- Столбец — проекция поверх строк: get_column → {id: value}.
- Запись идёт ТОЛЬКО через очередь (set/append/delete → write.json → execute).
- Защита формул: запись в computed:true → немедленный отказ (COMPUTED_READONLY).
- Enum-валидация: значение вне schema.enum → ENUM_VIOLATION (сервер пишет в
  обход дропдауна, поэтому проверка на сервере, ИНСТРУКЦИЯ инструменты §5).

## Скелет (ИНСТРУКЦИЯ инструменты §1)
Тонкая обёртка (server.py) → generic-ядро (этот файл) → StateManager.
Ядро НЕ помнит состояние между вызовами; факты возвращает вызывающему.
"""

from __future__ import annotations

# Действия, которые можно класть в очередь (пишущие примитивы).
# get_column/get_row — чтения, в очередь НЕ кладутся (ИНСТРУКЦИЯ §4).
QUEUEABLE_ACTIONS = {"set", "append", "delete"}


class TableError(Exception):
    """Ошибка слоя данных. Несёт код из server_reactions.yaml + подсказку.

    Обёртка в server.py ловит и собирает ErrorDetail(code, message, recovery).
    """

    def __init__(self, code: str, message: str, reason: str = "", suggested_tool: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool


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

        ValueError (path escape) прокидывается наружу — обёртка маппит в PATH_ESCAPE.
        """
        snapshot = self.state.read_snapshot(table)
        if snapshot is None:
            raise TableError(
                "TABLE_NOT_FOUND", f"Таблица не найдена: {table}",
                reason="Создай структуру через fs_create_project_structure.",
                suggested_tool="fs_create_project_structure",
            )
        return snapshot

    def _sheet(self, snapshot: dict, sheet: str) -> dict:
        """Достаёт лист. SHEET_NOT_FOUND если листа нет в снапшоте."""
        if sheet not in snapshot:
            available = ", ".join(sorted(k for k in snapshot.keys())) or "(пусто)"
            raise TableError(
                "SHEET_NOT_FOUND", f"Лист '{sheet}' не найден. Есть: {available}",
                reason="Проверь имя листа (регистр важен) или создай его через excel_add_sheet.",
                suggested_tool="json_read_snapshot",
            )
        return snapshot[sheet]

    @staticmethod
    def _rows(sheet_obj: dict) -> dict:
        return sheet_obj.get("rows", {})

    @staticmethod
    def _schema(sheet_obj: dict) -> dict:
        return sheet_obj.get("schema", {})

    # ═══ ЧТЕНИЕ (проекции) ═══

    def get_column(self, table: str, sheet: str, column: str) -> dict:
        """Проекция столбца: {row_id: value}. Точечно, экономно по токенам."""
        sheet_obj = self._sheet(self._load(table), sheet)
        schema = self._schema(sheet_obj)
        if schema and column not in schema:
            raise TableError(
                "COLUMN_NOT_FOUND", f"Столбец '{column}' не найден в листе '{sheet}'.",
                reason="Сверь имя столбца со схемой (json_read_snapshot) или добавь через excel_add_column.",
            )
        # «Тихий столбец»: если ячейки нет в строке — её нет в проекции (не None).
        return {rid: row[column] for rid, row in self._rows(sheet_obj).items() if column in row}

    def get_row(self, table: str, sheet: str, row_id: str) -> dict:
        """Одна строка целиком: {column: value}."""
        sheet_obj = self._sheet(self._load(table), sheet)
        rows = self._rows(sheet_obj)
        if row_id not in rows:
            raise TableError(
                "ROW_NOT_FOUND", f"Строка '{row_id}' не найдена в листе '{sheet}'.",
                reason="Проверь ID строки (get_column отдаст список ID) или создай через table_append.",
            )
        return dict(rows[row_id])

    # ═══ ВАЛИДАЦИЯ ЗАПИСИ (защита формул + enum) ═══

    def _validate_write(self, sheet_obj: dict, sheet: str, column: str, value) -> None:
        """Общая проверка перед записью поля: computed-защита + enum.

        Немедленный отказ (до постановки в очередь) — как велит «защита формул».
        """
        schema = self._schema(sheet_obj)
        if not schema:
            return  # неструктурированный лист — пропускаем (ЗАКОН СИНХРОНИЗАЦИИ на стороне схемы)
        if column not in schema:
            raise TableError(
                "COLUMN_NOT_FOUND", f"Столбец '{column}' не найден в листе '{sheet}'.",
                reason="Сверь имя столбца со схемой или добавь через excel_add_column.",
            )
        col = schema[column] or {}
        # Защита формул: computed:true ЛИБО writable:false → отказ.
        if col.get("computed") or col.get("writable") is False:
            raise TableError(
                "COMPUTED_READONLY", f"Столбец '{column}' вычисляемый (формула) — запись запрещена.",
                reason="Формулы не перезаписываются данными. Меняй исходные столбцы, из которых считается формула.",
            )
        # Enum-валидация на стороне сервера (не только дропдаун Excel).
        if col.get("type") == "enum":
            allowed = col.get("enum", [])
            if allowed and value not in allowed:
                raise TableError(
                    "ENUM_VIOLATION", f"Значение '{value}' вне enum столбца '{column}'. Допустимо: {allowed}.",
                    reason="Используй одно из значений enum из схемы столбца.",
                )

    # ═══ ЗАПИСЬ (через очередь) ═══

    def set(self, table: str, sheet: str, row_id: str, column: str, value) -> dict:
        """Изменить поле (RMW по строке). Кладёт операцию в очередь.

        Валидация (существование листа/строки/столбца, computed, enum) —
        немедленная, против текущего снапшота. Применение — при execute_queue.
        """
        snapshot = self._load(table)
        sheet_obj = self._sheet(snapshot, sheet)
        rows = self._rows(sheet_obj)
        if row_id not in rows:
            raise TableError(
                "ROW_NOT_FOUND", f"Строка '{row_id}' не найдена в листе '{sheet}'.",
                reason="Проверь ID строки или создай через table_append.",
            )
        self._validate_write(sheet_obj, sheet, column, value)
        op = {"action": "set", "sheet": sheet, "row_id": row_id, "column": column, "value": value}
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
        row_id = self.ids.generate_simple(id_prefix)
        op = {"action": "append", "sheet": sheet, "row_id": row_id, "data": dict(data)}
        self.state.push_to_queue(table, op)
        return op

    def delete(self, table: str, sheet: str, row_id: str) -> dict:
        """Удалить строку (кладёт операцию в очередь)."""
        snapshot = self._load(table)
        sheet_obj = self._sheet(snapshot, sheet)
        if row_id not in self._rows(sheet_obj):
            raise TableError(
                "ROW_NOT_FOUND", f"Строка '{row_id}' не найдена в листе '{sheet}'.",
                reason="Проверь ID строки (get_column отдаст список ID).",
            )
        op = {"action": "delete", "sheet": sheet, "row_id": row_id}
        self.state.push_to_queue(table, op)
        return op

    # ═══ ОЧЕРЕДЬ (json_*) ═══

    def push_to_queue(self, table: str, action: dict) -> dict:
        """Универсальная постановка в очередь. action = ОДИН из пишущих примитивов.

        Псевдонимы-сахар допустимы на стороне Claude, но сюда приходит уже
        нормализованный {action: set|append|delete, ...}. Чтения (get_*) в
        очередь не кладутся.
        """
        if not isinstance(action, dict) or "action" not in action:
            raise TableError(
                "INVALID_ACTION", "action должен быть объектом с полем 'action'.",
                reason="Передай {action:'set|append|delete', sheet, ...}.",
            )
        kind = action.get("action")
        if kind not in QUEUEABLE_ACTIONS:
            raise TableError(
                "INVALID_ACTION", f"Действие '{kind}' нельзя поставить в очередь. Разрешены: {sorted(QUEUEABLE_ACTIONS)}.",
                reason="Чтения (get_column/get_row) выполняются сразу, в очередь идут только set/append/delete.",
            )
        sheet = action.get("sheet")
        if not sheet:
            raise TableError("INVALID_ACTION", "В action отсутствует 'sheet'.", reason="Укажи лист.")
        # Дублируем валидацию типизированных методов (единый вход в очередь).
        if kind == "set":
            return self.set(table, sheet, action["row_id"], action["column"], action.get("value"))
        if kind == "append":
            return self.append(table, sheet, action.get("data", {}), action.get("id_prefix", "ROW"))
        return self.delete(table, sheet, action["row_id"])

    def execute_queue(self, table: str) -> dict:
        """Применить очередь к read.json (RMW). Забирает и очищает write.json.

        .xlsx-материализация ОТЛОЖЕНА (следующий блок) — применяем к снапшоту
        read.json, а факт синка в книгу честно помечаем xlsx_synced=False (G16).
        Защита формул перепроверяется на применении (schema мог измениться).
        """
        queued = self.state.execute_queue(table)  # [{timestamp, operation}, ...], очередь очищена
        if not queued:
            return {"applied": 0, "skipped": [], "xlsx_synced": False,
                    "note": "Очередь пуста — применять нечего."}

        snapshot = self.state.read_snapshot(table) or {}
        applied = 0
        skipped: list[dict] = []

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
                    if rid not in rows:
                        raise TableError("ROW_NOT_FOUND", f"Строка '{rid}' исчезла до применения.")
                    self._validate_write(sheet_obj, sheet, col, op.get("value"))
                    rows[rid][col] = op.get("value")
                elif kind == "append":
                    rows[op["row_id"]] = dict(op.get("data", {}))
                elif kind == "delete":
                    rows.pop(op.get("row_id"), None)
                else:
                    raise TableError("INVALID_ACTION", f"Неизвестное действие в очереди: {kind}")
                applied += 1
            except TableError as e:
                skipped.append({"op": op, "code": e.code, "reason": e.message})

        self.state.write_snapshot(table, snapshot)
        return {"applied": applied, "skipped": skipped, "xlsx_synced": False,
                "note": "Применено к read.json. Синк в .xlsx отложен (следующий блок excel_*)."}

    def clear_queue(self, table: str) -> dict:
        """Очистить очередь без применения (отладка/сброс)."""
        count = self.state.clear_queue(table)
        return {"cleared": count}
