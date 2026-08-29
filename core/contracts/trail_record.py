"""
core/contracts/trail_record.py — форма ОДНОЙ записи следа и журнала.

След сервера (`logs/trail/*.jsonl`) и журнал харнесса (`tests/.journal/*.jsonl`) пишут одну форму,
и воспроизводитель читает оба источника одним кодом. Модель — источник ИМЁН, а не общий код:
харнесс её не импортирует намеренно (он судит сервер снаружи, по проводу), и его сторона сверяется
сторожом по этому же объявлению.

Поле зовётся `reaction_class`, а не `class`: `class` — ключевое слово Python. В реестре реакций и в
языке сценариев то же понятие зовётся `class`, поэтому спросить запись ЭТИМ именем — частый промах,
немой по природе.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class TrailRecord(BaseModel):
    """Запись о ШАГЕ: что позвали, чем ответили, каким кодом и с каким рецептом."""

    # Лишнее поле — не «расширение», а разошедшаяся форма: читатель его не ждёт и промахнётся молча.
    model_config = ConfigDict(extra="forbid")

    ts: float
    scenario: str
    step: int
    tool: str
    args: dict
    ok: bool
    code: str
    message: str
    reaction_class: str
    recovery: dict
    facts: list[str]
    data: dict

    # Поля ОДНОГО производителя объявлены здесь же: иначе «общая форма» опять держалась бы тем,
    # что никто не спросил, откуда взялась запись.
    level: str = ""            # только след: рубеж отказа (periphery/identity/firewall/engine)
    rpc: str = ""              # только след: отказ ДО диспетчера воспроизводится шагом rpc, не call
    console: list[str] = []    # только журнал: строки консоли сервера за этот шаг


class RunSummary(BaseModel):
    """Итог прогона последней строкой журнала: сторож читает артефакт, а не верит слову «прогнал»."""

    model_config = ConfigDict(extra="forbid")

    ts: float
    scenario: Literal["__run__"]
    ok: bool
    crashed: bool
    total: int
    failed: int
    scenarios: list[str]
    head: str
    dirty: bool
