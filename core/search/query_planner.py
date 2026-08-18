"""core/search/query_planner.py — планировщик запросов к таблицам.

## Назначение
Разбирает запрос (YAML-файл или dict) в `QueryPlan`, исполняет чтения из таблиц,
объединяет их, пост-фильтрует, сортирует и режет по лимиту.

## Границы
Состав полей запроса — ровно то, что читает `_parse_plan`; верхнеуровневый `filter`
ложится в `filter_after`, то есть применяется ПОСЛЕ объединения, а не при чтении.
Чтения исполняются пулом потоков по группам, которые нарезает `analyze_dependencies`.
"""

import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
import concurrent.futures

from core.paths import safe_resolve  # D1/G17: containment внутри workspace/


@dataclass
class ReadTask:
    """Задача на чтение данных из таблицы."""
    id: str
    table: str
    sheet: str
    columns: list[str] = field(default_factory=list)
    filter: dict = field(default_factory=dict)
    status: str = "pending"  # pending, running, done, error
    result: Any = None
    error: str | None = None


@dataclass
class QueryPlan:
    """План выполнения запроса."""
    name: str
    description: str
    reads: list[ReadTask]
    join: dict | None = None
    filter_after: dict = field(default_factory=dict)
    sort: dict | None = None
    limit: int | None = None


