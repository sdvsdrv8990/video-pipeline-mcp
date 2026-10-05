"""core/packages — пакеты среды расчёта: суд «что уже стоит» без сети, план — разница, действие — только по плану."""

from .manager import PackageError, Packages

__all__ = ["PackageError", "Packages"]
