"""core/providers/usage.py — учёт расхода: после вызова счётчик провайдера двигается.

Лимиты и fallback резолвера — теория, пока счётчик стоит на месте. Способы ИЗМЕРИТЬ — закрытый
вокабуляр в коде, а назначение способа провайдеру — данные канала; неизвестная единица есть отказ,
потому что тихий ноль означал бы вечный лимит. Счётчик пишется точечно в снимок, мимо очереди
записи: та копится до осознанного применения, то есть расход не двигался бы до чужого вызова, а
применение утащило бы вместе с ним ВСЕ накопленные чужие операции.
"""

from pathlib import Path

from core.declaration import Declaration
from .resolver import ProviderError

# Как измерить единицу. Вокабуляр закрытый: новый СПОСОБ мерить — это код, а вот назначение
# способа провайдеру — данные (`usage_unit` в строке).
MEASURES = ("fixed", "input_length", "output_count")


class UsageLedger:
    """Измерение расхода по объявленной единице + запись счётчика в строку канала."""

    def __init__(self, config_file: str | Path, state_manager):
        self.config_file = Path(config_file)
        self.state = state_manager
        self._decl = Declaration(config_file, ProviderError, "провайдеров")

    @property
    def config(self) -> dict:
        return self._decl.data

    # ═══ Сколько ═══

    def unit(self, row: dict) -> str:
        """Единица расхода этой строки: из данных канала, иначе объявленный дефолт.

        Меряем по СТРОКЕ, а не по параметрам вызова: `usage_unit` — служебный столбец и в
        параметры не уходит (иначе учёт молча съезжал бы на дефолт «вызов»).
        """
        cfg = self.config.get("usage") or {}
        declared = str(row.get(cfg.get("unit_column", "usage_unit")) or "").strip()
        return declared or str(cfg.get("default_unit", "call"))

    def measure(self, row: dict, text: str = "", files: int = 0) -> float:
        """Сколько единиц потратил этот вызов."""
        cfg = self.config.get("usage") or {}
        unit = self.unit(row)
        how = str((cfg.get("units") or {}).get(unit, "")).strip()
        if how not in MEASURES:
            raise ProviderError("USAGE_UNIT_UNKNOWN", facts={"unit": unit})
        if how == "input_length":
            return float(len(text or ""))
        if how == "output_count":
            return float(files)
        return 1.0

    # ═══ Запись ═══

    def charge(self, table: str, sheet: str, row_id: str, amount: float) -> dict:
        """Прибавить расход строке провайдера. Возвращает «было → стало» для отчёта."""
        column = ((self.config.get("limits") or {}).get("usage_column")) or "current_usage"
        if not row_id:                                  # строка из декларации-дефолта: в книге канала её ещё нет
            raise ProviderError("ROW_NOT_FOUND", facts={"sheet": sheet})
        # Под замком снимка: очередь таблицы, применённая в соседнем потоке, не затрёт расход, а расход — её строки.
        with self.state.edit_snapshot(table) as snapshot:
            row = ((snapshot.get(sheet) or {}).get("rows") or {}).get(row_id)
            if row is None:
                raise ProviderError("ROW_NOT_FOUND", facts={"sheet": sheet, "row": row_id})
            before = row.get(column) or 0
            before = float(before) if isinstance(before, (int, float)) else 0.0
            after = before + float(amount)
            # Счётчик целый, если целыми были обе части: иначе в книге заводится 3.0 вместо 3.
            row[column] = int(after) if float(after).is_integer() else after
        return {"row_id": row_id, "column": column, "unit": None,
                "amount": amount, "before": before, "after": row[column]}

    def charge_call(self, table: str, sheet: str, decision: dict,
                    text: str = "", files: int = 0) -> dict:
        """Учесть один состоявшийся вызов по решению резолвера."""
        row = decision.get("row") or decision.get("params") or {}
        amount = self.measure(row, text=text, files=files)
        report = self.charge(table, sheet, decision.get("row_id", ""), amount)
        report["unit"] = self.unit(row)
        return report
