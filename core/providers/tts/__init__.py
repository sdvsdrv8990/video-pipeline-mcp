"""
core/providers/tts/__init__ — точка входа для TTS провайдера

## Назначение
Из любого места сервера:
    from core.providers.tts import LiteLLMTTSAdapter
"""

from .litellm_tts import LiteLLMTTSAdapter

__all__ = ["LiteLLMTTSAdapter"]
