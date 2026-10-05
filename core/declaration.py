"""core/declaration.py — единственная дверь к декларациям.

Одна загрузка на весь сервер: копии расходятся по мелочам — одна перечитывает файл, другая нет,
у третьей отсутствие файла означает «правило выключено». Правка объявления действует без
перезапуска. Битое объявление отвечает кодом реестра, а не исключением наружу, и класс исключения
передаёт вызывающий — по нему видно, где порвалось.
"""

from pathlib import Path
from typing import TypeVar, overload

import yaml

T = TypeVar("T")


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

    def optional(self) -> dict:
        """Декларация, отсутствие которой — решение вызывающего, а не отказ.

        Битость отказ ВСЕГДА: непрочитанный файл нечем отличить от пустого, и правило,
        собранное из пустоты, тихо перестаёт работать.
        """
        return self.data if self.config_file.exists() else {}

    @overload
    def need(self, *path: str) -> object: ...

    @overload
    def need(self, *path: str, kind: type[T]) -> T: ...

    def need(self, *path: str, kind: type | None = None) -> object:
        """Объявленное значение по пути. Ключа нет — код реестра, а не запасное значение в коде.

        Копия рядом с чтением (`sec.get("limit_column", "daily_limit")`) глушит опечатку в
        декларации: правка конфига не действует и об этом никто не узнаёт. `kind` — тип, которым
        вызывающий читает значение: другой — тоже код реестра с путём и обоими типами, а не сырой
        ValueError у `int("abc")` дальше по коду.
        """
        node: object = self.data
        for depth, key in enumerate(path, 1):
            if not isinstance(node, dict) or key not in node:
                raise self._error(
                    "SCHEMA_INVALID", f"В {self.config_file.name} не объявлено `{'.'.join(path)}`.",
                    reason=f"Добавь строку `{'.'.join(path[:depth])}`: сервер читает её оттуда, "
                           f"своей копии у него нет.")
            node = node[key]
        if kind is not None and not _is(node, kind):
            raise self._error(
                "SCHEMA_INVALID", f"В {self.config_file.name} `{'.'.join(path)}` — {type(node).__name__} "
                                  f"«{node}», а сервер читает его как {kind.__name__}.",
                reason=f"Поправь значение `{'.'.join(path)}`: тип задан тем, кто его читает, своей копии у сервера нет.")
        return node


def _is(value: object, kind: type) -> bool:
    """bool — подкласс int, но числом в декларации не считается; int годится там, где читают float."""
    if isinstance(value, bool):
        return kind is bool
    return isinstance(value, (int, float) if kind is float else kind)
