"""
core/providers/ffmpeg/errors.py — отказ движка монтажа в форме контракта.

## Назначение
Своё имя исключения нужно обёртке: по нему она отличает отказ монтажа от чужого и не глотает
посторонние. Поля и коды — общие, из `config/server_reactions.yaml`.
"""

from core.contracts.errors import ContractError


class FfmpegError(ContractError):
    """Отказ сборки, запуска или приёмки рендера: код реестра + пояснение."""
