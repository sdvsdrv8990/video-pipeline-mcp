"""Приспособление «проверка файрвола падает»: `Firewall.check` бросает на вызове инструмента, как при сбое правила, —
сервер обязан отказать (fail-closed, FIREWALL_UNAVAILABLE), а не пропустить вызов. Опись (`tools/list`) проверяется как
обычно: по ней обвязка узнаёт, что сервер поднят (`live_server._await_ready`). Порождений и сети нет: только подмена."""
from core.firewall.firewall import Firewall

_check = Firewall.check


def _broken(self, request):
    if request.method == "tools/list":
        return _check(self, request)
    raise RuntimeError("проверка файрвола сломана приспособлением firewall_broken")


Firewall.check = _broken  # type: ignore[method-assign]
