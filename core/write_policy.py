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
