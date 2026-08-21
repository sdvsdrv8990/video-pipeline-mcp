"""
core/advice.py — второй канал объяснения: советы в УСПЕШНОМ ответе.

## Назначение
`server_reactions.yaml` объясняет ошибки — но у ИИ бывает выбор, о котором он не знает,
и промаха при этом нет. Тогда объяснять нечему реагировать: совет обязан приходить в момент
успеха. Тексты и маршруты живут в `config/recommendations.yaml`; здесь только чтение и
подстановка контекста — ни одной фразы в коде.

## Отказ
Нет файла или нет ключа → пустой список. Совет — не контракт: его отсутствие не должно ронять
операцию, которая уже выполнена. А вот СЛОМАННЫЙ файл глушить нельзя, иначе декларация тихо
перестанет работать — такой случай отдаём исключением.
"""

from pathlib import Path

import yaml

from core.contracts import ContractError


class AdviceError(ContractError):
    """Битая декларация советов: молчать нельзя, иначе канал тихо исчезнет."""


class Advice:
    """Реестр рекомендаций поверх `config/recommendations.yaml`."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._cache: dict | None = None
        self._mtime: float = 0.0

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        mtime = self.path.stat().st_mtime
        if self._cache is None or mtime != self._mtime:
            try:
                data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                raise AdviceError("ADVICE_INVALID", f"Битый {self.path.name}: {e}") from e
            if not isinstance(data, dict):
                raise AdviceError("ADVICE_INVALID",
                                  f"{self.path.name}: ожидался словарь ключ → список советов.")
            self._cache, self._mtime = data, mtime
        return self._cache

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
