"""core/montage/errors.py — отказ перевода «книга ↔ монтаж».

Зона своя, поэтому и имя своё: по нему обёртка отличает отказ маппинга от отказа движка ffmpeg и не
глотает чужие исключения.
"""

from core.contracts.errors import ContractError


class MontageError(ContractError):
    """Отказ чтения замысла сцены из книги или записи результата в неё."""
