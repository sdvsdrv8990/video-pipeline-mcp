"""
core/providers/stt/__init__ — точка входа для STT провайдера

## Назначение
Из любого места сервера:
    from core.providers.stt import StableTSAdapter
"""

from .stable_ts_adapter import StableTSAdapter

__all__ = ["StableTSAdapter"]