class SearchError(Exception):
    """Ошибка поиска."""
    def __init__(self, code: str, message: str, reason: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason


class QueryPlanner:
    """Планировщик запросов к таблицам.

    Attributes:
        table_engine: движок таблиц (для чтения)
        workspace: путь к workspace/
    """

    def __init__(self, table_engine, workspace: str | Path):
        self.table_engine = table_engine
        self.workspace = Path(workspace)

    def load_query(self, yaml_path: str | Path) -> QueryPlan:
        """Загрузка YAML-файла запроса из workspace/ (путь контейнится — анти-traversal)."""
        try:
            p = safe_resolve(str(yaml_path), self.workspace)
        except ValueError:
            raise SearchError("PATH_ESCAPE", f"Путь запроса вне workspace: {yaml_path}")
        if not p.exists():
            raise SearchError("QUERY_NOT_FOUND", f"Файл запроса не найден: {yaml_path}")
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return self._parse_plan(data)

    def load_query_from_dict(self, data: dict) -> QueryPlan:
        """Создание плана из dict (без YAML-файла)."""
        return self._parse_plan(data)

    def _parse_plan(self, data: dict) -> QueryPlan:
        """Парсинг данных в QueryPlan."""
        reads = []
        for i, r in enumerate(data.get("reads", [])):
            reads.append(ReadTask(
                id=f"read_{i}",
                table=r.get("table", ""),
                sheet=r.get("sheet", ""),
                columns=r.get("columns", []),
                filter=r.get("filter", {}),
            ))
        return QueryPlan(
            name=data.get("name", "unnamed"),
            description=data.get("description", ""),
            reads=reads,
            join=data.get("join"),
            filter_after=data.get("filter", {}),
            sort=data.get("sort"),
            limit=data.get("limit"),
        )

    def analyze_dependencies(self, plan: QueryPlan) -> list[list[ReadTask]]:
        """Анализ зависимостей и группировка для параллельного выполнения.

        Возвращает список групп задач. Задачи в одной группе могут выполняться
        параллельно. Группы выполняются последовательно.
        """
        # Группируем по таблице (чтения из одной таблицы = последовательно)
        table_groups: dict[str, list[ReadTask]] = {}
        for read in plan.reads:
            key = read.table
            if key not in table_groups:
                table_groups[key] = []
            table_groups[key].append(read)

        # Разные таблицы = параллельно
        groups = []
        for table_reads in table_groups.values():
            groups.append(table_reads)

        return groups

    def execute_plan(self, plan: QueryPlan, max_workers: int = 4) -> dict:
        """Выполнение плана запроса.

        Returns:
            dict с результатами: rows, metadata, errors
        """
        groups = self.analyze_dependencies(plan)
        all_results = []
        errors = []

        for group in groups:
            # Задачи в группе = параллельно
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for task in group:
                    future = executor.submit(self._execute_read, task)
                    futures[future] = task

                for future in concurrent.futures.as_completed(futures):
                    task = futures[future]
                    try:
                        result = future.result()
                        all_results.append(result)
                    except Exception as e:
                        errors.append({"task_id": task.id, "error": str(e)})
                        task.status = "error"
                        task.error = str(e)

        # Объединение результатов
        merged = self._merge_results(all_results, plan)

        # Пост-фильтрация
        if plan.filter_after:
            merged = self._apply_filter(merged, plan.filter_after)

        # Сортировка
        if plan.sort:
            merged = self._apply_sort(merged, plan.sort)

        # Лимит
        if plan.limit:
            merged = merged[:plan.limit]

        return {
            "rows": merged,
            "metadata": {
                "query_name": plan.name,
                "reads_executed": len(plan.reads),
                "reads_successful": len(all_results),
                "reads_failed": len(errors),
                "total_rows": len(merged),
            },
            "errors": errors,
        }

    def _execute_read(self, task: ReadTask) -> list[dict]:
        """Выполнение одного чтения."""
        task.status = "running"
        try:
            # Читаем данные из таблицы
            sheet_data = self.table_engine.load_snapshot(task.table)
            if task.sheet not in sheet_data:
                raise SearchError("SHEET_NOT_FOUND", f"Лист '{task.sheet}' не найден в таблице '{task.table}'")

            sheet_obj = sheet_data[task.sheet]
            rows = sheet_obj.get("rows", {})

            result = []
            for rid, row in rows.items():
                # Фильтрация
                if not self._match_filter(row, task.filter):
                    continue

                # Выбор столбцов
                if task.columns:
                    selected = {c: row.get(c) for c in task.columns if c in row}
                    selected["_row_id"] = rid
                    result.append(selected)
                else:
                    row["_row_id"] = rid
                    result.append(dict(row))

            task.status = "done"
            task.result = result
            return result

        except Exception as e:
            task.status = "error"
            task.error = str(e)
            raise

    def _match_filter(self, row: dict, filter_dict: dict) -> bool:
        """Проверка соответствия строки фильтру."""
        for key, condition in filter_dict.items():
            value = row.get(key)
            if isinstance(condition, dict):
                # gt/lt: несопоставимые типы (str vs num) → строка не проходит фильтр, не TypeError (F42).
                if "gt" in condition:
                    try:
                        if not (value is not None and value > condition["gt"]):
                            return False
                    except TypeError:
                        return False
                if "lt" in condition:
                    try:
                        if not (value is not None and value < condition["lt"]):
                            return False
                    except TypeError:
                        return False
                if "eq" in condition and value != condition["eq"]:
                    return False
                if "neq" in condition and value == condition["neq"]:
                    return False
                if "in" in condition and value not in condition["in"]:
                    return False
                if "contains" in condition and condition["contains"] not in str(value):
                    return False
            else:
                if value != condition:
                    return False
        return True

    def _merge_results(self, all_results: list[list[dict]], plan: QueryPlan) -> list[dict]:
        """Объединение результатов чтений."""
        if not plan.join:
            # Без объединения — просто конкатенация
            merged = []
            for result in all_results:
                merged.extend(result)
            return merged

        # Объединение по ключу
        join_key = plan.join.get("on", "_row_id")
        strategy = plan.join.get("strategy", "inner")

        # Группируем по ключу
        key_groups: dict[str, list[dict]] = {}
        for result in all_results:
            for row in result:
                key = str(row.get(join_key, ""))
                if key not in key_groups:
                    key_groups[key] = []
                key_groups[key].append(row)

        # Объединяем
        merged = []
        for key, rows in key_groups.items():
            if strategy == "inner" and len(rows) < 2:
                continue
            combined = {}
            for row in rows:
                combined.update(row)
            merged.append(combined)

        return merged

    def _apply_filter(self, rows: list[dict], filter_dict: dict) -> list[dict]:
        """Пост-фильтрация результатов."""
        return [r for r in rows if self._match_filter(r, filter_dict)]

    def _apply_sort(self, rows: list[dict], sort_config: dict) -> list[dict]:
        """Сортировка результатов (разнотипные значения не роняют sorted — F42).

        Чужеродные для столбца значения всегда в ХВОСТЕ, в обе стороны (F90): раньше группа
        участвовала в развороте, и «топ по просмотрам» по убыванию начинался с текстовых
        заглушек и пустых ячеек — то есть ответ на «покажи лучшие» открывался мусором.
        """
        column = sort_config.get("column", "")
        reverse = sort_config.get("order", "asc") == "desc"

        def numeric(v) -> bool:
            return isinstance(v, (int, float))          # bool — подтип int, сравним как число

        values = [r.get(column) for r in rows]
        # Тип столбца — по большинству заполненных значений, а не по первой строке.
        filled = [v for v in values if v is not None]
        by_number = sum(1 for v in filled if numeric(v)) >= sum(1 for v in filled if not numeric(v))

        def fits(v) -> bool:
            return v is not None and numeric(v) == by_number

        main = [r for r in rows if fits(r.get(column))]
        tail = [r for r in rows if not fits(r.get(column))]
        key = ((lambda r: float(r.get(column))) if by_number   # type: ignore[arg-type]
               else (lambda r: str(r.get(column))))
        return (sorted(main, key=key, reverse=reverse)
                + sorted(tail, key=lambda r: str(r.get(column) or "")))
