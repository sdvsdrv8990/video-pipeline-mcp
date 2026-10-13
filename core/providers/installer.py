"""core/providers/installer.py — установка модели идёт долго, поэтому она — задание узла заданий, и за ней наблюдают.

Гигабайты не укладываются в один синхронный вызов: он упёрся бы в таймаут клиента или замолчал бы неотличимо от смерти.
Ход меряется ПО ДИСКУ, а не по проценту загрузчика — тот может отчитаться и оборваться: признак работы — рост байтов
плана не медленнее меры загрузчика или его отметка, а не тик наблюдателя. Запись, статус, снятие и перевод отказа — у
узла заданий (`core/jobs.py`); планировщика нет (решение владельца): поток живёт ровно эту установку. Замок модели
держит другая установка — работа ждёт её конца (остаток части у них общий), пока задание не снято, а не отказывает.
"""

import logging
import threading
import time

from core import jobs
from core.contracts import TaskStatus

log = logging.getLogger(__name__)
PLACE = "model_download"        # класс места работы установки: число мест — resources.yaml `work`


class ModelInstaller:
    """Установка модели заданием узла и наблюдение за ней по диску."""

    def __init__(self, catalog, heartbeat_sec: float):
        self.catalog = catalog
        self.heartbeat = float(heartbeat_sec)
        self.floor = float(catalog.speed()["floor"])

    def begin(self, tasks: jobs.Tasks, tool: str, model_id: str, kind: str, force: bool = False) -> dict:
        """Начать установку заданием узла — статус задачи (`TaskStatus`) с идентификатором для прозвонки.

        Модель уже стоит той же ревизией и цела — исход сразу, задание не заводится (`task_id=None`): ни потока, ни
        записи, ни загрузки (MF-17). Место на диске судится до задания: нехватка — отказ вызова, а не задание, которое
        упадёт гигабайтами позже. Повтор того же вызова в окне узла — то же задание, второй загрузки нет (AD-21); явный
        `force` — перекачать все части с исходом «перекачано».
        """
        params = {"model_id": model_id, "kind": kind, "force": force}
        already = None if force else self.catalog.installed_as(model_id, kind)
        if already:
            return TaskStatus(task_id=None, status="completed", params=params, result=already).model_dump()
        twin = tasks.twin(tool, params)         # до суда места: остаток идущей установки его уже съел
        if twin is not None:
            return twin["task"]
        место = self.catalog.место_под(model_id, kind, force=force)
        return tasks.begin(tool, params, PLACE, lambda pulse: self.work(место, pulse))["task"]

    def work(self, место: dict, pulse, progress=lambda _m: None) -> dict:
        """Работа установки по снимку `место_под`: наблюдатель — с первого шага, до замка модели (рост чужой загрузки той
        же модели — тоже работа: остаток части у них общий), чужой замок ждётся до конца держателя, пока задание не
        снято. Снятие (`pulse.cancelled`) работа судит в каждой фазе — ожидание замка, шаг, отметка загрузки и сверки —
        и кончается отказом снятия (`pulse.refusal`). Итог — после того, как наблюдатель остановлен и дождан: его
        последний тик не ляжет поверх итога. `pulse` — признак работы задания узла или установки из CLI."""
        steps, marks, stop, started = [""], [0.0], threading.Event(), time.time()

        def alive() -> None:
            if pulse.cancelled.is_set():
                raise pulse.refusal()

        def step(message: str) -> None:
            alive()
            steps.append(message)
            progress(message)

        def mark() -> None:
            alive()
            marks.append(time.time())

        watcher = threading.Thread(target=self._observe, args=(место, pulse, started, steps, marks, stop), daemon=True)
        watcher.start()
        try:
            entry = self.catalog.install_planned(место, progress=step, mark=mark,
                                                 held=lambda: step("замок модели взят"), pulse=pulse)
        finally:
            stop.set()
            watcher.join()
        pulse(self._progress(self.catalog.on_disk(место), started, time.time(), steps[-1]))
        return {"outcome": "reinstalled" if место["force"] else "installed", "path": entry["path"], "mb": entry["mb"],
                "refused": entry.get("refused") or []}

    def _observe(self, место: dict, pulse, started: float, steps: list, marks: list, stop: threading.Event) -> None:
        """Пока идёт работа — тик раз в `heartbeat_sec`: ход и признак работы в `pulse`; часы — только здесь."""
        seen = {"bytes": 0, "tick": started, "work": started}
        while not stop.wait(self.heartbeat):
            try:
                now = time.time()
                seen, working = self._step(seen, self.catalog.on_disk(место), marks[-1], now)
                pulse(self._progress(int(seen["bytes"]), started, now, steps[-1]), working=working)
            except Exception:  # наблюдение не роняет установку, но причина остановки тиков — в журнале сервера
                _stopped(место["entry"].get("id"))
                return

    def _step(self, seen: dict, landed: int, mark: float, now: float) -> tuple[dict, bool]:
        """Шаг наблюдения без часов: сколько байт плана легло и была ли с прошлого шага работа — рост байтов не медленнее
        меры загрузчика (NI-01; мера 0 — любой рост) или отметка загрузчика и сверки позже прошлой работы. Живой поток
        при мёртвой сети работой не выглядит; байты, упавшие и растущие заново, — работа."""
        grown = landed - seen["bytes"]
        working = (grown > 0 and grown >= self.floor * (now - seen["tick"])) or mark > seen["work"]
        return {"bytes": landed, "tick": now, "work": now if working else seen["work"]}, working

    @staticmethod
    def _progress(landed: int, started: float, now: float, step: str) -> dict:
        return {"bytes": landed, "mb": round(landed / 1e6, 1), "elapsed_sec": round(now - started, 1), "step": step}


def _stopped(model: object) -> None:
    """Наблюдение установки остановлено: признак работы больше не обновляется — причина с трассой в журнал сервера."""
    log.exception("наблюдение установки %s остановлено: признак работы больше не обновляется", model)
