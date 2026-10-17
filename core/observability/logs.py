"""core/observability/logs.py — где сервер держит свои журналы и сколько: один корень, пределы из одного объявления.

Корень — каталог журналов пользователя этой ОС (platformdirs) для объявленного имени приложения; среда переопределяет
его, как рабочую область. Не каталог кода и не рабочая область: журнал фактов не попадает под инструменты fs_* клиента.
След вызовов и журнал фактов — разные читатели и формы, общее у них — корень и одно место чтения пределов.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
import warnings
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

import platformdirs

from core.contracts import ContractError
from core.declaration import Declaration

from .trail import Trail

_TRAIL = ("dir", "keep_files", "max_bytes", "record_args")
_SESSION = ("file", "max_bytes", "backup_count")
_HEARD = ("loggers", "level", "warnings")


@dataclass(frozen=True)
class Logs:
    """Журналы сервера по объявлению: корень, след вызовов и журнал фактов (None — не объявлен)."""

    root: Path | None
    trail: Trail
    session: Journal | None
    heard: dict | None = None

    @classmethod
    def from_declaration(cls, declaration: dict | None, override: str | None) -> Logs:
        """Единственное место чтения корня и пределов обоих журналов (`config/observability.yaml`); `override` — корень из
        среды (`MCP_LOGS`). Неполное объявление гасит журнал ГРОМКО: молча подставленный предел хуже отсутствия."""
        decl = declaration or {}
        logs = decl.get("logs") or {}
        if not override and not logs.get("app"):
            print("⚠️  [logs] не объявлен logs.app и нет MCP_LOGS — журналы не пишутся")
            return cls(None, Trail(None), None)
        root = Path(override or platformdirs.user_log_dir(str(logs["app"]), appauthor=False))
        return cls(root, _trail(decl.get("trail") or {}, root), _session(logs.get("session") or {}, root),
                   _heard(logs.get("heard") or {}))

    @classmethod
    def from_config(cls, config_dir: str | Path, override: str | None) -> Logs:
        """Журналы по `<config_dir>/observability.yaml`, прочитанному дверью деклараций: так их находит любой, кто не
        сервер (обвязка тестов, воспроизводитель следа). Нет файла — журналов нет, битый файл — отказ кодом."""
        declared = Declaration(Path(config_dir) / "observability.yaml", ContractError, "журналов сервера",
                               "Заведи config/observability.yaml — корень и пределы журналов объявлены там.")
        return cls.from_declaration(declared.optional(), override)

    def hear(self) -> None:
        """Записи logging объявленных логгеров и предупреждения Python — в журнал фактов: туда ответ клиенту отсылает
        владельца номером события (EB-03, EB-05). Зовёт сборка сервера, а не любой читатель журналов; повторная сборка в
        том же процессе заменяет обработчик, а не добавляет второй."""
        for name, handler in _attached:
            logging.getLogger(name).removeHandler(handler)
        _attached.clear()
        if self.session is None or self.heard is None:
            return
        handler = _Heard(self.session)
        for name in self.heard["loggers"]:
            logging.getLogger(name).addHandler(handler)
            logging.getLogger(name).setLevel(self.heard["level"])
            _attached.append((name, handler))
        logging.captureWarnings(True)
        warnings.simplefilter(self.heard["warnings"])
        threading.excepthook = _thread_crash


def _trail(cfg: dict, root: Path) -> Trail:
    if not cfg.get("enabled", False):
        return Trail(None)
    missing = [k for k in _TRAIL if k not in cfg]
    if missing:
        print(f"⚠️  [trail] объявление неполно ({', '.join(missing)}) — след не пишется")
        return Trail(None)
    return Trail(root / str(cfg["dir"]), keep_files=cfg["keep_files"], max_bytes=cfg["max_bytes"],
                 record_args=cfg["record_args"])


def _thread_crash(args: threading.ExceptHookArgs) -> None:
    """Сетка под узлом границы потоков: необработанное исключение любого потока — запись журнала с трассировкой; stderr
    остаётся (`threading.__excepthook__`)."""
    name = args.thread.name if args.thread is not None else "—"
    logging.getLogger("core.threads").error("поток %s упал мимо узла границы", name, exc_info=args.exc_value)
    threading.__excepthook__(args)


def _heard(cfg: dict) -> dict | None:
    if not cfg:
        print("⚠️  [journal] не объявлен logs.heard — записи logging и предупреждения в журнал не идут")
        return None
    missing = [k for k in _HEARD if k not in cfg]
    if missing:
        print(f"⚠️  [journal] logs.heard неполно ({', '.join(missing)}) — записи logging в журнал не идут")
        return None
    return {k: cfg[k] for k in _HEARD}


def _session(cfg: dict, root: Path) -> Journal | None:
    missing = [k for k in _SESSION if k not in cfg]
    if missing:
        print(f"⚠️  [journal] объявление неполно ({', '.join(missing)}) — журнал фактов не пишется")
        return None
    if int(cfg["max_bytes"]) <= 0 or int(cfg["backup_count"]) <= 0:
        # Ноль в любом из двух у RotatingFileHandler — «ротации нет»: файл рос бы без конца.
        print("⚠️  [journal] max_bytes и backup_count обязаны быть больше нуля — журнал фактов не пишется")
        return None
    return Journal(root / str(cfg["file"]), int(cfg["max_bytes"]), int(cfg["backup_count"]))


class Journal:
    """Журнал фактов с ротацией по размеру (FS-18): `RotatingFileHandler` с пределами из объявления. Писатель один —
    процесс сервера: запись одного файла из нескольких процессов logging не согласует. Каталог появляется при первой
    записи, а не при сборке: объявление без записей не оставляет следов на диске."""

    def __init__(self, path: Path, max_bytes: int, backup_count: int):
        self.path = Path(path)
        self._handler = _Rotating(self.path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
                                  delay=True)
        self._handler.terminator = ""  # запись приносит свои переводы строк
        self._ready = False

    def write(self, entry: str) -> None:
        if not self._ready:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._ready = True
        self._handler.handle(logging.makeLogRecord({"msg": entry}))


class _Rotating(RotatingFileHandler):
    def handleError(self, record) -> None:  # ошибку записи — вызывающему: движок называет её один раз, а не глушит
        raise


class _Heard(logging.Handler):
    """Запись logging — записью журнала фактов: время, уровень, логгер, номер события, текст, трассировка. Сбой записи
    уходит в stderr (`handleError`), а не в ответ инструмента: отказ клиенту не становится падением из-за журнала."""

    def __init__(self, journal: Journal):
        super().__init__()
        self.journal = journal

    def emit(self, record: logging.LogRecord) -> None:
        try:
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
            entry = f"\n## [{when}] {record.levelname} {record.name}\n"
            event = getattr(record, "event", None)     # номер события — из extra узла incident (core/engine/engine.py)
            if event:
                entry += f"- **событие:** {event}\n"
            entry += f"- **запись:** {record.getMessage()}\n"
            if record.exc_info:
                entry += "```\n" + "".join(traceback.format_exception(*record.exc_info)) + "```\n"
            self.journal.write(entry)
        except Exception:
            self.handleError(record)


_attached: list[tuple[str, logging.Handler]] = []
