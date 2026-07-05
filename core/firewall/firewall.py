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
    """

    def __init__(self, config: dict | None = None):
        """Инициализация файрвола.

        Args:
            config: Конфигурация из firewall.yaml (опционально)
        """
        self._assign(self._make_rules(config))

    @staticmethod
    def _make_rules(config: dict | None):
        """Собрать набор правил из конфига (без сайд-эффектов на self).

        Вынесено из __init__, чтобы reload() мог собрать правила во ВРЕМЕННЫЕ
        объекты и подменить их атомарно только при успехе (fail-closed).
        Может бросить исключение (напр. top-level YAML не словарь → .get() падает,
        либо несовместимый тип значения в конструкторе правила) — это сигнал
        «конфиг битый», который ловит reload(). Injection-паттерны трактуются как
        литеральные подстроки (re.escape), кривой regex тут невозможен.
        """
        config = config or {}
        rate_limiter = RateLimiter(
            max_requests=config.get("rate_limit", {}).get("max_requests_per_minute", 60),
            ban_after=config.get("rate_limit", {}).get("ban_after_violations", 3)
        )
        # patterns=None → refined DEFAULT_PATTERNS; [] = выключить детекцию.
        injection_detector = InjectionDetector(
            patterns=config.get("injection_detection", {}).get("patterns", None)
        )
        ip_blocklist = IPBlocklist(
            auto_ban=config.get("ip_blocklist", {}).get("auto_ban", True),
            ban_duration_hours=config.get("ip_blocklist", {}).get("ban_duration_hours", 24)
        )
        # dangerous_tools из конфига (None → дефолт); детекция event-based.
        anomaly_detector = AnomalyDetector(
            dangerous_tools=config.get("anomaly_detection", {}).get("dangerous_tools", None)
        )
        return rate_limiter, injection_detector, ip_blocklist, anomaly_detector

    def _assign(self, rules):
        """Присвоить собранный набор правил (атомарная точка подмены)."""
        (self.rate_limiter, self.injection_detector,
         self.ip_blocklist, self.anomaly_detector) = rules

    def reload(self, config: dict | None) -> bool:
        """Горячая перезагрузка правил из нового config БЕЗ пересоздания файрвола.

        Fail-closed (D10): правила собираются во временные объекты; если конфиг
        битый (исключение при сборке) — self НЕ трогаем, остаются ПРЕЖНИЕ рабочие
        правила, файрвол не выключается. Ссылка на объект Firewall сохраняется,
        поэтому все держатели (в т.ч. Transport) сразу видят новые правила.

        ПРИМЕЧАНИЕ: рантайм-счётчики (rate-limit) и баны IP сбрасываются — reload
        это редкое админ-действие, а за туннелем один клиентский IP (G18), потеря
        банов пренебрежима. Смысл именно в подхвате новых порогов/паттернов на лету.

        Returns:
            True — новый конфиг применён; False — битый, оставлены прежние правила.
        """
        try:
            rules = self._make_rules(config)
        except Exception:
            return False
        self._assign(rules)
        return True

    def check(self, request: FirewallRequest) -> FirewallResult:
        """Проверка запроса через все правила.

        Порядок проверок:
        1. IP blocklist (быстро)
        2. Rate limit
        3. Injection detection
        4. Anomaly detection

        Args:
            request: Запрос для проверки

        Returns:
            FirewallResult с решением
        """
        # 1. Проверка IP blocklist
        if self.ip_blocklist.is_blocked(request.ip):
            return FirewallResult(
                decision=FirewallDecision.BLOCK,
                reason=f"IP {request.ip} заблокирован",
            )

        # 2. Проверка rate limit
        rate_result = self.rate_limiter.check(request.ip, request.timestamp)
        if not rate_result.allowed:
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
        if self.injection_detector.detect(request.params):
            return FirewallResult(
                decision=FirewallDecision.BLOCK,
                reason="Обнаружена prompt injection",
            )

        # 4. Детекция аномалий
        anomaly = self.anomaly_detector.check(request)
        if anomaly.detected:
            return FirewallResult(
                decision=FirewallDecision.BLOCK,
                reason=anomaly.reason,
            )

        # Всё ок
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
