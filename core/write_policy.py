"""core/write_policy.py — что сервер вправе записать по пути клиента: тип, содержимое, размер.

Единственная точка решения «можно ли создать или перезаписать файл», default-deny: разрешено только объявленное.
Судит её дверь слоя (`Files` с `policy=`) до первого байта, а не каждый пишущий сам. Правило про ТИП и объём, а не
про путь — containment и подпись артефактов живут отдельными слоями и друг друга не заменяют. Отказ — код реестра и
факты, текст даёт реестр.

Выключатель в объявлении — осознанное решение владельца, и оно видно. ОТСУТСТВИЕ объявления таким
решением не является и отвечает отказом.
"""

import shutil
from pathlib import Path

from core.contracts import ContractError
from core.declaration import Declaration


class WritePolicyError(ContractError):
    """Запись отвергнута политикой: FILE_TYPE_FORBIDDEN (тип или содержимое), FILE_TOO_LARGE (размер)."""


class WritePolicy:
    """Allowlist типов, сигнатуры исполняемого содержимого и предел размера записи."""

    SECTION = "write_allowlist"
    LIMITS = "file_limits"

    def __init__(self, config_path: Path):
        self.config_file = Path(config_path) / "firewall.yaml"
        self._decl = Declaration(
            self.config_file, WritePolicyError, "разрешённых к записи типов",
            "Заведи config/firewall.yaml — какие типы файлов сервер вправе класть, объявлено там.")

    def _config(self) -> dict:
        return self._decl.data.get(self.SECTION) or {}

    @property
    def enabled(self) -> bool:
        # Секции нет → правило не выключено молча, а просто не настроено: считаем выключенным
        # и это видно в конфиге (пустой список = «ничего нельзя» ломал бы сервер на старте).
        cfg = self._config()
        return bool(cfg.get("enabled")) and bool(cfg.get("extensions"))

    @property
    def extensions(self) -> set[str]:
        return {str(e).lower() for e in (self._config().get("extensions") or [])}

    @property
    def forbidden_content(self) -> list[dict]:
        return list(self._config().get("forbidden_content") or [])

    def admit(self, path: str, data: bytes | str | None = None, *, at: Path | None = None) -> None:
        """Суд записи до первого байта (FS-12/15): тип, размер, содержимое, место. `data=None` — байтов до записи нет
        (перенос): судится только тип. Предел размера — `file_limits.write_bytes`, он действует и при выключенном списке
        типов. `at` — настоящий путь записи: свободного места на его диске должно хватить на данные."""
        self.check(path)
        if data is None:
            return
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        limit = self._decl.need(self.LIMITS, "write_bytes", kind=int)
        if len(raw) > limit:
            raise WritePolicyError("FILE_TOO_LARGE", facts={"path": path, "size": len(raw), "limit": limit})
        self._content(path, raw)
        if at is not None:
            free = shutil.disk_usage(next(p for p in (at, *at.parents) if p.exists())).free
            if free < len(raw):
                raise WritePolicyError("NO_DISK_SPACE", facts={"path": path, "size": len(raw), "free": free})

    def _content(self, path: str, raw: bytes) -> None:
        """Отказать, если СОДЕРЖИМОЕ исполняемое/веб — независимо от расширения.

        Смена расширения не делает файл безопасным: `.sh`, переименованный в `.txt`,
        прекрасно исполняется `sh file.txt`. Поэтому имя проверяется отдельно (`check`),
        а содержимое — здесь, по объявленным сигнатурам.
        """
        if not self.enabled:
            return
        head = raw[:512]
        lowered = head.decode("utf-8", errors="ignore").lower().lstrip()
        for sig in self.forbidden_content:
            name = sig.get("name", "исполняемое содержимое")
            hexpat = sig.get("starts_with_hex")
            prefix = sig.get("starts_with")
            needle = sig.get("contains")
            if ((hexpat and head.startswith(bytes.fromhex(hexpat)))
                    or (prefix and lowered.startswith(str(prefix).lower()))
                    or (needle and str(needle).lower() in lowered)):
                raise WritePolicyError("FILE_TYPE_FORBIDDEN", facts={"path": path, "signature": name})

    def check(self, path: str) -> None:
        """Тип файла по объявленному списку; отказ — FILE_TYPE_FORBIDDEN с фактом `path`. Отдельно от `admit` — только
        там, где пишет не слой и байтов до записи нет (раннер, адаптер медиа)."""
        if not self.enabled:
            return
        if Path(path).suffix.lower() in self.extensions:
            return
        raise WritePolicyError("FILE_TYPE_FORBIDDEN", facts={"path": path})
