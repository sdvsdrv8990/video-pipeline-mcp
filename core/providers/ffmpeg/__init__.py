"""
core/providers/ffmpeg/__init__ — точка входа для FFmpeg провайдера

## Назначение
Из любого места сервера:
    from core.providers.ffmpeg import FFMpegAdapter

## Архитектурные связи
- Использует: core.contracts (ToolResult, ErrorDetail, TaskStatus, Fact)
- Используется: ПОКА НИКЕМ — инструментов монтажа в сервере нет. Адаптер стоит готовым (честные
  NotImplementedError внутри), и это состояние названо здесь намеренно: «используется группой X»
  про несуществующую группу читалось бы как рабочая проводка.
"""

from .ffmpeg_adapter import FFMpegAdapter

__all__ = ["FFMpegAdapter"]
