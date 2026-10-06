"""core/providers/installer.py — установка модели идёт долго, поэтому за ней наблюдают.

Гигабайты не укладываются в один синхронный вызов: он упёрся бы в таймаут клиента или замолчал бы
неотличимо от смерти. Прогресс меряется ПО ДИСКУ, а не по проценту загрузчика — тот может
отчитаться и оборваться; отсюда же «залежалось»: байты не растут дольше объявленного срока, а не
вечное «идёт». Планировщика нет (решение владельца): поток живёт ровно эту установку, между
запросами ничего не тикает. Файл состояния — хранилище хода и исхода (код и факты, без текста); статус
наружу — `TaskStatus` ядра, собранный на чтении, отказ — сборщиком реестра.
"""

import functools
import json
import logging
import threading
import time
import uuid
from pathlib import Path

from core.contracts import ContractError, TaskStatus
from core.paths import Files
from core.reactions import Reactions

from .resolver import ProviderError

log = logging.getLogger(__name__)


class ModelInstaller:
    """Фоновая установка модели + наблюдение за ней по состоянию на диске."""

    def __init__(self, catalog, heartbeat_sec: float = 2.0, stale_after_sec: float = 120.0):
        self.catalog = catalog
        self.heartbeat = float(heartbeat_sec)
        self.stale_after = float(stale_after_sec)
        silence, speed = float(catalog.silence_sec()), catalog.speed()
        self.floor, window = float(speed["floor"]), float(speed["window"])
        if self.stale_after <= max(silence, window):
            details = (f"local.install.progress.stale_after_sec {self.stale_after:g} с не длиннее законной тишины: "
                       f"тишины загрузчика {silence:g} с — max(2 × local.install.fetch.timeout_sec: соединение и ожидание "
                       "ответа httpx меряет порознь; backoff_max_sec × (1 + jitter): пауза перед повтором) — и окна меры "
                       f"скорости local.install.fetch.speed_window_sec {window:g} с. Законная пауза стала бы простоем — "
                       "подними stale_after_sec выше обоих.")
            raise ContractError("SCHEMA_INVALID", details, facts={"details": details})
        self._marks: dict[str, float] = {}     # install_id → время последнего признака работы загрузчика или сверки

    # ═══ Состояние ═══

    @property
    def state_dir(self) -> Path:
        return Path(self.catalog.models_dir) / "installs"

    def _file(self, install_id: str) -> Path:
        return self.state_dir / f"{install_id}.json"

    def _write(self, state: dict) -> None:
        """Замена файла состояния целиком через дверь слоя: читатель видит старый или новый, не половину (FS-06)."""
        Files(self.state_dir).replace(f"{state['install_id']}.json", json.dumps(state, ensure_ascii=False))

    def _read(self, install_id: str) -> dict:
        path = self._file(install_id)
        if not path.is_file():
            raise ProviderError(
                "ROW_NOT_FOUND", f"Установка '{install_id}' не найдена.",
                reason="Идентификатор выдаётся при запуске; список идущих и завершённых — "
                       "media_install_status без параметров.",
                suggested_tool="media_install_status")
        return json.loads(path.read_text(encoding="utf-8"))

    # ═══ Запуск ═══

    def start(self, model_id: str, kind: str, force: bool = False) -> dict:
        """Начать установку в фоне — статус задачи (`TaskStatus`) с идентификатором для прозвонки.

        Модель уже стоит той же ревизией и цела — исход сразу, задача не заводится (`task_id=None`): ни потока, ни
        файла состояния, ни загрузки (MF-17). Явный `force` — перекачать все части с исходом «перекачано».
        Ответ ждёт, пока поток возьмёт замок модели: отказ до замка (MODEL_INSTALL_BUSY — её уже ставят) поднимается здесь.
        """
        already = None if force else self.catalog.installed_as(model_id, kind)
        if already:
            return TaskStatus(task_id=None, status="completed", params={"model_id": model_id, "kind": kind},
                              result=already).model_dump()
        # Место спрашивается ДО фонового потока (нехватка — отказ `место_под`): кончившийся диск обрывает загрузку
        # общим исключением, и клиент узнаёт об этом гигабайтами позже, без числа и без причины.
        место = self.catalog.место_под(model_id, kind, force=force)
        state = {"install_id": uuid.uuid4().hex[:12], "model_id": model_id, "kind": kind,
                 "phase": "running", "started": time.time(), "heartbeat": time.time(), "bytes": 0, "force": force}
        begun = threading.Event()
        refused: list[BaseException] = []
        threading.Thread(target=self._run, args=(state, место, begun, refused), daemon=True).start()
        begun.wait()
        if refused:
            raise refused[0]
        return self._task(state)

    def _run(self, state: dict, место: dict, begun: threading.Event, refused: list) -> None:
        """Ставит по снимку `место_под`: качается тот же план той же ревизии, по которому судили место.

        Файл состояния и наблюдатель появляются, когда каталог взял замок модели (`held`); отказ до замка уходит в
        `refused` и поднимается в `start`. `begun` ставится на любом пути — `start` не ждёт вечно.
        Итог пишется после того, как наблюдатель остановлен и дождан: его последний тик читал «идёт» и иначе мог лечь
        поверх итога — клиент видел бы «идёт» до простоя.
        """
        install_id, stop = state["install_id"], threading.Event()
        watcher = threading.Thread(target=self._watch, args=(install_id, место, stop), daemon=True)

        def held() -> None:
            self._write(state)
            watcher.start()
            begun.set()

        outcome: dict = {"phase": "failed", "failure": {"code": "INTERNAL_ERROR"}}
        try:
            entry = self.catalog.install_planned(
                место, mark=lambda: self._marks.__setitem__(install_id, time.time()), held=held)
            outcome = {"phase": "done", "path": entry["path"], "mb": entry["mb"], "refused": entry.get("refused") or []}
        except Exception as e:
            if not begun.is_set():              # до замка: файла состояния нет, отказ поднимает `start`
                refused.append(e)
                return
            if isinstance(e, ContractError):
                outcome = {"phase": "failed", "failure": {
                    "code": e.code, "message": e.message, "facts": e.facts, "errors": e.errors}}
            else:  # неожиданное: причина с трассой — в журнал, наружу — номер события
                event = uuid.uuid4().hex[:8]
                log.error("установка %s (%s), событие %s", install_id, место.get("entry", {}).get("id"), event,
                          exc_info=e)
                outcome = {"phase": "failed", "failure": {"code": "INTERNAL_ERROR", "facts": {"event": event}}}
        finally:
            if begun.is_set():
                stop.set()
                watcher.join()
                final = self._read(install_id)
                final.update({**outcome, "heartbeat": time.time(), "finished": time.time()})
                self._write(final)
            begun.set()

    def _watch(self, install_id: str, место: dict, stop: threading.Event) -> None:
        """Пока идёт загрузка — тик наблюдения раз в `heartbeat_sec`; часы — только здесь, тик судится без них."""
        while not stop.wait(self.heartbeat):
            try:
                if not self._tick(install_id, место, time.time()):
                    return
            except Exception:  # наблюдение не должно ронять установку
                return

    def _tick(self, install_id: str, место: dict, now: float) -> bool:
        """«Сколько байт плана легло» и «последний признак работы» (`heartbeat`): двигается ростом байтов или отметкой
        загрузчика и сверки, а не самим тиком — живой поток при мёртвой сети не выглядит работающим. False — не идёт."""
        state = self._read(install_id)
        if state["phase"] != "running":
            return False
        landed, mark = self.catalog.on_disk(место), self._marks.get(install_id, 0.0)
        grown = landed - int(state.get("bytes", 0))
        # Рост — работа, если не медленнее меры загрузчика с прошлого тика или старта (NI-01); мера 0 — любой рост.
        if grown > 0 and grown >= self.floor * (now - float(state.get("tick", state.get("started", now)))):
            state["heartbeat"] = now
        elif mark > float(state.get("heartbeat", 0)):
            state["heartbeat"] = mark
        state["bytes"], state["tick"] = landed, now
        self._write(state)
        return True

    # ═══ Прозвонка ═══

    def status(self, install_id: str = "") -> list[dict]:
        """Что с установками — `TaskStatus` каждой: идёт, готово, отказ (простой тоже). Молчания нет ни в одном случае."""
        return [self._task(s) for s in ([self._read(install_id)] if install_id else self._states())]

    def _states(self) -> list[dict]:
        """Файлы состояния установок, свежие первыми; остаток записи с заметкой (`*.part.json`) и нечитаемый файл
        пропускаются."""
        if not self.state_dir.is_dir():
            return []
        rows = []
        for entry in Files(self.state_dir).walk():
            if entry.kind != "file" or not entry.path.endswith(".json"):
                continue
            try:
                rows.append(json.loads(entry.real.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        rows.sort(key=lambda r: r.get("started", 0), reverse=True)
        return rows

    def _stalled(self, state: dict, now: float | None = None) -> bool:
        """Идёт, а признаков работы (рост байтов, отметка загрузчика) нет дольше объявленного срока: сеть встала, сервер
        перезапустили или поток умер."""
        now = time.time() if now is None else now
        return state.get("phase") == "running" and now - state.get("heartbeat", now) > self.stale_after

    def _task(self, state: dict) -> dict:
        """Статус установки формой ядра (`TaskStatus`): ход — из файла состояния, исход — сборщиком реестра."""
        now, failure = time.time(), state.get("failure")
        if self._stalled(state):
            failure = {"code": "INSTALL_STALLED", "facts": {"model": state["model_id"], "seconds": round(self.stale_after)}}
        elif state.get("phase") == "failed" and not failure:      # файл состояния до TaskStatus: код без фактов
            failure = {"code": state.get("code") or "INTERNAL_ERROR"}
        done = state.get("phase") == "done" and not failure
        error = failure and self._reactions.get_error(
            failure["code"], failure.get("message", ""), facts=failure.get("facts"), errors=failure.get("errors"))
        return TaskStatus(
            task_id=state["install_id"], status="failed" if failure else "completed" if done else "processing",
            params={"model_id": state["model_id"], "kind": state["kind"]},
            progress={"bytes": state.get("bytes", 0), "mb": round(state.get("bytes", 0) / 1e6, 1),
                      "elapsed_sec": round((state.get("finished") or now) - state.get("started", now), 1),
                      "heartbeat": state.get("heartbeat")},
            result={"outcome": "reinstalled" if state.get("force") else "installed", "path": state.get("path", ""),
                    "mb": state.get("mb", 0), "refused": state.get("refused") or []} if done else None,
            error=error or None).model_dump()

    @functools.cached_property
    def _reactions(self) -> Reactions:
        return Reactions(Path(self.catalog.config_file).parent / "server_reactions.yaml")
