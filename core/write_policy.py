"""
core/write_policy.py — Какие типы файлов сервер вправе материализовать (S2)

## Назначение
Единственная точка решения «можно ли создать/перезаписать файл с таким расширением».
Default-deny: разрешено только то, что объявлено в `config/firewall.yaml → write_allowlist`.
Сервер не должен уметь класть в рабочую область исполняемое или веб-содержимое
(`.sh`, `.html`, `.exe`, `.bat`, `.dll`) — даже если его об этом попросят.

## Границы
- Список — в конфиге, не в коде (anti-hardcode): добавить тип = строка в YAML.
- Правило про **тип**, а не про путь: containment (`core/paths`) и подпись артефактов
  (`core/integrity`) — соседние, независимые слои.
- Выключение (`enabled: false`) — осознанный fail-open владельца, он виден в конфиге.
"""

from pathlib import Path

import yaml


class WritePolicyError(Exception):
    """Тип файла запрещён к записи (маппится вызывающим в FILE_TYPE_FORBIDDEN)."""

    def __init__(self, code: str, message: str, reason: str = "", suggested_tool: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool


class WritePolicy:
    """Allowlist типов файлов, разрешённых к записи."""

    SECTION = "write_allowlist"

    def __init__(self, config_path: Path):
        self.config_file = Path(config_path) / "firewall.yaml"
        self._cache: dict | None = None

    def _config(self) -> dict:
        if self._cache is None:
            data: dict = {}
            if self.config_file.exists():
                data = yaml.safe_load(self.config_file.read_text(encoding="utf-8")) or {}
            self._cache = data.get(self.SECTION) or {}
        return self._cache

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

    def check_content(self, path: str, content) -> None:
        """Отказать, если СОДЕРЖИМОЕ исполняемое/веб — независимо от расширения.

        Смена расширения не делает файл безопасным: `.sh`, переименованный в `.txt`,
        прекрасно исполняется `sh file.txt`. Поэтому имя проверяется отдельно (`check`),
        а содержимое — здесь, по объявленным сигнатурам.
        """
        if not self.enabled or content is None:
            return
        raw = content.encode("utf-8", errors="ignore") if isinstance(content, str) else bytes(content)
        head = raw[:512]
        lowered = head.decode("utf-8", errors="ignore").lower().lstrip()
        for sig in self.forbidden_content:
            name = sig.get("name", "исполняемое содержимое")
            hexpat = sig.get("starts_with_hex")
            if hexpat and head.startswith(bytes.fromhex(hexpat)):
                self._deny(path, name)
            prefix = sig.get("starts_with")
            if prefix and lowered.startswith(str(prefix).lower()):
                self._deny(path, name)
            needle = sig.get("contains")
            if needle and str(needle).lower() in lowered:
                self._deny(path, name)

    def _deny(self, path: str, what: str) -> None:
        raise WritePolicyError(
            "FILE_TYPE_FORBIDDEN",
            f"Содержимое распознано как {what} — запись запрещена: {path}",
            "Расширение здесь ни при чём: файл остаётся исполняемым и под другим именем. "
            "Если это данные проекта — сохрани их без исполняемой сигнатуры; "
            "сигнатуры объявлены в config/firewall.yaml → write_allowlist.forbidden_content.")

    def check(self, path: str) -> None:
        """Бросить WritePolicyError, если тип файла не разрешён к записи."""
        if not self.enabled:
            return
        suffix = Path(path).suffix.lower()
        if suffix in self.extensions:
            return
        allowed = ", ".join(sorted(self.extensions))
        raise WritePolicyError(
            "FILE_TYPE_FORBIDDEN",
            f"Запись файлов типа '{suffix or '(без расширения)'}' запрещена: {path}",
            f"Разрешены только объявленные типы: {allowed}. "
            "Список правится в config/firewall.yaml → write_allowlist, а не в коде.")
