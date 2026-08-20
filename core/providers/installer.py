"""
core/providers/installer.py — установка модели идёт долго, поэтому за ней наблюдают.

## Назначение
Установка весов запускается, а её ход прозванивается: гигабайты не укладываются в один
синхронный вызов — он упёрся бы в таймаут клиента или замолчал бы неотличимо от смерти.

## Границы
- прогресс меряется ПО ДИСКУ, а не по проценту загрузчика: загрузчик может отчитаться и
  оборваться. Отсюда же `stale` — байты не растут дольше объявленного срока, а не вечное «идёт»;
- планировщика нет (решение владельца S15): поток живёт ровно эту установку, между запросами
  ничего не тикает; отвалившийся клиент установку не теряет и не спасает;
- прерванное возобновляется с места обрыва (кэш загрузчика), поэтому «повторить» — нормальный
  ответ на сбой, а не «качать всё заново».
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from .resolver import ProviderError

PHASES = ("running", "done", "failed", "stale")


class ModelInstaller:
    """Фоновая установка модели + наблюдение за ней по состоянию на диске."""

    def __init__(self, catalog, heartbeat_sec: float = 2.0, stale_after_sec: float = 120.0):
        self.catalog = catalog
        self.heartbeat = float(heartbeat_sec)
        self.stale_after = float(stale_after_sec)

    # ═══ Состояние ═══

    @property
    def state_dir(self) -> Path:
        return Path(self.catalog.models_dir) / "installs"

    def _file(self, install_id: str) -> Path:
        return self.state_dir / f"{install_id}.json"

    def _write(self, state: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self._file(state["install_id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)                    # наблюдатель не должен прочитать половину записи

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

    def start(self, model_id: str, kind: str) -> dict:
        """Начать установку в фоне. Возвращает идентификатор для прозвонки."""
        running = [s for s in self.status() if s["phase"] == "running" and s["model_id"] == model_id]
        if running:
            raise ProviderError(
                "INVALID_ACTION", f"Установка '{model_id}' уже идёт ({running[0]['install_id']}).",
                reason="Две загрузки одних весов пишут в один каталог и мешают друг другу. "
                       "Прозвони идущую: media_install_status.",
                suggested_tool="media_install_status")
        state = {"install_id": uuid.uuid4().hex[:12], "model_id": model_id, "kind": kind,
                 "phase": "running", "started": time.time(), "heartbeat": time.time(),
                 # Отсчёт от того, что уже лежало: иначе «прогресс» показывал бы размер всего
                 # каталога моделей и был бы одинаков в начале и в конце.
                 "baseline": self._bytes_on_disk(), "bytes": 0,
                 "path": "", "error": "", "code": "", "refused": []}
        self._write(state)
        threading.Thread(target=self._run, args=(state["install_id"],), daemon=True).start()
        return state

    def _run(self, install_id: str) -> None:
        state = self._read(install_id)
        stop = threading.Event()
        watcher = threading.Thread(target=self._watch, args=(install_id, stop), daemon=True)
        watcher.start()
        try:
            entry = self.catalog.install(state["model_id"], state["kind"])
            state = self._read(install_id)
            state.update({"phase": "done", "path": entry["path"], "mb": entry["mb"],
                          "refused": entry.get("refused") or []})
        except ProviderError as e:
            state = self._read(install_id)
            state.update({"phase": "failed", "code": e.code, "error": e.message, "reason": e.reason})
        except Exception as e:  # сеть/диск/среда
            state = self._read(install_id)
            state.update({"phase": "failed", "code": "LOCAL_MODEL_MISSING", "error": str(e),
                          "reason": "Загрузка оборвалась. Повтор продолжит с места обрыва — "
                                    "скачанное не выбрасывается."})
        finally:
            stop.set()
            state["heartbeat"] = time.time()
            state["finished"] = time.time()
            self._write(state)

    def _watch(self, install_id: str, stop: threading.Event) -> None:
        """Пока идёт загрузка — обновлять «сколько байт легло». Диск не врёт, загрузчик может."""
        while not stop.wait(self.heartbeat):
            try:
                state = self._read(install_id)
                if state["phase"] != "running":
                    return
                state["bytes"] = max(0, self._bytes_on_disk() - int(state.get("baseline", 0)))
                state["heartbeat"] = time.time()
                self._write(state)
            except Exception:  # наблюдение не должно ронять установку
                return

    def _bytes_on_disk(self) -> int:
        """Сколько байт лежит там, куда идёт загрузка: каталог моделей + кэш загрузчика.

        Кэш считается тоже, иначе прогресс был бы нулевым до самого конца: загрузчик сперва
        кладёт файл к себе и лишь потом переносит его в каталог моделей.
        """
        total = 0
        for root in self._watched_dirs():
            if root.is_dir():
                total += sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
        return total

    def _watched_dirs(self) -> list[Path]:
        dirs = [Path(self.catalog.models_dir)]
        try:
            from huggingface_hub.constants import HF_HUB_CACHE
            dirs.append(Path(HF_HUB_CACHE))
        except Exception:  # кэш не обязателен
            pass
        return dirs

    # ═══ Прозвонка ═══

    def status(self, install_id: str = "") -> list[dict]:
        """Что с установками: идёт, готово, отказ или зависло. Молчания нет ни в одном случае."""
        if install_id:
            return [self._decorate(self._read(install_id))]
        if not self.state_dir.is_dir():
            return []
        rows = []
        for path in sorted(self.state_dir.glob("*.json")):
            try:
                rows.append(self._decorate(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError):
                continue
        rows.sort(key=lambda r: r.get("started", 0), reverse=True)
        return rows

    def _decorate(self, state: dict) -> dict:
        """Досчитать наблюдаемое: сколько идёт и не зависло ли."""
        now = time.time()
        state["elapsed_sec"] = round((state.get("finished") or now) - state.get("started", now), 1)
        if state.get("phase") == "running" and now - state.get("heartbeat", now) > self.stale_after:
            # Сервер перезапустили или поток умер: «идёт» здесь было бы враньём.
            state["phase"] = "stale"
            state["error"] = state.get("error") or (
                f"Признаков жизни нет дольше {round(self.stale_after)} с.")
            state["reason"] = ("Установку прервали (перезапуск сервера или обрыв). Повтори её — "
                               "скачанное не выбрасывается, загрузка продолжится с места обрыва.")
        state["mb"] = state.get("mb") or round(state.get("bytes", 0) / 1e6, 1)
        return state
