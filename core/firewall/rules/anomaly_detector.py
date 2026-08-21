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

    # Дефолтный список. Перекрывается декларацией firewall.yaml →
    # anomaly_detection.dangerous_tools (её читает core/firewall/firewall.py).
    DEFAULT_DANGEROUS_TOOLS = frozenset({
        "fs_delete", "fs_remove", "fs_move_outside",
        "config_delete", "system_shutdown", "system_restart",
    })

    def __init__(self, dangerous_tools: set[str] | None = None):
        # Конфигурируемый список вместо хардкода.
        self.dangerous_tools = set(dangerous_tools) if dangerous_tools is not None else set(self.DEFAULT_DANGEROUS_TOOLS)
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
