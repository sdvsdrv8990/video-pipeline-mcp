"""
core/providers/stt/stable_ts_adapter.py — Stable-TS STT Adapter

## Назначение
Адаптер для транскрибации через stable-ts (локально).
Локальная модель, не требует внешнего API.

## 4 уровня анализа

### 1. Код
- StableTSAdapter — класс для работы с stable-ts
- Методы: trigger_transcription, parse_timestamps
- Обычно sync (один вызов), но на длинном аудио может быть async

### 2. Поведение
- Claude шлёт trigger_transcription → запуск stable-ts
- Обычно: сразу результат (sync)
- На длинном аудио: task_id → poll → result
- Ошибки маппятся на LOCAL_INFERENCE_FAILED

### 3. Поток данных
```
Claude → trigger_transcription{audio_path, model_size}
   → stable-ts: model.transcribe(audio_path)
   → результат: {segments: [{start, end, text, words}]}
   → ToolResult{timestamps, silence_map}

Claude → parse_timestamps{raw_segments}
   → обработка: пословные таймкоды + границы тишины
   → ToolResult{timestamp_start, timestamp_end, word_count, pace_wpm}
```

### 4. Долгосрочный (6 мес)
- Все STT-операции через этот адаптер
- Точность таймкодов влияет на качество сцен
- Ошибки ресурсов (GPU/память) будут накапливаться

## Какие данные нужны для вызова
- audio_path: str — путь к аудио-файлу
- model_size: str — размер модели (large/medium/base)
- word_timestamps: bool — пословные таймкоды
- suppress_silence: bool — подавление тишины
- vad: bool — Voice Activity Detection

## Какие данные возвращаются
- segments: list — сегменты с таймкодами
- silence_map: list — границы тишины
- word_count: int — количество слов
- duration_sec: float — длительность
"""

from core.contracts import ToolResult, ErrorDetail, Recovery, Fact


class StableTSAdapter:
    """Адаптер для STT через stable-ts (локально).

    Attributes:
        model_size: Размер модели (large/medium/base)
        device: Устройство (cuda/cpu)
    """

    def __init__(self, model_size: str = "large", device: str = "cuda"):
        self.model_size = model_size
        self.device = device
        self._model = None  # lazy loading

    async def trigger_transcription(
        self,
        audio_path: str,
        word_timestamps: bool = True,
        suppress_silence: bool = True,
        vad: bool = True
    ) -> ToolResult:
        """Фаза 1: Запуск транскрибации.

        Args:
            audio_path: Путь к аудио-файлу
            word_timestamps: Пословные таймкоды
            suppress_silence: Подавление тишины
            vad: Voice Activity Detection

        Returns:
            ToolResult с результатами транскрибации
        """
        # TODO: Реализовать вызов stable-ts
        # 1. Загрузить модель (если не загружена)
        # 2. model.transcribe(audio_path, ...)
        # 3. Извлечь таймкоды и тишину
        # 4. Вернуть ToolResult

        raise NotImplementedError("trigger_transcription будет реализован при подключении stable-ts")

    async def parse_timestamps(self, raw_segments: list) -> ToolResult:
        """Парсинг таймкодов из результатов stable-ts.

        Args:
            raw_segments: Сырые сегменты от stable-ts

        Returns:
            ToolResult с обработанными таймкодами
        """
        # TODO: Реализовать парсинг
        # 1. Извлечь пословные таймкоды
        # 2. Определить границы тишины (VAD)
        # 3. Рассчитать word_count, pace_wpm
        # 4. Вернуть ToolResult

        raise NotImplementedError("parse_timestamps будет реализован")

    def _map_error(self, inference_error: Exception) -> ErrorDetail:
        """Маппинг ошибки stable-ts на нашу систему.

        Args:
            inference_error: Исключение от stable-ts

        Returns:
            ErrorDetail с кодом LOCAL_INFERENCE_FAILED
        """
        return ErrorDetail(
            code="LOCAL_INFERENCE_FAILED",
            message=str(inference_error),
            recovery=Recovery(
                suggested_tool=None,
                reason="Локальный инференс не удался. Деградация модели: large→medium→base"
            )
        )
