"""
core/montage/errors.py — отказ перевода «книга ↔ монтаж» в форме контракта.

## Назначение
Зона своя, поэтому и имя своё: обёртка по нему отличает отказ маппинга от отказа движка ffmpeg
и не глотает чужие исключения. Коды — общие, из `config/server_reactions.yaml`.
"""

from core.contracts.errors import ContractError


class MontageError(ContractError):
    """Отказ чтения замысла сцены из книги или записи результата в неё."""
