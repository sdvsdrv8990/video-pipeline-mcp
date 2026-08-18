"""
core/providers/declaration.py — общая загрузка YAML-деклараций слоя провайдеров.

## Назначение
Одна загрузка YAML на весь слой провайдеров: резолвер, цикл ожидания и учёт расхода читают
свои декларации одинаково — иначе копии расходятся по мелочам (одна перечитывает, другая нет).

## Границы
- правка YAML действует без перезапуска: содержимое перечитывается по изменению mtime;
- нет файла или битый YAML — код реестра (`TEMPLATE_NOT_FOUND` / `SCHEMA_INVALID`), а не
  исключение наружу; класс исключения передаёт вызывающий, чтобы по нему было видно, где порвалось.
"""

from pathlib import Path

import yaml


class Declaration:
    """YAML-декларация: перечитывается по mtime, отсутствие и битость — коды реестра."""

    def __init__(self, config_file: str | Path, error_cls, subject: str, hint: str):
        self.config_file = Path(config_file)
        self._error = error_cls
        self._subject = subject
        self._hint = hint
        self._data: dict | None = None
        self._mtime: float = 0.0

    @property
    def data(self) -> dict:
        if not self.config_file.exists():
            raise self._error(
                "TEMPLATE_NOT_FOUND", f"Нет декларации {self._subject}: {self.config_file.name}",
                reason=self._hint)
        mtime = self.config_file.stat().st_mtime
        if self._data is None or mtime != self._mtime:
            try:
                data = yaml.safe_load(self.config_file.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                raise self._error(
                    "SCHEMA_INVALID", f"Битый {self.config_file.name}: {e}",
                    reason="Почини YAML — без него сервер не знает, как работать.") from e
            self._data, self._mtime = data, mtime
        return self._data
