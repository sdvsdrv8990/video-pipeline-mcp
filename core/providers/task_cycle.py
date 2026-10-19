"""core/providers/task_cycle.py — прозвонка асинхронной задачи провайдера и приёмка загрузки.

Прозвонка нужна не «дождаться готового», а ПОЙМАТЬ ОТКАЗ: модерация, лимит, невалидный вход — без
неё это молчание, а причину надо отдать ИИ. Живёт в работе задания узла (`follow`): своего срока нет,
конец — итог провайдера или снятие задания; отвалившийся клиент задание не теряет и не спасает. Ни
одного имени провайдера: опрос передаётся функцией, имена статусов объявлены. Приёмка смотрит на
ДИСК, а не на «200 OK»: бывает нулевой размер и обрыв на середине.
"""

import time
from collections.abc import Iterator
from pathlib import Path

from core.paths import Files

from core.declaration import Declaration
from core.contracts import ContractError
from core.jobs import paced

from .resolver import ProviderSaid

PLACE = "generate"              # класс места работы генерации: число мест — resources.yaml `work`


class TaskCycleError(ContractError):
    """Ошибка ожидания/загрузки в формате контракта (код из server_reactions.yaml)."""


class TaskCycle:
    """Прозвонка задачи провайдера + приёмка скачанного файла по декларации."""

    def __init__(self, config_file: str | Path, now=time.monotonic):
        self.config_file = Path(config_file)
        self._now = now
        self._decl = Declaration(config_file, TaskCycleError, "ожидания")

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

    def follow(self, poll, task_id: str, pulse) -> dict:
        """Прозвонка задачи провайдера в работе задания узла (`pulse`) до её итога: готово — последний ответ и журнал
        опросов; отказ — PROVIDER_FAILED с причиной провайдера; неизвестный статус не обрывает и не выдаётся за успех —
        копится в отчёте. Нашего срока нет: предел — итог провайдера и снятие задания (`jobs.paced`: пауза темпом `poll`
        прерывается снятием); сбой самого опроса повторяет его дверь."""
        statuses = self.config.get("statuses") or {}
        known = {str(s).lower() for kind in ("done", "failed") for s in statuses.get(kind) or []}
        started, attempts = self._now(), [0]
        unknown: set[str] = set()

        def answered() -> dict | None:
            attempts[0] += 1
            answer = poll(task_id, None) or {}
            status = answer.get("status", "")
            kind = self.classify(status)
            if kind == "done":
                return {"outcome": "done", "attempts": attempts[0], "answer": answer,
                        "unknown_status": sorted(unknown), "waited_sec": round(self._now() - started, 3)}
            if kind == "failed":
                raise self._provider_failed(status, answer)
            if str(status).lower() not in known:
                unknown.add(str(status))
            return None

        def pauses() -> Iterator[float]:
            interval = self._decl.need("poll", "interval_sec", kind=float)
            while True:
                yield interval
                interval = min(interval * self._decl.need("poll", "backoff", kind=float),
                               self._decl.need("poll", "max_interval_sec", kind=float))

        return paced(answered, pulse, pauses())

    @staticmethod
    def _provider_failed(status: str, answer: dict) -> TaskCycleError:
        """Провайдер сообщил об отказе задачи: его слово статуса — фактом, причина из ответа — в журнал."""
        refused = TaskCycleError("PROVIDER_FAILED", facts={"status": status})
        if answer.get("error"):
            refused.__cause__ = ProviderSaid(str(answer["error"])[:300])
        return refused

    # ═══ Приёмка загрузки ═══

    def verify_download(self, rel_path: str, workspace: str | Path,
                        declared_bytes: int | None = None) -> dict:
        """Файл действительно лёг на диск? Проверяем по ДИСКУ, а не по коду ответа.

        «200 OK» не означает загрузку: бывает нулевой размер, обрыв на середине и путь вне
        рабочей области. Каждая проверка объявлена в `download.verify` — выключается декларацией.
        """
        rules = ((self.config.get("download") or {}).get("verify") or {})
        # Ссылка приходит извне, адресу доверять нельзя: containment обязателен.
        target = Files(Path(workspace)).place(rel_path)
        checks: dict[str, bool] = {}

        if rules.get("exists", True):
            checks["exists"] = target.is_file()
            if not checks["exists"]:
                raise TaskCycleError("DOWNLOAD_INCOMPLETE", facts={
                    "path": rel_path, "got": 0, "expected": declared_bytes or "—"})
        size = target.stat().st_size if target.is_file() else 0
        min_bytes = int(rules.get("min_bytes", 0) or 0)
        if min_bytes:
            checks["min_bytes"] = size >= min_bytes
            if not checks["min_bytes"]:
                raise TaskCycleError("DOWNLOAD_INCOMPLETE", facts={
                    "path": rel_path, "got": size, "expected": declared_bytes or "—"})
        if rules.get("match_declared_size") and declared_bytes:
            checks["declared_size"] = size == int(declared_bytes)
            if not checks["declared_size"]:
                raise TaskCycleError("DOWNLOAD_INCOMPLETE", facts={
                    "path": rel_path, "got": size, "expected": int(declared_bytes)})
        return {"path": rel_path, "bytes": size, "checks": checks}

    def expected_name(self, base: str, provider_params: dict) -> str:
        """Имя файла с расширением ИЗ СТРОКИ провайдера, а не угаданным из ссылки."""
        col = ((self.config.get("download") or {}).get("format_column")) or "response_format"
        fmt = str(provider_params.get(col) or "").strip().lstrip(".")
        return f"{base}.{fmt}" if fmt else base
