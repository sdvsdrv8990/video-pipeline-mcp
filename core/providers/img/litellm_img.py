"""
core/providers/img/litellm_img.py — LiteLLM IMG Adapter

## Назначение
Адаптер для генерации картинок через LiteLLM (облако/локально).
Claude дёргает img_* инструменты → адаптер общается с LiteLLM → результат.
"""

from core.contracts import ToolResult, ErrorDetail, Recovery, TaskStatus, Fact


class LiteLLMIMGAdapter:
    """Адаптер для генерации картинок через LiteLLM.

    Attributes:
        api_url: URL LiteLLM API
        api_key: API ключ
        timeout: Таймаут запросов (сек)
    """

    def __init__(self, api_url: str, api_key: str, timeout: int = 120):
        self.api_url = api_url
        self.api_key = api_key
        self.timeout = timeout

    async def trigger_generation(
        self,
        model: str,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1
    ) -> ToolResult:
        """Фаза 1: Запуск генерации изображения.

        Args:
            model: Модель генерации (FLUX, SD, DALL-E)
            prompt: Промпт на ОДИН исходник
            size: Размер изображения
            n: Количество вариантов

        Returns:
            ToolResult с task_id (async) или image_paths (sync)
        """
        # TODO: Реализовать вызов LiteLLM API
        # 1. POST /image/generations
        # 2. Если sync → сразу изображения → verify → ToolResult
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

    async def download_image(self, task_id: str) -> ToolResult:
        """Фаза 3: Скачивание изображения.

        Args:
            task_id: ID задачи

        Returns:
            ToolResult с путём к файлу и verify
        """
        # TODO: Реализовать вызов LiteLLM API
        # 1. GET /tasks/{task_id}/result
        # 2. Скачать файл
        # 3. Verify (≠ 0 байт, валидное изображение)
        # 4. Вернуть ToolResult

        raise NotImplementedError("download_image будет реализован при подключении LiteLLM")

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
                message=api_error.get("message", "Изображение отклонено модерацией"),
                recovery=Recovery(reason="Переформулировать промпт или сменить модель")
            )

        return ErrorDetail(
            code="PROVIDER_FAILED",
            message=str(api_error),
            recovery=Recovery(reason="Технический сбой LiteLLM, повторить или сменить провайдера")
        )
