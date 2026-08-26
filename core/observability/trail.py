"""
core/observability/trail.py — след вызовов сервера

## Назначение
Отказ в живой сессии не оставлял после себя ничего: сервер не пишет ни строчки, поэтому «точные
условия неизвестны и взять их негде» было буквальной правдой. След пишет то и только то, из чего
собирается сценарий: инструмент, аргументы, исход, порядок.

## Границы
Формат СОВПАДАЕТ с журналом харнесса (`tests/.journal/*.jsonl`) — синтезатор сценариев читает оба
источника одним кодом. Запись никогда не роняет вызов: сломанный след теряет наблюдение, сломанный
сервер теряет работу владельца.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..contracts.error_detail import redact


class Trail:
    """Дописывает строку на каждый вызов инструмента. Выключенный — молчаливая заглушка."""

    # Дефолтов у параметров НЕТ намеренно: любое значение по умолчанию было бы второй копией
    # того, что уже сказано в config/observability.yaml, и разошлось бы с ним молча.
    def __init__(self, directory: Path | None, keep_files: int = 0,
                 max_bytes: int = 0, record_args: bool = False):
        self.directory = Path(directory) if directory else None
        self.keep_files = max(1, int(keep_files))
        self.max_bytes = max(0, int(max_bytes))
        self.record_args = bool(record_args)
        self.run = time.strftime("%Y%m%d-%H%M%S")
        self._path: Path | None = None
        self._step = 0
        self._written = 0
        self._broken = False

    @classmethod
    def from_declaration(cls, declaration: dict | None, root: Path) -> "Trail":
        """Собрать по `config/observability.yaml`. Нет объявления — след выключен."""
        cfg = (declaration or {}).get("trail") or {}
        if not cfg.get("enabled", False):
            return cls(None)
        missing = [k for k in ("dir", "keep_files", "max_bytes", "record_args") if k not in cfg]
        if missing:
            # Дописать недостающее значением из кода — значит завести вторую половину правды.
            # Неполное объявление гасит след ГРОМКО: молча подставленный предел хуже отсутствия.
            print(f"⚠️  [trail] объявление неполно ({', '.join(missing)}) — след не пишется")
            return cls(None)
        return cls(root / str(cfg["dir"]), keep_files=cfg["keep_files"],
                   max_bytes=cfg["max_bytes"], record_args=cfg["record_args"])

    @property
    def enabled(self) -> bool:
        return self.directory is not None and not self._broken

    def _open(self) -> Path | None:
        """Файл на прогон сервера + прополка старых. Первая запись создаёт, дальше дописываем."""
        if self._path is not None:
            return self._path
        assert self.directory is not None
        self.directory.mkdir(parents=True, exist_ok=True)
        old = sorted(self.directory.glob("trail-*.jsonl"), key=lambda p: p.name)
        for stale in old[: max(0, len(old) - self.keep_files + 1)]:
            try:
                stale.unlink()
            except OSError:
                pass
        self._path = self.directory / f"trail-{self.run}.jsonl"
        return self._path

    def write(self, tool: str, args: dict, result) -> None:
        """Строка следа по одному вызову. Любой отказ записи глушит след, но не вызов."""
        if not self.enabled:
            return
        if self.max_bytes and self._written >= self.max_bytes:
            return
        self._step += 1
        error = getattr(result, "error", None)
        entry = {
            "ts": round(time.time(), 3),
            "scenario": f"trail-{self.run}",
            "step": self._step,
            "tool": tool,
            "args": redact(args) if self.record_args else {},
            "ok": getattr(result, "status", "") == "success",
            "code": getattr(error, "code", "") or "",
            "message": getattr(error, "message", "") or "",
            "reaction_class": getattr(error, "reaction_class", "") or "",
            "recovery": _recovery(error),
            "facts": [f.type for f in (getattr(result, "facts", None) or [])],
            "data": redact(getattr(result, "data", None)) or {},
        }
        try:
            path = self._open()
            assert path is not None
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            self._written += len(line.encode("utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            # Молча — нельзя: след перестанет писаться незаметно, и это выяснится ровно тогда,
            # когда он понадобился. Шумно на каждый вызов — тоже нельзя, консоль зальёт.
            self._broken = True
            print(f"⚠️  [trail] запись следа не удалась ({exc!r}) — дальше след не пишется")


def _recovery(error) -> dict:
    """Рецепт как данные: сценарий сверяет с реестром, а не с текстом сообщения."""
    recovery = getattr(error, "recovery", None)
    if recovery is None:
        return {}
    return {"suggested_tool": getattr(recovery, "suggested_tool", "") or "",
            "reason": getattr(recovery, "reason", "") or ""}
