"""core/declaration.py — единственная дверь к декларациям.

Одна загрузка на весь сервер: копии расходятся по мелочам — одна перечитывает файл, другая нет,
у третьей отсутствие файла означает «правило выключено». Правка объявления действует без
перезапуска. Битое объявление отвечает кодом реестра, а не исключением наружу, и класс исключения
передаёт вызывающий — по нему видно, где порвалось.
"""

from pathlib import Path
from typing import Generic, TypeVar, overload

import yaml
from pydantic import BaseModel, ValidationError

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


M = TypeVar("M", bound=BaseModel)


class Declared(Declaration, Generic[M]):
    """Декларация со схемой: файл проверяется целиком при загрузке, читатель получает неизменяемый объект.

    Перечитывание по mtime собирает новый объект и переставляет ссылку — читатель видит объект целиком.
    Правка, не прошедшая проверку, — отказ на каждое чтение, пока её не починят, без тихого отката к прежнему.
    """

    def __init__(self, config_file: str | Path, error_cls, subject: str, hint: str, schema: type[M]):
        super().__init__(config_file, error_cls, subject, hint)
        self._schema = schema
        self._model: M | None = None
        self._model_mtime: float | None = None

    @property
    def model(self) -> M:
        data = self.data
        if self._model is None or self._model_mtime != self._mtime:
            try:
                model = self._schema.model_validate(data)
            except ValidationError as e:
                errors = _schema_errors(e)
                # Без цепочки причины: текст ValidationError несёт пришедшие значения, а журнал отказа пишет причину.
                raise self._error(
                    "DECLARATION_INVALID", errors=errors,
                    facts={"file": self.config_file.name,
                           "details": "; ".join(f"{x['field'] or '—'} ({x['rule']})" for x in errors)}) from None
            self._model, self._model_mtime = model, self._mtime
        return self._model


def _schema_errors(error: ValidationError) -> list[dict]:
    """Ошибки проверки — путь ключа, правило и что ждали; пришедшее значение в ответ не идёт: в декларациях бывают адреса."""
    return [{"field": ".".join(str(part) for part in x["loc"]), "rule": x["type"], "expected": x["msg"]}
            for x in error.errors()]
