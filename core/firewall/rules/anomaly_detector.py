"""
core/firewall/rules/anomaly_detector.py — Детектор аномалий

## Назначение
Обнаружение подозрительных паттернов поведения.
"""

from dataclasses import dataclass

from ..contracts import FirewallRequest


@dataclass
class AnomalyResult:
    """Результат проверки на аномалии.

    Attributes:
        detected: Обнаружена ли аномалия
        reason: Причина обнаружения
        severity: Серьёзность (low/med/high)
    """
    detected: bool
    reason: str = ""
    severity: str = "low"


class AnomalyDetector:
    """Детектор аномалий в поведении.

    Детекция ТОЛЬКО event-based: проверка конкретного запроса на опасные
    инструменты. Time-based по окну не годится — таймеры пропускают события
    и дают ложные срабатывания.

    Attributes:
        dangerous_tools: Множество деструктивных инструментов
    """

    def __init__(self, dangerous_tools: set[str] | None = None):
        # Запасного списка тут НЕТ намеренно: копия разошлась с декларацией и следила за
        # инструментами, которых у сервера нет. Без объявления детектор не следит ни за чем —
        # это состояние видно в статистике файрвола, а не выглядит рабочим.
        self.dangerous_tools = set(dangerous_tools or ())
        self._detected = 0

    def check(self, request: FirewallRequest) -> AnomalyResult:
        """Проверка запроса на аномалии (event-based, log-only).

        Опасный инструмент СЧИТАЕТСЯ (сигнал в get_stats), но НЕ блокируется
        (detected=False) — гейт деструктива клиентский, не на файрволе.
        """
        # имя инструмента для tools/call
        if request.method == "tools/call" and isinstance(request.params, dict):
            method = request.params.get("name") or "tools/call"
        else:
            method = request.method

        # опасный инструмент: считаем сигнал, но пропускаем
        if method in self.dangerous_tools:
            self._detected += 1
            return AnomalyResult(
                detected=False,
                reason=f"Деструктивный инструмент (log-only): {method}",
                severity="info"
            )

        return AnomalyResult(detected=False)

    def get_detected(self) -> int:
        """Получение количества обнаруженных аномалий.

        Returns:
            Количество аномалий
        """
        return self._detected
