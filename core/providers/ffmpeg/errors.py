"""core/providers/ffmpeg/errors.py — отказ движка монтажа в форме контракта.

Своё имя исключения нужно обёртке: по нему она отличает отказ монтажа от чужого и не глотает
посторонние.
"""

from core.contracts.errors import ContractError


class FfmpegError(ContractError):
    """Отказ сборки, запуска или приёмки рендера: код реестра + пояснение."""
