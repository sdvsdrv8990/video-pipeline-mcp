"""
core/providers/tts/litellm_tts.py — LiteLLM TTS Adapter

## Назначение
Адаптер для озвучки через LiteLLM (облако/локально).
Claude дёргает tts_* инструменты → адаптер общается с LiteLLM → результат.

## 4 уровня анализа

### 1. Код
- LiteLLMTTSAdapter — класс для работы с LiteLLM TTS API
- Методы: trigger_generation, poll_status, download_audio
- Каждый метод возвращает ToolResult/TaskStatus

### 2. Поведение
- Claude шлёт trigger_tts_generation → получает task_id
- Claude опрашивает poll_tts_status → видит progress
- Claude скачивает через download_audio → получает файл
- Ошибки маппятся на PROVIDER_FAILED / CONTENT_REJECTED

### 3. Поток данных
```
Claude → trigger_tts{model, input, voice, ...}
   → LiteLLM API: /audio/speech
   → ответ: {task_id} или {audio: base64}
   → ToolResult{task_id} или ToolResult{file_path}

Claude → poll_tts_status{task_id}
   → LiteLLM API: GET /tasks/{task_id}
   → TaskStatus{status, progress}

Claude → download_audio{task_id}
   → LiteLLM API: GET /tasks/{task_id}/result
   → файл → verify → ToolResult{file_path, verified}
```

### 4. Долгосрочный (6 мес)
- Все TTS-операции через этот адаптер
- Ошибки накапливаются → Claude учится распознавать
- Выбор модели оптимизируется по cost/quality

## Какие данные нужны для вызова
- model: str — модель из channel_config.RESOURCE_LIMITS
- input: str — текст для озвучки (из сценария)
- voice: str — голос
- response_format: str — формат аудио (wav, mp3)
- speed: float — скорость (опционально)

## Какие данные возвращаются
- file_path: str — путь к скачанному аудио
- verified: bool — файл валиден
- duration_sec: float — длительность (из метаданных)
"""

from core.contracts import ToolResult, ErrorDetail, Recovery, TaskStatus, Fact


class LiteLLMTTSAdapter:
    """Адаптер для TTS через LiteLLM.

    Attributes:
        api_url: URL LiteLLM API
        api_key: API ключ
        timeout: Таймаут запросов (сек)
    """

    def __init__(self, api_url: str, api_key: str, timeout: int = 60):
        self.api_url = api_url
        self.api_key = api_key
        self.timeout = timeout

    async def trigger_generation(
        self,
        model: str,
        input_text: str,
        voice: str,
        response_format: str = "wav",
        speed: float = 1.0
    ) -> ToolResult:
        """Фаза 1: Запуск генерации TTS.

        Args:
            model: Модель TTS (из RESOURCE_LIMITS)
            input_text: Текст для озвучки
            voice: Голос
            response_format: Формат аудио
            speed: Скорость речи

        Returns:
            ToolResult с task_id (async) или file_path (sync)
        """
        # TODO: Реализовать вызов LiteLLM API
        # 1. POST /audio/speech
        # 2. Если sync → сразу файл → verify → ToolResult
        # 3. Если async → task_id → ToolResult

        raise NotImplementedError("trigger_generation будет реализован при подключении LiteLLM")

    async def poll_status(self, task_id: str) -> TaskStatus:
        """Фаза 2: Проверка статуса генерации.

        Args:
            task_id: ID задачи

        Returns:
            TaskStatus с текущим статусом
        """
        # TODO: Реализовать вызов LiteLLM API
        # 1. GET /tasks/{task_id}
        # 2. Получить статус + progress

        raise NotImplementedError("poll_status будет реализован при подключении LiteLLM")

    async def download_audio(self, task_id: str) -> ToolResult:
        """Фаза 3: Скачивание аудио.

        Args:
            task_id: ID задачи

        Returns:
            ToolResult с путём к файлу и verify
        """
        # TODO: Реализовать вызов LiteLLM API
        # 1. GET /tasks/{task_id}/result
        # 2. Скачать файл
        # 3. Verify (≠ 0 байт, играбелен)
        # 4. Вернуть ToolResult

        raise NotImplementedError("download_audio будет реализован при подключении LiteLLM")

    def _map_error(self, api_error: dict) -> ErrorDetail:
        """Маппинг ошибки LiteLLM на нашу систему.

        Args:
            api_error: Ошибка от LiteLLM API

        Returns:
            ErrorDetail с кодом ошибки
        """
        error_code = api_error.get("code", "unknown")

        if error_code == "content_policy_violation":
            return ErrorDetail(
                code="CONTENT_REJECTED",
                message=api_error.get("message", "Контент отклонён модерацией"),
                recovery=Recovery(reason="Переформулировать промпт или сменить модель")
            )

        return ErrorDetail(
            code="PROVIDER_FAILED",
            message=str(api_error),
            recovery=Recovery(reason="Технический сбой LiteLLM, повторить или сменить провайдера")
        )
