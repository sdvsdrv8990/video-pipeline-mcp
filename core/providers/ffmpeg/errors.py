"""core/providers/ffmpeg/errors.py — отказ движка монтажа в форме контракта.

Своё имя исключения нужно обёртке: по нему она отличает отказ монтажа от чужого и не глотает
посторонние.
"""

from core.contracts.errors import ContractError
from core.environment import ChildSaid


class FfmpegError(ContractError):
    """Отказ сборки, запуска или приёмки рендера: код реестра + пояснение."""


class FfmpegSaid(ChildSaid):
    """Слова ffmpeg/ffprobe об отказе — свой тип, чтобы журнал и тесты отличали монтаж от прочих детей сервера."""
