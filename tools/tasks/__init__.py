"""tools/tasks/__init__.py — статус и снятие заданий узла заданий (`core/jobs.py`): одни инструменты на весь сервер.

Элемент ответа — `TaskEntry`: статус задания или отказ своего элемента (нет такого, запись не читается, снимать нечего).
Один плохой id не прячет статусы остальных; отказ всего вызова — только негодный вход, его судит схема движка.
"""

import asyncio

from core.contracts import ToolResult
from core.engine import Engine
from core.jobs import Tasks
from tools._context import ANNOTATIONS_MODIFY, ANNOTATIONS_READONLY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    tasks = Tasks(engine)

    async def task_status(task_ids: list[str] | None = None, wait: bool = False) -> ToolResult:
        """Статусы заданий по id; без id — все живые и недавние. `wait` — ответ на первую смену статуса: ожидание идёт
        событием узла без потока и без места, обрыв клиента его снимает. Записи читаются в потоке пула: цикл не ждёт
        диск, отказ декларации — тем же переводом (`ctx.safe`)."""
        ok, entries = await asyncio.to_thread(ctx.safe, lambda: tasks.status(task_ids))
        if not ok:
            return entries
        if wait and entries:                    # заданий нет — ждать нечего; пустой список id узел читает как «все»
            entries = await tasks.waiting([entry["task_id"] for entry in entries])
        return ToolResult(status="success", data={"tasks": entries})

    async def task_cancel(task_ids: list[str]) -> ToolResult:
        """Снять задания по id: элемент на каждый — статус `cancelled` или отказ своего элемента."""
        ok, entries = ctx.safe(lambda: tasks.cancel(task_ids))
        if not ok:
            return entries
        return ToolResult(status="success", data={"tasks": entries})

    engine.register(
        name="task_status",
        title="Задания: статус",
        description=(
            "Показывает статус долгих заданий сервера: ждёт места (pending), идёт (processing), готово (completed), "
            "отказ (failed) или снято (cancelled) — с причиной из реестра. У идущего — факты хода: last_work_at (когда "
            "был последний признак работы, UTC) и idle_sec (сколько секунд его нет); сервер не судит задание по часам — "
            "молчит слишком долго, решаешь ты и снимаешь task_cancel. Без wait отвечает сразу — это основной режим: "
            "опрашивай им. С wait=true держит вызов, пока статус хоть одного задания не сменится (ход сменой не "
            "считается): клиенту, который обрывает долгие вызовы, надёжнее опрос. Каждый элемент ответа — либо статус "
            "задания, либо отказ этого элемента: такого задания нет или его запись повреждена; остальные задания "
            "отвечают как обычно. Задание без исполнителя (сервер перезапущен) называется прерванным."),
        input_schema={"type": "object", "properties": {
            "task_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                         "description": "Какие задания (их id из ответа запуска); не задано — все живые и недавние"},
            "wait": {"type": "boolean",
                     "description": "Ждать первой смены статуса любого из заданий, а не отвечать сразу"},
        }}, handler=task_status, group="tasks", annotations=ANNOTATIONS_READONLY)

    engine.register(
        name="task_cancel",
        title="Задания: снять",
        description=(
            "Снимает задания сервера: работа останавливается, статус становится cancelled и больше не меняется; "
            "результата у снятого нет, повторный запуск того же вызова заводит новое задание. Каждый элемент ответа — "
            "статус снятого задания либо отказ этого элемента: задание уже кончилось (снимать нечего), его исполняет "
            "другой процесс или такого задания нет; остальные снимаются как обычно."),
        input_schema={"type": "object", "properties": {
            "task_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                         "description": "Какие задания снять (их id из ответа запуска)"},
        }, "required": ["task_ids"]}, handler=task_cancel, group="tasks", annotations=ANNOTATIONS_MODIFY)
