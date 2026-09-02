"""_trace.py — общий след сторожей: кто из них СРАБАТЫВАЛ и когда.

Механизм, объявленный и не сработавший ни разу, неотличим от работающего: тишина у сторожа значит
и «чисто», и «меня не позвали». Отличить их можно только следом, поэтому каждый хук отмечается здесь
при входе, а судит след `vpm-discipline-guard.py`.

След лежит ВНЕ репозитория (`~/.claude/state/`): он про работу конкретной машины, а не про продукт.
Любая ошибка записи проглатывается молча — сторож не вправе падать из-за собственного журнала.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

TRACE = Path.home() / ".claude" / "state" / "vpm-trace.json"


def _read(path: Path = TRACE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def mark(name: str, path: Path = TRACE) -> None:
    """Отметить срабатывание. Имя — файла хука, чтобы сходилось с объявлением в settings.json."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        след = _read(path)
        # Рождение самого следа: без него в первый день ВСЕ сторожа выглядят пылящимися — они и не
        # могли отметиться, механизма ещё не было. Обвинять за это значит краснеть на пустом месте.
        след.setdefault("_рождение", time.time())
        строка = след.setdefault(name, {})
        строка["last"] = time.time()
        строка["count"] = int(строка.get("count") or 0) + 1
        path.write_text(json.dumps(след, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def seen(name: str, path: Path = TRACE) -> float:
    """Когда сторож срабатывал в последний раз. Ноль — не срабатывал ни разу."""
    return float((_read(path).get(name) or {}).get("last") or 0)


def dusty(names: list[str], days: float, path: Path = TRACE) -> list[tuple[str, float]]:
    """(имя, дней назад) для тех, кто не срабатывал дольше срока. Ни разу — бесконечность.

    Судится СРОК, а не «не сработал в этой сессии»: часть сторожей законно молчит целыми сессиями
    (правишь только доки — гейту фактов нечего ловить), и обвинять их за это значило бы приучить
    выключать сторожа, чтобы не мешал.
    """
    сейчас = time.time()
    рождение = float(_read(path).get("_рождение") or 0)
    if not рождение or (сейчас - рождение) / 86400 < days:
        return []                              # след моложе срока — улики нет, а не обвинение
    out = []
    for name in names:
        когда = seen(name, path)
        прошло = float("inf") if not когда else (сейчас - когда) / 86400
        if прошло > days:
            out.append((name, прошло))
    return out
