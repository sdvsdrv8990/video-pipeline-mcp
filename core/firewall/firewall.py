"""
core/firewall/firewall.py — Основной класс Firewall

## Назначение
Фильтрация ВСЕХ входящих запросов ДО ядра сервера.
Блокировка атак, rate limiting, детекция injection.
"""

from .contracts import FirewallDecision, FirewallRequest, FirewallResult
from .rules.rate_limiter import RateLimiter
from .rules.injection_detector import InjectionDetector
from .rules.ip_blocklist import IPBlocklist
from .rules.anomaly_detector import AnomalyDetector


class Firewall:
    """Файрвол MCP-сервера.

    Фильтрует ВСЕ входящие запросы ДО ядра.
    Использует модульные правила для проверки.

    Attributes:
        rate_limiter: Ограничитель частоты
        injection_detector: Детектор prompt injection
        ip_blocklist: Блок-лист IP
        anomaly_detector: Детектор аномалий
        enabled: Флаги включения правил из конфига (F54: `enabled: false` реально выключает)
    """

    def __init__(self, config: dict | None = None):
        """Инициализация файрвола.

        Args:
            config: Конфигурация из firewall.yaml (опционально)
        """
        self._assign(self._make_rules(config))

    @staticmethod
    def _num(config: dict, section: str, key: str, default: int) -> int:
        """F128: число из конфига обязано быть целым числом ЗДЕСЬ.

        Иначе сравнение падает уже на запросе: reload объявляет успех, а сервер отвечает
        отказом на всё подряд — прежние правила при этом снесены.
        """
        raw = config.get(section, {}).get(key, default)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != int(raw):
            raise ValueError(f"{section}.{key}: ожидалось целое число, получено {raw!r}")
        return int(raw)

    @staticmethod
    def _make_rules(config: dict | None):
        """Собрать набор правил из конфига (без сайд-эффектов на self).

        Вынесено из __init__, чтобы reload() собирал правила во ВРЕМЕННЫЕ объекты и подменял
        их атомарно только при успехе. Битый конфиг проявляется исключением отсюда — его ловит
        reload(); кривой regex в паттернах невозможен, они экранируются как литералы.
        """
        config = config or {}
        rate_limiter = RateLimiter(
            max_requests=Firewall._num(config, "rate_limit", "max_requests_per_minute", 60),
            ban_after=Firewall._num(config, "rate_limit", "ban_after_violations", 3)
        )
        # patterns=None → refined DEFAULT_PATTERNS; [] = выключить детекцию.
        injection_detector = InjectionDetector(
            patterns=config.get("injection_detection", {}).get("patterns", None)
        )
        ip_blocklist = IPBlocklist(
            auto_ban=config.get("ip_blocklist", {}).get("auto_ban", True),
            ban_duration_hours=Firewall._num(config, "ip_blocklist", "ban_duration_hours", 24)
        )
        # dangerous_tools из конфига (None → дефолт); детекция event-based.
        anomaly_detector = AnomalyDetector(
            dangerous_tools=config.get("anomaly_detection", {}).get("dangerous_tools", None)
        )
        # F54: `enabled` раньше объявлялся в firewall.yaml, но не читался — выключатель был мёртвым.
        # Отсутствие ключа = правило включено (обратная совместимость со старым конфигом).
        enabled = {
            section: bool(config.get(section, {}).get("enabled", True))
            for section in ("rate_limit", "ip_blocklist", "injection_detection", "anomaly_detection")
        }
        # Родня F54: `logging.log_suspicious` объявлялся в конфиге и не читался никем, поэтому
        # сигнал log-only оставался немым — счётчик в памяти видели только тесты.
        log_suspicious = bool(config.get("logging", {}).get("log_suspicious", True))
        return (rate_limiter, injection_detector, ip_blocklist, anomaly_detector,
                enabled, log_suspicious)

    def _assign(self, rules):
        """Присвоить собранный набор правил (атомарная точка подмены)."""
        (self.rate_limiter, self.injection_detector,
         self.ip_blocklist, self.anomaly_detector, self.enabled, self.log_suspicious) = rules

    def reload(self, config: dict | None) -> bool:
        """Горячая перезагрузка правил из нового config БЕЗ пересоздания файрвола.

        Fail-closed (D10): битый конфиг — не выключенный файрвол, а прежние рабочие правила
        и False на выходе. Объект Firewall тот же, поэтому все держатели (в т.ч. Transport)
        сразу видят новые правила; рантайм-счётчики rate-limit и баны IP при этом сбрасываются.
        """
        try:
            rules = self._make_rules(config)
        except Exception:
            return False
        self._assign(rules)
        return True

    def check(self, request: FirewallRequest) -> FirewallResult:
        """Проверка запроса через все правила: первое сработавшее и решает исход."""
        # 1. Проверка IP blocklist
        if self.enabled["ip_blocklist"] and self.ip_blocklist.is_blocked(request.ip):
            return FirewallResult(
                decision=FirewallDecision.BLOCK,
                reason=f"IP {request.ip} заблокирован",
            )

        # 2. Проверка rate limit
        rate_result = self.rate_limiter.check(request.ip, request.timestamp)
        if self.enabled["rate_limit"] and not rate_result.allowed:
            # D6: бан НЕ с первого превышения. Мягкий 429 до порога ban_after,
            # и только после N нарушений — фактический бан IP. За туннелем Claude
            # сидит за одним IP: один всплеск не должен блокировать его на 24ч.
            if self.rate_limiter.should_ban(request.ip):
                self.ip_blocklist.block(request.ip)
            return FirewallResult(
                decision=FirewallDecision.RATE_LIMIT,
                reason=f"Превышен лимит запросов для {request.ip}",
            )

        # 3. Детекция prompt injection
        # D7: блокируем ТОЛЬКО текущий запрос, без авто-бана IP. Эвристика на
        # подстроках даёт ложные срабатывания (легитимный "act as a narrator"),
        # а Claude — доверенная сторона; бан за это — операционная мина.
        if self.enabled["injection_detection"] and self.injection_detector.detect(request.params):
            return FirewallResult(
                decision=FirewallDecision.BLOCK,
                reason="Обнаружена prompt injection",
            )

        # 4. Детекция аномалий
        anomaly = self.anomaly_detector.check(request)
        if self.enabled["anomaly_detection"] and anomaly.detected:
            return FirewallResult(
                decision=FirewallDecision.BLOCK,
                reason=anomaly.reason,
            )

        # Пропускаем — но сигнал log-only обязан доехать наружу причиной: иначе деструктивный
        # вызов в проде не оставляет следа нигде, а `reason` детектора выбрасывался здесь.
        if self.log_suspicious and anomaly.reason:
            return FirewallResult(decision=FirewallDecision.ALLOW, reason=anomaly.reason)
        return FirewallResult(decision=FirewallDecision.ALLOW)

    def block_ip(self, ip: str, reason: str = ""):
        """Ручная блокировка IP.

        Args:
            ip: IP для блокировки
            reason: Причина блокировки
        """
        self.ip_blocklist.block(ip, reason)

    def unblock_ip(self, ip: str):
        """Разблокировка IP.

        Args:
            ip: IP для разблокировки
        """
        self.ip_blocklist.unblock(ip)

    def get_stats(self) -> dict:
        """Получение статистики файрвола.

        Returns:
            Словарь со статистикой
        """
        return {
            "blocked_ips": self.ip_blocklist.get_blocked_count(),
            "rate_limit_violations": self.rate_limiter.get_violations(),
            "injection_attempts": self.injection_detector.get_attempts(),
            "anomalies_detected": self.anomaly_detector.get_detected()
        }
