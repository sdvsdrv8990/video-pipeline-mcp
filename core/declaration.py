"""core/declaration.py — единственная дверь к декларациям.

Одна загрузка на весь сервер: копии расходятся по мелочам — одна перечитывает файл, другая нет,
у третьей отсутствие файла означает «правило выключено». Правка объявления действует без
перезапуска. Битое объявление отвечает кодом реестра, а не исключением наружу, и класс исключения
передаёт вызывающий — по нему видно, где порвалось.
"""

import os
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Generic, TypeVar, overload

import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from core.declaration_schemas import STRICT

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)


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
                # Без цепочки причины: текст ошибки YAML несёт строку файла вместе со значениями.
                raise self._invalid([_yaml_error(e)]) from None
            self._data, self._mtime = data, mtime
        return self._data

    def _invalid(self, errors: list[dict]) -> Exception:
        """Единственный отказ на битую декларацию — битый YAML, нет ключа, не тот тип, схема: путь ключа и правило,
        без пришедших значений. Файл назван от каталога деклараций; файл вне него — только имя: путь хоста в ответ не идёт."""
        root, file = declarations_root(), self.config_file.resolve()
        return self._error(
            "DECLARATION_INVALID", errors=errors,
            facts={"file": file.relative_to(root).as_posix() if file.is_relative_to(root) else file.name,
                   "details": "; ".join(f"{x['field'] or '—'} ({x['rule']})" for x in errors)})

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
        вызывающий читает значение: другой — тот же отказ двери с путём и правилом схемы, а не сырой
        ValueError у `int("abc")` дальше по коду.
        """
        node: object = self.data
        for depth, key in enumerate(path, 1):
            if not isinstance(node, dict) or key not in node:
                raise self._invalid(_missing(path[:depth]))
            node = node[key]
        if kind is not None:
            try:
                _adapter(kind).validate_python(node)
            except ValidationError as e:
                raise self._invalid(_schema_errors(e, path)) from None
        return node

    def section(self, *path: str, schema: type[M]) -> M:
        """Раздел декларации по пути — объект своей схемы: проверен целиком и неизменяем; битый — тот же отказ двери
        с путём ключа от корня файла. Схема — модель со своей строгостью, поэтому без `_adapter`."""
        node = self.need(*path)
        try:
            return schema.model_validate(node)
        except ValidationError as e:
            raise self._invalid(_schema_errors(e, path)) from None


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
                # Без цепочки причины: текст ValidationError несёт пришедшие значения, а журнал отказа пишет причину.
                raise self._invalid(_schema_errors(e)) from None
            self._model, self._model_mtime = model, self._mtime
        return self._model


def _schema_errors(error: ValidationError, prefix: tuple[str, ...] = ()) -> list[dict]:
    """Ошибки проверки — путь ключа, правило и что ждали; пришедшее значение в ответ не идёт: в декларациях бывают адреса."""
    return [{"field": ".".join(str(part) for part in (*prefix, *x["loc"])), "rule": x["type"], "expected": x["msg"]}
            for x in error.errors()]


def _yaml_error(error: yaml.YAMLError) -> dict:
    """Битый YAML — своё правило (у схемы его нет), место — строка и столбец; строку файла не берём: в ней значения."""
    mark = getattr(error, "problem_mark", None)
    return {"field": f"{mark.line + 1}:{mark.column + 1}" if mark else "", "rule": "yaml_syntax",
            "expected": str(getattr(error, "problem", None) or type(error).__name__)}


def _missing(path: tuple[str, ...]) -> list[dict]:
    """Нет ключа — то же правило и текст, что у схемы двери: ошибку собирает pydantic, а не строка в коде."""
    return _schema_errors(ValidationError.from_exception_data("need", [{"type": "missing", "loc": path, "input": None}]))


@cache
def _adapter(kind: type) -> TypeAdapter:
    """Проверка типа значения тем же строгим режимом, что у схем деклараций: «5» не число, bool не число."""
    return TypeAdapter(kind, config=STRICT)


def declarations_root(environ: Mapping[str, str] = os.environ) -> Path:
    """Каталог деклараций: `MCP_CONFIG` (прогон рядом с боевым) или `config/` рядом с кодом сервера — одно решение
    для сервера, установщика, подсказки отказа и харнесса, который поднимает сервер со своей средой."""
    return Path(environ.get("MCP_CONFIG") or (Path(__file__).resolve().parents[1] / "config")).resolve()
