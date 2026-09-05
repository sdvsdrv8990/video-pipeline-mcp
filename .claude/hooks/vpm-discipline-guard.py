#!/usr/bin/env python3
"""vpm-discipline-guard.py — сторож ДИСЦИПЛИНЫ: чтобы механизмы не пылились.

Судит не работу, а самих сторожей: объявленный и ни разу не сработавший неотличим от работающего —
тишина значит и «чисто», и «меня не позвали». Отличает их общий след (`_trace.py`), который каждый
хук ставит при входе. Судится СРОК, а не сессия: часть сторожей законно молчит целыми сессиями.

Выключатель: `VPM_DISCIPLINE_GUARD=off`.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

PROJ = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Столько суток молчания превращают сторожа в мебель: рабочая неделя проекта, за которую любой из
# объявленных хуков обязан был встретить своё событие хотя бы раз.
ПЫЛЬ_ДНЕЙ = 7.0
HOOK = re.compile(r"\.claude/hooks/([\w.-]+)")

MSG = """Сторож дисциплины: объявленные сторожа пылятся.

{список}

Сторож, не сработавший ни разу, неотличим от работающего: его тишина читается как «чисто». Либо у
него нет события — тогда он мебель и его снимают, — либо событие есть, а он молчит, и это дефект,
прячущий все его настоящие проверки.

Посмотреть след:
    python3 .claude/hooks/_trace.py

Выключить: VPM_DISCIPLINE_GUARD=off."""


def объявленные(root: Path = PROJ) -> list[str]:
    """Имена хуков из `settings.json`. Не разбирается — пусто: чужая поломка не наша находка."""
    try:
        текст = (root / ".claude" / "settings.json").read_text(encoding="utf-8")
        json.loads(текст)
    except (OSError, json.JSONDecodeError):
        return []
    # Помощники (`_trace.py`) событием не зовутся и в объявлении не значатся — их и не судим.
    return sorted({n for n in HOOK.findall(текст) if not n.startswith("_")})


def пылящиеся(root: Path = PROJ, дней: float = ПЫЛЬ_ДНЕЙ, след: Path | None = None) -> list[str]:
    """Строки о сторожах, молчащих дольше срока. Следа нет вовсе — пусто, а не обвинение."""
    try:
        import _trace
    except ImportError:
        return []
    путь = след or _trace.СЛЕД
    if not путь.is_dir():
        return []
    return [f"  {имя} — срабатывал {'ни разу' if math.isinf(прошло) else f'{прошло:.0f} дней назад'}"
            for имя, прошло in _trace.dusty(объявленные(root), дней, путь)]


def main() -> None:
    if os.environ.get("VPM_DISCIPLINE_GUARD", "").lower() in ("off", "0", "false"):
        sys.exit(0)
    try:
        import _trace
        _trace.mark("vpm-discipline-guard.py")
    except Exception:                          # noqa: BLE001 — след не важнее самой проверки
        pass
    data = json.loads(sys.stdin.read() or "{}")
    if data.get("stop_hook_active"):
        sys.exit(0)
    if (строки := пылящиеся()):
        print(json.dumps({"decision": "block", "reason": MSG.format(список="\n".join(строки))},
                         ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:                          # noqa: BLE001 — сторож не вправе ронять сессию
        sys.exit(0)
