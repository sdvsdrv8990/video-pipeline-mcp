"""
core/providers/img/__init__ — точка входа для IMG провайдера

## Назначение
Из любого места сервера:
    from core.providers.img import LiteLLMIMGAdapter
"""

from .litellm_img import LiteLLMIMGAdapter

__all__ = ["LiteLLMIMGAdapter"]
