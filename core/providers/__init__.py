"""core/providers — адаптеры внешних провайдеров + выбор провайдера по данным канала."""

from .resolver import ProviderError, ProviderResolver

__all__ = ["ProviderResolver", "ProviderError"]
