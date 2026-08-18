"""
core/providers/resolver.py — кто и какой моделью исполняет запрос (динамический конфиг).

## Назначение
Кем сейчас исполнять тип ресурса. Живой конфиг — строка листа `RESOURCE_LIMITS` в книге канала;
вся строка уходит адаптеру как параметры вызова, переключение = `table_update`, без рестарта.

## Границы
- ни одного имени провайдера в коде: имена, размеры, голоса, таймауты — данные канала, а какие
  столбцы служебные — `config/providers.yaml`;
- нет строк для типа ресурса — отказ с кодом реестра, а не «возьмём что-нибудь»;
- исчерпанный лимит уводит на объявленный fallback и ГОВОРИТ об этом; глубина цепочки
  ограничена, потому что данные правит ИИ и кольцо `A → B → A` там возможно.
"""

from pathlib import Path

from .declaration import Declaration


class ProviderError(Exception):
    """Ошибка выбора провайдера в формате контракта (код из server_reactions.yaml)."""

    def __init__(self, code: str, message: str, reason: str = "", suggested_tool: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.suggested_tool = suggested_tool


class ProviderResolver:
    """Выбор провайдера/модели по данным канала (`config/providers.yaml` описывает, где искать)."""

    def __init__(self, config_file: str | Path):
        self.config_file = Path(config_file)
        self._decl = Declaration(
            config_file, ProviderError, "провайдеров",
            "Заведи config/providers.yaml — откуда брать провайдера, объявлено там.")

    @property
    def config(self) -> dict:
        return self._decl.data

    # ═══ Состояние строки ═══

    def _exhausted(self, row: dict) -> bool:
        """Лимит исчерпан? Отрицательный лимит означает «без ограничения»."""
        lim = self.config.get("limits") or {}
        limit = row.get(lim.get("limit_column", "daily_limit"))
        usage = row.get(lim.get("usage_column", "current_usage")) or 0
        if limit is None or not isinstance(limit, (int, float)):
            return False
        if limit < float(lim.get("unlimited_below", 0)):
            return False
        return float(usage) >= float(limit)

    def _warning(self, row: dict) -> bool:
        """Пора предупредить: расход дошёл до объявленного порога."""
        lim = self.config.get("limits") or {}
        threshold = row.get(lim.get("warning_column", "warning_threshold"))
        usage = row.get(lim.get("usage_column", "current_usage")) or 0
        if threshold is None or not isinstance(threshold, (int, float)) or threshold < 0:
            return False
        return float(usage) >= float(threshold)

    def _params(self, row: dict) -> dict:
        """Всё, что не служебное, — параметры вызова. Новый параметр = новый столбец, не код."""
        meta = set(self.config.get("meta_columns") or [])
        return {k: v for k, v in row.items() if k not in meta and not k.startswith("_")}

    # ═══ Выбор ═══

    def resolve(self, rows: list[dict], resource_type: str, source: str = "") -> dict:
        """Кем исполнять `resource_type` прямо сейчас: строка данных → решение для адаптера."""
        src = self.config.get("source") or {}
        type_col = src.get("type_column", "resource_type")
        prov_col = src.get("provider_column", "provider")
        fb_col = src.get("fallback_column", "fallback_provider")

        candidates = [r for r in rows if r.get(type_col) == resource_type]
        if not candidates:
            raise ProviderError(
                "PROVIDER_NOT_CONFIGURED", f"Для ресурса '{resource_type}' не объявлен ни один провайдер.",
                reason=(f"Добавь строку в лист {src.get('sheet', 'RESOURCE_LIMITS')} книги канала: "
                        "провайдер, модель, лимит. Провайдер живёт в данных, не в коде."),
                suggested_tool="table_append")

        by_provider = {r.get(prov_col): r for r in candidates}
        chain: list[str] = []
        skipped: list[dict] = []
        current = candidates[0]
        depth = int(self.config.get("max_fallback_depth", 5))
        for _ in range(depth):
            name = str(current.get(prov_col) or "")
            if not name or name in chain:
                break                                   # пусто или кольцо в данных — дальше не идём
            chain.append(name)
            if not self._exhausted(current):
                return {
                    "resource_type": resource_type,
                    "provider": name,
                    # ID строки нужен, чтобы прибавить расход именно ей (служебное поле «_»
                    # не уходит в параметры вызова — фильтр в _params это уже делает).
                    "row_id": current.get("_row_id", ""),
                    # Строка целиком — для тех, чьё дело сама строка, а не вызов: учёт расхода
                    # меряется столбцом `usage_unit`, который в параметры вызова не уходит.
                    "row": {k: v for k, v in current.items() if not k.startswith("_")},
                    "params": self._params(current),
                    "warning": self._warning(current),
                    "exhausted_chain": skipped,
                    "chain": chain,
                    "source": source,
                }
            skipped.append({"provider": name, "reason": "лимит исчерпан"})
            nxt = current.get(fb_col)
            if not nxt or nxt not in by_provider:
                break
            current = by_provider[nxt]

        raise ProviderError(
            "PROVIDER_EXHAUSTED",
            f"Все провайдеры для '{resource_type}' исчерпали лимит: {', '.join(chain)}.",
            reason=("Подними daily_limit, обнули current_usage или добавь провайдера строкой в "
                    "лист провайдеров — всё это данные канала, правятся table_update/table_append."),
            suggested_tool="table_update")
