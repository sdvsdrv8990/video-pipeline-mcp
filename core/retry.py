"""core/retry.py — один цикл повтора сервера: попытка до исхода с паузой и сроком (экспертиза wait-cycle, WC-01..07).

Узел решения «повторять ли и сколько ждать» для всех дверей: дверь загрузки (`core.providers.download.Fetcher`) и
замена файла слоя (`core.paths`). Политика — объявление со схемой у двери (`core.declaration_schemas.Backoff` и
наследники), часы, сон и генератор — швы двери: число, срок и случайность сюда не вшиты.
"""
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from core.contracts import ContractError

if TYPE_CHECKING:
    import random

    from core.declaration_schemas import Backoff

T = TypeVar("T")


class Again(Exception):
    """Попытка оборвалась так, что повтор осмыслен (обрыв, перегрузка, молчание, временный отказ ОС); несёт отказ на
    случай исчерпания, срок, названный другой стороной (`wait`), и `applied` — запрос мог дойти и выполниться:
    неповторяемый вызов такую попытку не повторяет."""

    def __init__(self, refusal: ContractError, wait: float = 0.0, *, applied: bool = True):
        super().__init__(refusal.code)
        self.refusal, self.wait, self.applied = refusal, wait, applied


def retry(attempt: Callable[[], T], policy: "Backoff", *, deadline: float | None, replayable: bool,
          told_max_sec: float, clock: Callable[[], float], sleep: Callable[[float], None], rng: "random.Random",
          mark: Callable[[], None]) -> T:
    """Попытка до исхода. Повторимое (`Again`) — пауза: своя растёт вдвое от `backoff_sec` до `backoff_max_sec` с
    разбросом `jitter`; срок другой стороны (`Again.wait`) не короче своей и до `told_max_sec`. Последний `Again` уходит
    наружу — отказ это или «не узнано», решает дверь — после `attempts` попыток, а раньше, если другая сторона назвала
    срок длиннее `told_max_sec` или пауза не оставляет времени до срока `deadline` (часы двери): названный срок — в
    фактах отказа. Неповторяемый вызов (`replayable` False) повторяет только попытку, чей запрос не применён
    (`Again.applied` False). `mark` — признак работы двери перед паузой."""
    pause, left = policy.backoff_sec, policy.attempts
    while True:
        try:
            return attempt()
        except Again as e:
            left -= 1
            wait = max(pause * (1 + policy.jitter * rng.random()), e.wait)
            late = deadline is not None and clock() + wait >= deadline
            if not left or e.wait > told_max_sec or late or (e.applied and not replayable):
                if e.wait:
                    e.refusal.with_facts(retry_after_sec=e.wait)
                raise
            mark()
            sleep(wait)
            pause = min(pause * 2, policy.backoff_max_sec)
