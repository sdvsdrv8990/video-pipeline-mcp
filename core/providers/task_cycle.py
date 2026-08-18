"""
core/providers/task_cycle.py — ожидание асинхронной задачи провайдера и приёмка загрузки.

## Назначение
Асинхронный провайдер отвечает идентификатором задачи (`sync_mode: false`): здесь ожидание
trigger → poll → download и приёмка того, что файл лёг. Как ждать — `config/media_tasks.yaml`.

## Границы
- цикл нужен не «дождаться готового», а ПОЙМАТЬ ОТКАЗ: модерация, лимит, невалидный вход —
  без прозвонки это молчание, а причину надо отдать ИИ;
- планировщика нет (решение владельца S15): цикл — часть вызова, отвалившийся клиент задачу
  не теряет и не спасает, её статус прозванивается заново;
- ни одного имени провайдера: опрос передаётся функцией, имена статусов — из декларации;
- приёмка смотрит на ДИСК, а не на «200 OK»: бывает нулевой размер и обрыв на середине.
"""

import time
from pathlib import Path

from core.paths import safe_resolve

from .declaration import Declaration


class TaskCycleError(Exception):
    """Ошибка ожидания/загрузки в формате контракта (код из server_reactions.yaml)."""

    def __init__(self, code: str, message: str, reason: str = "", suggested_tool: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool


class TaskCycle:
    """Ожидание задачи провайдера + приёмка скачанного файла по декларации."""

    def __init__(self, config_file: str | Path, sleep=time.sleep, now=time.monotonic):
        self.config_file = Path(config_file)
        self._sleep = sleep          # инъекция: в тестах ожидание не должно занимать минуты
        self._now = now
        self._decl = Declaration(
            config_file, TaskCycleError, "ожидания",
            "Заведи config/media_tasks.yaml — интервалы и статусы объявляются там.")

    @property
    def config(self) -> dict:
        return self._decl.data

    # ═══ Ожидание ═══

    def classify(self, status: str) -> str:
        """Слово провайдера → `done` / `failed` / `running`. Слова живут в декларации."""
        table = self.config.get("statuses") or {}
        low = str(status or "").strip().lower()
        for kind in ("done", "failed"):
            if low in {str(s).lower() for s in (table.get(kind) or [])}:
                return kind
        return "running"

    def wait(self, poll, task_id: str) -> dict:
        """Опрашивать `poll(task_id)` до готовности. Возвращает последний ответ + журнал попыток.

        Неизвестный статус НЕ обрывает ожидание и не выдаётся за успех: он копится в отчёте,
        чтобы после таймаута было видно, что именно отвечал провайдер.
        """
        p = self.config.get("poll") or {}
        interval = float(p.get("interval_sec", 3))
        backoff = float(p.get("backoff", 1.0))
        max_interval = float(p.get("max_interval_sec", interval))
        timeout = float(p.get("timeout_sec", 300))
        max_attempts = int(p.get("max_attempts", 60))
        known = {str(s).lower() for kind in ("done", "failed")
                 for s in (self.config.get("statuses") or {}).get(kind) or []}

        started = self._now()
        attempts = 0
        unknown: list[str] = []
        while True:
            attempts += 1
            answer = poll(task_id) or {}
            status = answer.get("status", "")
            kind = self.classify(status)
            if kind == "done":
                return {"outcome": "done", "attempts": attempts, "answer": answer,
                        "unknown_status": sorted(set(unknown)),
                        "waited_sec": round(self._now() - started, 3)}
            if kind == "failed":
                raise TaskCycleError(
                    "PROVIDER_FAILED", f"Провайдер сообщил об отказе задачи: {status}",
                    reason=(answer.get("error") or "Ответ провайдера не содержит причины — "
                            "смотри его журнал; повтор того же запроса даст тот же отказ."))
            if str(status).lower() not in known:
                unknown.append(str(status))
            if attempts >= max_attempts or (self._now() - started) >= timeout:
                raise TaskCycleError(
                    "PROVIDER_TIMEOUT",
                    f"Задача '{task_id}' не завершилась за {attempts} опросов "
                    f"({round(self._now() - started, 1)} с).",
                    reason=("Подними timeout_sec/max_attempts в config/media_tasks.yaml, если задача "
                            "штатно дольше, или проверь задачу на стороне провайдера. "
                            f"Последний статус: '{status}'."))
            self._sleep(interval)
            interval = min(interval * backoff, max_interval)

    # ═══ Приёмка загрузки ═══

    def verify_download(self, rel_path: str, workspace: str | Path,
                        declared_bytes: int | None = None) -> dict:
        """Файл действительно лёг на диск? Проверяем по ДИСКУ, а не по коду ответа.

        «200 OK» не означает загрузку: бывает нулевой размер, обрыв на середине и путь вне
        рабочей области. Каждая проверка объявлена в `download.verify` — выключается декларацией.
        """
        rules = ((self.config.get("download") or {}).get("verify") or {})
        # Ссылка приходит извне, адресу доверять нельзя: containment обязателен (G17).
        target = safe_resolve(rel_path, Path(workspace))
        checks: dict[str, bool] = {}

        if rules.get("exists", True):
            checks["exists"] = target.is_file()
            if not checks["exists"]:
                raise TaskCycleError(
                    "DOWNLOAD_INCOMPLETE", f"Файл не появился на диске: {rel_path}",
                    reason="Провайдер ответил успехом, но файла нет — загрузка не состоялась.")
        size = target.stat().st_size if target.is_file() else 0
        min_bytes = int(rules.get("min_bytes", 0) or 0)
        if min_bytes:
            checks["min_bytes"] = size >= min_bytes
            if not checks["min_bytes"]:
                raise TaskCycleError(
                    "DOWNLOAD_INCOMPLETE", f"Файл пуст ({size} байт): {rel_path}",
                    reason="Нулевой размер означает, что загрузки не было, даже если код ответа 200.")
        if rules.get("match_declared_size") and declared_bytes:
            checks["declared_size"] = size == int(declared_bytes)
            if not checks["declared_size"]:
                raise TaskCycleError(
                    "DOWNLOAD_INCOMPLETE",
                    f"Размер не сошёлся: на диске {size}, провайдер обещал {declared_bytes}.",
                    reason="Обрыв на середине выглядит как успешная загрузка — сверка размеров это ловит.")
        return {"path": rel_path, "bytes": size, "checks": checks}

    def expected_name(self, base: str, provider_params: dict) -> str:
        """Имя файла с расширением ИЗ СТРОКИ провайдера, а не угаданным из ссылки."""
        col = ((self.config.get("download") or {}).get("format_column")) or "response_format"
        fmt = str(provider_params.get(col) or "").strip().lstrip(".")
        return f"{base}.{fmt}" if fmt else base
