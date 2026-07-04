"""
core/firewall/rules/anomaly_detector.py — Детектор аномалий

## Назначение
Обнаружение подозрительных паттернов поведения.

## 4 уровня анализа

### 1. Код
- AnomalyDetector класс с методом check(request)
- Анализирует паттерны запросов
- Возвращает AnomalyResult

### 2. Поведение
- Следит за частотой и типами запросов
- Обнаруживает отклонения от нормы
- Блокирует подозрительную активность

### 3. Поток данных
```
Запрос → AnomalyDetector.check()
   ├── detected=False → пропускаем
   └── detected=True → блокируем
```

### 4. Долгосрочный (6 мес)
- Норма поведения определяется автоматически
- Аномалии становятся правилами

## Порядок полей
1. Пороги (thresholds)
2. Статистика (history)
3. Методы (check, get_stats)
"""

import time
from collections import defaultdict
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

    D17: time-based anomaly detection УДАЛЁН — таймеры пропускают события
    и дают ложные срабатывания. Оставлен ТОЛЬКО event-based detection:
    проверка конкретного запроса на опасные инструменты.

    Attributes:
        dangerous_tools: Множество деструктивных инструментов (D18)
    """

    # D18: дефолтный список (если не задан через config/ops/*.yaml).
    DEFAULT_DANGEROUS_TOOLS = frozenset({
        "fs_delete", "fs_remove", "fs_move_outside",
        "config_delete", "system_shutdown", "system_restart",
    })

    def __init__(self, dangerous_tools: set[str] | None = None):
        # D18: конфигурируемый список вместо хардкода.
        self.dangerous_tools = set(dangerous_tools) if dangerous_tools is not None else set(self.DEFAULT_DANGEROUS_TOOLS)
        self._detected = 0

    def check(self, request: FirewallRequest) -> AnomalyResult:
        """Проверка запроса на аномалии.

        D17: только event-based проверки (опасные инструменты).
        Time-based counting удалён — таймеры пропускают события.

        Args:
            request: Запрос для проверки

        Returns:
            AnomalyResult
        """
        # D8/D18: проверяем ИМЯ ИНСТРУМЕНТА на опасность.
        if request.method == "tools/call" and isinstance(request.params, dict):
            method = request.params.get("name") or "tools/call"
        else:
            method = request.method

        # D18: event-based — конкретный запрос содержит опасный инструмент.
        if method in self.dangerous_tools:
            self._detected += 1
            return AnomalyResult(
                detected=True,
                reason=f"Подозрительный инструмент: {method}",
                severity="high"
            )

        return AnomalyResult(detected=False)

    def get_detected(self) -> int:
        """Получение количества обнаруженных аномалий.

        Returns:
            Количество аномалий
        """
        return self._detected
