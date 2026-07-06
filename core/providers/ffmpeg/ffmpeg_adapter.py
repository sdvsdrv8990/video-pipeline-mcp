"""
core/providers/ffmpeg/ffmpeg_adapter.py — FFmpeg Adapter

## Назначение
Единый интерфейс для работы с внешним FFmpeg MCP-сервером.
Claude видит только наш интерфейс, внешний MCP скрыт за адаптером.
"""

from core.contracts import ToolResult, ErrorDetail, Recovery, TaskStatus, Fact


class FFMpegAdapter:
    """Адаптер для работы с внешним FFmpeg MCP-сервером.

    Два уровня рендера:
    - draft: черновой рендер (быстро, для проверки)
    - final: финальный рендер (качественно, для публикации)

    Attributes:
        server_url: URL внешнего MCP-сервера
        timeout: Таймаут запросов (сек)
    """

    def __init__(self, server_url: str, timeout: int = 300):
        self.server_url = server_url
        self.timeout = timeout

    async def trigger_render(
        self,
        video_id: str,
        scene_id: str,
        render_stage: str,  # "draft" или "final"
        render_profile: str,  # профиль из channel_config.RENDER_CONFIG
        scene_data: dict,  # данные из video_data.SCENES
        layout_data: dict,  # данные из scene_layouts/
        output_path: str,  # videos/<video>/renders/
        derived_from_render_id: str | None = None  # для final: привязка к draft
    ) -> ToolResult:
        """Фаза 1: Запуск рендера.

        Args:
            video_id: ID видео
            scene_id: ID сцены
            render_stage: "draft" или "final"
            render_profile: Профиль рендера (codec/res/fps/crf из RENDER_CONFIG)
            scene_data: Данные сцены (assets, timestamps, animation)
            layout_data: Раскладка сцены (слоты, позиции, габариты)
            output_path: Путь для вывода видео
            derived_from_render_id: ID черновика (для финального рендера)

        Returns:
            ToolResult с task_id для поллинга
        """
        # TODO: Реализовать вызов внешнего MCP
        # 1. Сформировать запрос: render_stage + render_profile + scene_data + layout_data
        # 2. Отправить POST /render
        # 3. Получить task_id
        # 4. Вернуть ToolResult

        raise NotImplementedError("trigger_render будет реализован при подключении внешнего MCP")

    async def poll_render_status(self, task_id: str) -> TaskStatus:
        """Фаза 2: Проверка статуса рендера.

        Args:
            task_id: ID задачи рендера

        Returns:
            TaskStatus с текущим статусом
        """
        # TODO: Реализовать вызов внешнего MCP
        # 1. Отправить GET /render/{task_id}
        # 2. Получить статус + progress
        # 3. Вернуть TaskStatus

        raise NotImplementedError("poll_render_status будет реализован при подключении внешнего MCP")

    async def download_rendered(self, task_id: str) -> ToolResult:
        """Фаза 3: Скачивание и verify результата.

        Args:
            task_id: ID задачи рендера

        Returns:
            ToolResult с путём к файлу и verify
        """
        # TODO: Реализовать вызов внешнего MCP
        # 1. Отправить GET /render/{task_id}/result
        # 2. Скачать файл в output_path
        # 3. Verify: файл ≠ 0 байт, играбелен ( ffprobe )
        # 4. Вернуть ToolResult

        raise NotImplementedError("download_rendered будет реализован при подключении внешнего MCP")

    async def cancel_render(self, task_id: str) -> ToolResult:
        """Отмена рендера.

        Args:
            task_id: ID задачи рендера

        Returns:
            ToolResult с подтверждением отмены
        """
        # TODO: Реализовать вызов внешнего MCP
        # 1. Отправить DELETE /render/{task_id}
        # 2. Получить подтверждение
        # 3. Вернуть ToolResult

        raise NotImplementedError("cancel_render будет реализован при подключении внешнего MCP")

    async def render_full_pipeline(
        self,
        video_id: str,
        scene_id: str,
        render_profile: str,
        scene_data: dict,
        layout_data: dict,
        output_dir: str
    ) -> ToolResult:
        """Полный пайплайн: draft → verify → final → verify.

        Args:
            video_id: ID видео
            scene_id: ID сцены
            render_profile: Профиль рендера
            scene_data: Данные сцены
            layout_data: Раскладка сцены
            output_dir: Директория для renders/

        Returns:
            ToolResult с путями к draft и final
        """
        # 1. Trigger draft
        draft_result = await self.trigger_render(
            video_id=video_id,
            scene_id=scene_id,
            render_stage="draft",
            render_profile=render_profile,
            scene_data=scene_data,
            layout_data=layout_data,
            output_path=f"{output_dir}/{video_id}_draft.mp4"
        )
        if draft_result.status == "error":
            return draft_result

        # 2. Poll draft
        draft_task_id = draft_result.data["task_id"]
        while True:
            status = await self.poll_render_status(draft_task_id)
            if status.status in ("completed", "failed"):
                break
            # ждём...

        if status.status == "failed":
            return ToolResult(status="error", error=status.error)

        # 3. Download + verify draft
        draft_download = await self.download_rendered(draft_task_id)
        if draft_download.status == "error":
            return draft_download

        draft_render_id = draft_download.data["render_id"]

        # 4. Trigger final (с привязкой к draft)
        final_result = await self.trigger_render(
            video_id=video_id,
            scene_id=scene_id,
            render_stage="final",
            render_profile=render_profile,
            scene_data=scene_data,
            layout_data=layout_data,
            output_path=f"{output_dir}/{video_id}_final.mp4",
            derived_from_render_id=draft_render_id
        )
        if final_result.status == "error":
            return final_result

        # 5. Poll final
        final_task_id = final_result.data["task_id"]
        while True:
            status = await self.poll_render_status(final_task_id)
            if status.status in ("completed", "failed"):
                break

        if status.status == "failed":
            return ToolResult(status="error", error=status.error)

        # 6. Download + verify final
        final_download = await self.download_rendered(final_task_id)
        if final_download.status == "error":
            return final_download

        return ToolResult(
            status="success",
            data={
                "draft_render_id": draft_render_id,
                "draft_file_path": draft_download.data["file_path"],
                "final_render_id": final_download.data["render_id"],
                "final_file_path": final_download.data["file_path"],
                "verified": final_download.data["verified"]
            },
            facts=[
                Fact(type="RenderCompleted", data={"video_id": video_id, "scene_id": scene_id, "stage": "draft"}),
                Fact(type="RenderCompleted", data={"video_id": video_id, "scene_id": scene_id, "stage": "final"})
            ]
        )

    def _map_error(self, external_error: dict) -> ErrorDetail:
        """Маппинг ошибки внешнего MCP на нашу систему.

        FFmpeg — локальный движок, ошибки ближе к LOCAL_INFERENCE_FAILED:
        - битый исходник
        - нет файла
        - ошибка кодека
        - нехватка ресурса

        Args:
            external_error: Ошибка от внешнего MCP

        Returns:
            ErrorDetail с нашим кодом ошибки
        """
        error_type = external_error.get("type", "unknown")

        if error_type == "corrupt_input":
            return ErrorDetail(
                code="LOCAL_INFERENCE_FAILED",
                message=f"Битый исходник: {external_error.get('detail', '')}",
                recovery=Recovery(reason="Проверить исходные файлы, перегенерировать если нужно")
            )

        if error_type == "codec_error":
            return ErrorDetail(
                code="LOCAL_INFERENCE_FAILED",
                message=f"Ошибка кодека: {external_error.get('detail', '')}",
                recovery=Recovery(reason="Сменить кодек в RENDER_CONFIG или проверить совместимость")
            )

        if error_type == "resource_exhausted":
            return ErrorDetail(
                code="LOCAL_INFERENCE_FAILED",
                message=f"Нехватка ресурсов: {external_error.get('detail', '')}",
                recovery=Recovery(reason="Освободить ресурсы или уменьшить разрешение")
            )

        return ErrorDetail(
            code="LOCAL_INFERENCE_FAILED",
            message=str(external_error),
            recovery=Recovery(reason="Неизвестная ошибка FFmpeg, проверить логи")
        )
