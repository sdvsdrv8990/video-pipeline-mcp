"""core/providers/ffmpeg/errors.py — отказ движка монтажа в форме контракта.

Своё имя исключения нужно обёртке: по нему она отличает отказ монтажа от чужого и не глотает
посторонние.
"""

from core.contracts.errors import ContractError


class FfmpegError(ContractError):
    """Отказ сборки, запуска или приёмки рендера: код реестра + пояснение."""


class FfmpegSaid(Exception):
    """Слова ffmpeg/ffprobe об отказе — причина (`raise … from`) для журнала сервера, а не текст ответа: в них пути
    хоста. Слово — последняя строка stderr: ею бинарь называет, что помешало."""

    def __init__(self, stderr: str):
        super().__init__(stderr.strip().rsplit("\n", 1)[-1] or "причина не названа")
