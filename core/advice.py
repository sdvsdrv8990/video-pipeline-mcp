"""core/advice.py — второй канал объяснения: советы в УСПЕШНОМ ответе.

У модели бывает выбор, о котором она не знает, и промаха при этом нет: реагировать не на что, а
объяснить надо. Отсюда мягкий отказ — нет совета, значит пустой список: его отсутствие не вправе
ронять уже выполненную операцию. СЛОМАННОЕ объявление, наоборот, уходит исключением, иначе оно
тихо перестанет работать и никто не заметит.
"""

from pathlib import Path

from core.contracts import ContractError
from core.declaration import Declaration


class AdviceError(ContractError):
    """Битая декларация советов: молчать нельзя, иначе канал тихо исчезнет."""


class Advice:
    """Реестр рекомендаций поверх `config/recommendations.yaml`."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._decl = Declaration(
            path, AdviceError, "советов",
            "Заведи config/recommendations.yaml — что советовать клиенту, объявлено там.")

    def _load(self) -> dict:
        data = self._decl.data
        if not isinstance(data, dict):
            raise AdviceError("ADVICE_INVALID",
                              f"{self.path.name}: ожидался словарь ключ → список советов.")
        return data

    def get(self, key: str, **context) -> list[dict]:
        """Советы по ключу с подстановкой контекста в `params`.

        Неизвестный плейсхолдер остаётся текстом: тихо подставленная пустота выглядела бы
        как валидный параметр и увела бы ИИ не туда.
        """
        items = self._load().get(key) or []
        out = []
        for item in items:
            params = {}
            for name, value in (item.get("params") or {}).items():
                if isinstance(value, str) and "{" in value:
                    for ctx_name, ctx_value in context.items():
                        value = value.replace("{" + ctx_name + "}", str(ctx_value))
                params[name] = value
            out.append({"id": item.get("id", ""), "text": item.get("text", ""),
                        "tool": item.get("tool", ""), "params": params})
        return out
