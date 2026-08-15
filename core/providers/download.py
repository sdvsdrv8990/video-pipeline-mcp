"""
core/providers/download.py — забрать результат провайдера по ссылке и положить на диск.

## Зачем отдельно от приёмки
`task_cycle.verify_download` отвечает на вопрос «файл действительно лёг?». Здесь — сама загрузка:
ссылку выдаёт провайдер, значит она приходит ИЗВНЕ, и обращаться по ней сервер должен с той же
опаской, что и к любому чужому вводу.

## Ссылка провайдера — не доверенный адрес
Скомпрометированный или просто ошибшийся провайдер может вернуть ссылку на внутренний адрес
(`http://169.254.169.254/…`, `localhost`) — и тогда сервер своими руками сходит туда, куда клиент
не достаёт. Поэтому схема и адрес проверяются до запроса: список схем и запрет приватных адресов
объявлены в `config/media_tasks.yaml → download.fetch`.

## Предел размера — до записи, а не после
Файл пишется потоком с подсчётом байт: узнать о гигабайтах ПОСЛЕ того, как они легли на диск,
поздно. Превышение объявленного предела обрывает загрузку и удаляет недописанный файл.
"""

import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

from .declaration import Declaration
from .task_cycle import TaskCycleError

CHUNK = 64 * 1024


class ResultDownloader:
    """Загрузка файла результата по ссылке провайдера — с проверкой адреса и предела размера."""

    def __init__(self, config_file: str | Path):
        self.config_file = Path(config_file)
        self._decl = Declaration(
            config_file, TaskCycleError, "ожидания",
            "Заведи config/media_tasks.yaml — правила загрузки объявлены там.")

    @property
    def config(self) -> dict:
        return self._decl.data

    @property
    def rules(self) -> dict:
        return ((self.config.get("download") or {}).get("fetch") or {})

    # ═══ Проверка адреса ═══

    def check_url(self, url: str) -> str:
        """Пустить запрос или отказать. Отказ — до сети, а не после."""
        parsed = urlparse(str(url or ""))
        schemes = [str(s).lower() for s in (self.rules.get("allow_schemes") or ["https"])]
        if parsed.scheme.lower() not in schemes:
            raise TaskCycleError(
                "DOWNLOAD_FORBIDDEN", f"Схема ссылки '{parsed.scheme or '(нет)'}' не разрешена.",
                reason=f"Загрузка идёт только по {', '.join(schemes)} — список в "
                       "config/media_tasks.yaml → download.fetch.allow_schemes.")
        host = parsed.hostname or ""
        if not host:
            raise TaskCycleError(
                "DOWNLOAD_FORBIDDEN", "В ссылке провайдера нет адреса.",
                reason="Ответ провайдера не содержит пригодной ссылки — смотри его журнал.")
        if self.rules.get("block_private_hosts", True) and self._is_private(host):
            raise TaskCycleError(
                "DOWNLOAD_FORBIDDEN", f"Ссылка ведёт на внутренний адрес: {host}",
                reason=("Сервер не ходит по ссылкам во внутреннюю сеть — так чужой ответ мог бы "
                        "достать то, до чего клиент не дотягивается. Проверь, что вернул провайдер."))
        return url

    @staticmethod
    def _is_private(host: str) -> bool:
        """Адрес внутренний? Проверяем все, куда резолвится имя, а не только литерал."""
        candidates: list[str] = []
        try:
            ipaddress.ip_address(host)
            candidates = [host]
        except ValueError:
            try:
                candidates = [str(info[4][0]) for info in socket.getaddrinfo(host, None)]
            except OSError:
                # Имя не резолвится — пусть отказывает сама загрузка, с её сообщением.
                return False
        for raw in candidates:
            ip = ipaddress.ip_address(raw)
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return True
        return False

    # ═══ Загрузка ═══

    def fetch(self, url: str, target: Path) -> dict:
        """Скачать по ссылке в target. Путь приходит уже проверенным (containment + тип файла)."""
        self.check_url(url)
        try:
            import httpx
        except ImportError as e:                            # pragma: no cover — есть в зависимостях
            raise TaskCycleError(
                "PROVIDER_FAILED", f"HTTP-клиент недоступен: {e}",
                reason="Поставь httpx в окружение сервера.") from e

        max_bytes = int(self.rules.get("max_bytes", 0) or 0)
        timeout = float(self.rules.get("timeout_sec", 120))
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with httpx.stream("GET", url, timeout=timeout, follow_redirects=False) as response:
                if response.status_code >= 400:
                    raise TaskCycleError(
                        "PROVIDER_FAILED", f"Провайдер ответил {response.status_code} на ссылку результата.",
                        reason="Ссылка могла протухнуть — прозвони статус задачи заново.")
                with open(target, "wb") as fh:
                    for chunk in response.iter_bytes(CHUNK):
                        written += len(chunk)
                        if max_bytes and written > max_bytes:
                            raise TaskCycleError(
                                "DOWNLOAD_FORBIDDEN",
                                f"Файл превысил объявленный предел {max_bytes} байт.",
                                reason="Подними download.fetch.max_bytes в config/media_tasks.yaml, "
                                       "если такой размер штатный.")
                        fh.write(chunk)
        except TaskCycleError:
            target.unlink(missing_ok=True)                  # недописанный файл не выдаём за результат
            raise
        except Exception as e:                              # noqa: BLE001 — сеть/таймаут/обрыв
            target.unlink(missing_ok=True)
            raise TaskCycleError(
                "PROVIDER_FAILED", f"Загрузка результата не удалась: {e}",
                reason="Сеть или провайдер оборвали передачу — повтори загрузку.") from e
        return {"url": url, "bytes": written, "path": str(target)}
