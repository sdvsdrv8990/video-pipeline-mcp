"""tools/tasks/__init__.py — статус заданий узла заданий (`core/jobs.py`): один инструмент на весь сервер.

Элемент ответа — `TaskEntry`: статус задания или отказ своего элемента (нет такого, запись не читается). Один плохой
id не прячет статусы остальных; отказ всего вызова — только негодный вход, его судит схема движка.
"""

from core.contracts import ToolResult
from core.engine import Engine
from core.jobs import Tasks
from tools._context import ANNOTATIONS_READONLY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    tasks = Tasks(engine)

    async def task_status(task_ids: list[str] | None = None) -> ToolResult:
        """Статусы заданий по id; без id — все живые и недавние (конченые старше срока хранения убираются)."""
        ok, entries = ctx.safe(lambda: tasks.status(task_ids))
        if not ok:
            return entries
        return ToolResult(status="success", data={"tasks": entries})

    engine.register(
        name="task_status",
        title="Задания: статус",
        description=(
            "Показывает статус долгих заданий сервера: ждёт места (pending), идёт (processing), готово (completed) "
            "или отказ (failed) с причиной из реестра. Каждый элемент ответа — либо статус задания, либо отказ "
            "этого элемента: такого задания нет или его запись повреждена; остальные задания отвечают как обычно. "
            "Задание без исполнителя (сервер перезапущен) называется прерванным, а молчащее дольше срока — "
            "простоем, а не вечным «идёт»."),
        input_schema={"type": "object", "properties": {
            "task_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                         "description": "Какие задания (их id из ответа запуска); не задано — все живые и недавние"},
        }}, handler=task_status, group="tasks", annotations=ANNOTATIONS_READONLY)
