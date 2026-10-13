"""core/firewall/firewall.py — фильтр входящего: все запросы судятся ДО ядра.

Первое сработавшее правило и решает исход — остальные не спрашиваются. Перезагрузка идёт без
пересоздания фильтра: набор собирается целиком в стороне и подменяется одной точкой, иначе
запрос успел бы увидеть половину старых правил и половину новых.
"""

import math
from pathlib import Path

from core.contracts import ContractError
from core.declaration import Declaration
from core.declaration_schemas import RATE_LIMIT, RateLimit

from .contracts import FirewallDecision, FirewallRequest, FirewallResult
from .rules.rate_limiter import RateLimiter
from .rules.injection_detector import InjectionDetector
from .rules.ip_blocklist import IPBlocklist
from .rules.anomaly_detector import AnomalyDetector


class Firewall:
    """Файрвол MCP-сервера: фильтрует ВСЕ входящие запросы ДО ядра модульными правилами.

    `enabled: false` в конфиге правило реально выключает, а не игнорируется.
    """

    def __init__(self, config: dict, rate: RateLimit):
        """Файрвол по разделам firewall.yaml (`config`) и разделу лимита (`rate`, схема `RateLimit`); сборка по
        декларации через дверь — `Firewall.declared`."""
        self._assign(self._make_rules(config, rate))

    @classmethod
    def declared(cls, path: Path) -> "Firewall":
        """Файрвол по декларации через дверь: битая — отказ двери при сборке, а не правила по умолчанию."""
        return cls(*_declaration(path))

    @staticmethod
    def _num(config: dict, section: str, key: str, default: int) -> int:
        """Число из конфига обязано быть целым числом ЗДЕСЬ.

        Иначе сравнение падает уже на запросе: reload объявляет успех, а сервер отвечает
        отказом на всё подряд — прежние правила при этом снесены.
        """
        raw = config.get(section, {}).get(key, default)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != int(raw):
            raise ValueError(f"{section}.{key}: ожидалось целое число, получено {raw!r}")
        return int(raw)

    @staticmethod
    def _make_rules(config: dict, rate: RateLimit):
        """Собрать набор правил из конфига (без сайд-эффектов на self).

        Вынесено из __init__, чтобы reload() собирал правила во ВРЕМЕННЫЕ объекты и подменял
        их атомарно только при успехе. Битый конфиг проявляется исключением отсюда — его ловит
        reload(); кривой regex в паттернах невозможен, они экранируются как литералы. Лимит частоты —
        проверенный схемой раздел (`RateLimit`): умолчаний здесь нет.
        """
        rate_limiter = RateLimiter(max_requests=rate.max_requests, ban_after=rate.ban_after_violations,
                                   window_sec=rate.window_sec)
        # patterns=None → refined DEFAULT_PATTERNS; [] = выключить детекцию.
        injection_detector = InjectionDetector(
            patterns=config.get("injection_detection", {}).get("patterns", None)
        )
        ip_blocklist = IPBlocklist(
            auto_ban=config.get("ip_blocklist", {}).get("auto_ban", True),
            ban_duration_hours=Firewall._num(config, "ip_blocklist", "ban_duration_hours", 24)
        )
        # Единственный источник списка — декларация: запасной копии в коде нет, в отличие от
        # соседних правил; ноль под наблюдением виден в get_stats, а не притворяется работой.
        anomaly_detector = AnomalyDetector(
            dangerous_tools=config.get("anomaly_detection", {}).get("dangerous_tools", None)
        )
        # Выключатель правила обязан ЧИТАТЬСЯ: объявленный, но никем не читаемый `enabled`
        # выглядит рабочим и молча ничего не выключает.
        # Отсутствие ключа = правило включено (обратная совместимость со старым конфигом); у лимита ключ обязателен схемой.
        enabled = {
            section: bool(config.get(section, {}).get("enabled", True))
            for section in ("ip_blocklist", "injection_detection", "anomaly_detection")
        } | {"rate_limit": rate.enabled}
        # Та же родня: без чтения `logging.log_suspicious` сигнал log-only остаётся немым —
        # счётчик в памяти видят только тесты.
        log_suspicious = bool(config.get("logging", {}).get("log_suspicious", True))
        return (rate_limiter, injection_detector, ip_blocklist, anomaly_detector,
                enabled, log_suspicious)

    def _assign(self, rules):
        """Присвоить собранный набор правил (атомарная точка подмены)."""
        (self.rate_limiter, self.injection_detector,
         self.ip_blocklist, self.anomaly_detector, self.enabled, self.log_suspicious) = rules

    def reload(self, config: dict, rate: RateLimit) -> None:
        """Горячая перезагрузка правил из нового config БЕЗ пересоздания файрвола.

        Fail-closed: битый конфиг — не выключенный файрвол, а прежние рабочие правила; причина уходит
        исключением сборки вызывающему (правила подменяются только после неё). Объект Firewall тот же,
        поэтому все держатели (в т.ч. Transport) сразу видят новые правила; рантайм-счётчики rate-limit
        и баны IP при этом сбрасываются.
        """
        self._assign(self._make_rules(config, rate))

    def reload_declared(self, path: Path) -> None:
        """Перезагрузка по декларации через дверь: битый раздел — отказ двери до подмены, прежние правила целы."""
        self.reload(*_declaration(path))

    def check(self, request: FirewallRequest) -> FirewallResult:
        """Проверка запроса через все правила: первое сработавшее и решает исход."""
        # 1. Проверка IP blocklist
        if self.enabled["ip_blocklist"] and self.ip_blocklist.is_blocked(request.ip):
            return FirewallResult(
                decision=FirewallDecision.BLOCK,
                reason=f"IP {request.ip} заблокирован",
                rule="ip_blocklist",
            )

        rate_result = self.rate_limiter.check(request.ip, request.timestamp)
        if self.enabled["rate_limit"] and not rate_result.allowed:
            # Бан НЕ с первого превышения. Мягкий 429 до порога ban_after,
            # и только после N нарушений — фактический бан IP. За туннелем Claude
            # сидит за одним IP: один всплеск не должен блокировать его на 24ч.
            if self.rate_limiter.should_ban(request.ip):
                self.ip_blocklist.block(request.ip)
            return FirewallResult(
                decision=FirewallDecision.RATE_LIMIT,
                reason=f"Превышен лимит запросов для {request.ip}",
                rule="rate_limit",
                retry_after_sec=math.ceil(rate_result.reset_in),
            )

        # 3. Детекция prompt injection
        # Блокируем ТОЛЬКО текущий запрос, без авто-бана IP. Эвристика на
        # подстроках даёт ложные срабатывания (легитимный "act as a narrator"),
        # а Claude — доверенная сторона; бан за это — операционная мина.
        if self.enabled["injection_detection"] and self.injection_detector.detect(request.params):
            return FirewallResult(
                decision=FirewallDecision.BLOCK,
                reason="Обнаружена prompt injection",
                rule="injection_detection",
            )

        # 4. Детекция аномалий
        anomaly = self.anomaly_detector.check(request)
        if self.enabled["anomaly_detection"] and anomaly.detected:
            return FirewallResult(
                decision=FirewallDecision.BLOCK,
                reason=anomaly.reason,
                rule="anomaly_detection",
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

    def get_stats(self) -> dict:
        """Получение статистики файрвола.

        Returns:
            Словарь со статистикой
        """
        return {
            "blocked_ips": self.ip_blocklist.get_blocked_count(),
            "rate_limit_violations": self.rate_limiter.get_violations(),
            "injection_attempts": self.injection_detector.get_attempts(),
            "anomalies_detected": self.anomaly_detector.get_detected(),
            "dangerous_tools_watched": len(self.anomaly_detector.dangerous_tools)
        }


def _declaration(path: Path) -> tuple[dict, RateLimit]:
    """Декларация файрвола через дверь: разделы правил — данными файла, раздел лимита — своей схемой (нет ключа, не тот
    тип — DECLARATION_INVALID с путём ключа, без значений)."""
    door = Declaration(path, ContractError, "«firewall»", f"Заведи config/{path.name} — по нему сервер судит входящие.")
    return door.data, door.section(*RATE_LIMIT.section, schema=RATE_LIMIT.schema)
