"""
core/providers/ffmpeg/__init__ — точка входа для FFmpeg провайдера

## Назначение
Из любого места сервера:
    from core.providers.ffmpeg import FFMpegAdapter

## Архитектурные связи
- Использует: core.contracts (ToolResult, ErrorDetail, TaskStatus, Fact)
- Используется: tools/video/* (инструменты монтажа)
"""

from .ffmpeg_adapter import FFMpegAdapter

__all__ = ["FFMpegAdapter"]
