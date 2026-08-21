"""
tools/search — умный поиск по таблицам (QueryPlanner поверх read.json).

Тонкие обёртки: планирование и исполнение запроса — core/search/query_planner,
чтение снапшотов — TableEngine.load_snapshot. Здесь только разбор запроса и упаковка.
Контракт зафиксирован эталоном tests/quick/tools_inventory.golden.json.
"""

from core.contracts import Fact, ToolResult
from core.engine import Engine
from core.search.query_planner import QueryPlanner, SearchError
from tools._context import ANNOTATIONS_READONLY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы search в движке."""

    planner = QueryPlanner(ctx.table_engine, ctx.state_manager.workspace_path)

    async def search_tables(yaml_query: str | None = None, query_dict: dict | None = None) -> "ToolResult":
        """Умный поиск по таблицам через YAML-запрос.

        Принимает YAML-строку или dict с запросом. Возвращает объединённые
        результаты с прогрессом выполнения.
        """
        try:
            if yaml_query:
                import yaml
                data = yaml.safe_load(yaml_query)
            elif query_dict:
                data = query_dict
            else:
                return ctx.err("VALIDATION_ERROR", "Укажи yaml_query или query_dict")

            plan = planner.load_query_from_dict(data)
            result = planner.execute_plan(plan)

            return ToolResult(status="success", data=result,
                              facts=[Fact(type="SearchCompleted", data={
                                  "query": plan.name,
                                  "reads": result["metadata"]["reads_executed"],
                                  "rows": result["metadata"]["total_rows"],
                                  "errors": result["metadata"]["reads_failed"],
                              })])
        except SearchError as e:
            return ctx.err(e.code, e.message, e.reason, e.suggested_tool)
        except Exception as e:
            return ctx.err("INTERNAL_ERROR", f"Ошибка поиска: {e}")

    async def search_quick(table: str, sheet: str, column: str = "",
                           filter_col: str = "", filter_op: str = "eq",
                           filter_val: str = "", limit: int = 100) -> "ToolResult":
        """Быстрый поиск: одно чтение с фильтром (без YAML)."""
        query = {
            "name": "quick_search",
            "reads": [{
                "table": table,
                "sheet": sheet,
                "columns": [column] if column else [],
                "filter": {filter_col: {filter_op: filter_val}} if filter_col else {},
            }],
            "limit": limit,
        }
        plan = planner.load_query_from_dict(query)
        result = planner.execute_plan(plan)
        return ToolResult(status="success", data=result,
                          facts=[Fact(type="QuickSearch", data={
                              "table": table, "sheet": sheet,
                              "rows": result["metadata"]["total_rows"],
                          })])

    async def search_multi(tables: list[dict], join_key: str = "",
                           filter_after: dict | None = None,
                           sort_col: str = "", sort_order: str = "asc",
                           limit: int = 100) -> "ToolResult":
        """Многотабличный поиск с объединением."""
        reads = []
        for t in tables:
            reads.append({
                "table": t.get("table", ""),
                "sheet": t.get("sheet", ""),
                "columns": t.get("columns", []),
                "filter": t.get("filter", {}),
            })
        query = {
            "name": "multi_search",
            "reads": reads,
            "join": {"on": join_key, "strategy": "inner"} if join_key else None,
            "filter": filter_after or {},
            "sort": {"column": sort_col, "order": sort_order} if sort_col else None,
            "limit": limit,
        }
        plan = planner.load_query_from_dict(query)
        result = planner.execute_plan(plan)
        return ToolResult(status="success", data=result,
                          facts=[Fact(type="MultiSearch", data={
                              "tables": len(tables),
                              "rows": result["metadata"]["total_rows"],
                          })])

    # Регистрация
    search_tools = [
        ("search_tables", "Таблицы: умный поиск (YAML)", "Умный поиск по таблицам через YAML (очередь, многопоточность, объединение)",
         {"type": "object", "properties": {
             "yaml_query": {"type": "string", "description": "YAML-строка с запросом"},
             "query_dict": {"type": "object", "description": "Dict с запросом (альтернатива YAML)"},
         }},
         search_tables, ANNOTATIONS_READONLY),
        ("search_quick", "Таблицы: быстрый поиск", "Быстрый поиск в одной таблице с фильтром (без YAML)",
         {"type": "object", "properties": {
             "table": {"type": "string", "description": "Путь к таблице"},
             "sheet": {"type": "string", "description": "Имя листа"},
             "column": {"type": "string", "description": "Столбец для выборки (все если пусто)"},
             "filter_col": {"type": "string", "description": "Столбец фильтра"},
             "filter_op": {"type": "string", "enum": ["eq", "neq", "gt", "lt", "contains", "in"], "description": "Оператор"},
             "filter_val": {"type": "string", "description": "Значение фильтра"},
             "limit": {"type": "integer", "description": "Максимум строк", "default": 100},
         }, "required": ["table", "sheet"]},
         search_quick, ANNOTATIONS_READONLY),
        ("search_multi", "Таблицы: поиск с объединением (JOIN)", "Поиск с объединением нескольких таблиц (JOIN по ключу)",
         {"type": "object", "properties": {
             "tables": {"type": "array", "items": {"type": "object", "properties": {
                 "table": {"type": "string"}, "sheet": {"type": "string"},
                 "columns": {"type": "array", "items": {"type": "string"}},
                 "filter": {"type": "object"},
             }}, "description": "Список таблиц для поиска"},
             "join_key": {"type": "string", "description": "Ключ для объединения (JOIN)"},
             "filter_after": {"type": "object", "description": "Фильтр после объединения"},
             "sort_col": {"type": "string", "description": "Столбец сортировки"},
             "sort_order": {"type": "string", "enum": ["asc", "desc"], "default": "asc",
                            "description": "Направление сортировки: asc — по возрастанию, desc — по убыванию"},
             "limit": {"type": "integer", "default": 100,
                       "description": "Сколько строк вернуть максимум"},
         }, "required": ["tables"]},
         search_multi, ANNOTATIONS_READONLY),
    ]
    for name, title, desc, schema, handler, annot in search_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="search", annotations=annot)  # type: ignore[arg-type]
