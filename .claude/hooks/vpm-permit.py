#!/usr/bin/env python3
"""vpm-permit.py — дверь человека: снятие запрета спрашивается, а не объявляется.

Зона одна и узкая: команда, которая ПРОСИТ снять запрет сторожа остатков, и команда, которая
пытается снять его своей рукой. Первую хук выносит человеку вопросом «да/нет»
(`permissionDecision: ask` — единственный способ показать вопрос в консоли ДО запуска), вторую
отклоняет и показывает первую. Больше он не смотрит ни на что.

Прежде выключатель был напечатан в тексте самого отказа, и свежая консоль под давлением шла
именно туда. Правило живёт в репозитории (`scripts/guards/_permit.py`), у хука только дорога.

Ошибка = пропуск: сломанная дверь не должна останавливать работу — запрет держит сам сторож.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(PROJ / "scripts" / "guards"))

try:
    import _trace
    _trace.mark(Path(__file__).name)
except Exception:                              # noqa: BLE001 — след не важнее самой двери
    pass

СПРОС = """Снять запрет сторожа остатков — решение человека, не ИИ.

Просит: {почему}
Снимается: {сторож} · на {срок} минут · разрешение уйдёт подписью в общий журнал

«Нет» — сторож остаётся на месте, и работа идёт признанием остатка (закрыть · отложить · взять).
«Да» — запрет снят на срок и вернётся сам; вернуть раньше: `--отозвать {переменная}`."""

НЕТ_ПРИЧИНЫ = "причина не названа — это само по себе повод сказать «нет»"


def main() -> None:
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return
    команда = str((data.get("tool_input") or {}).get("command") or "")

    import _permit
    if (переменная := _permit.запрос(команда)):
        хвост = команда.split("--почему", 1)[1].strip() if "--почему" in команда else ""
        решение, причина = "ask", СПРОС.format(
            почему=хвост.strip("\"'") or НЕТ_ПРИЧИНЫ,
            сторож=_permit.СТОРОЖА[переменная], срок=_permit.СРОК // 60, переменная=переменная)
    elif (переменная := _permit.самоволка(команда)):
        решение, причина = "deny", _permit.текст(переменная)
    else:
        return

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": решение,
        "permissionDecisionReason": причина}}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:                          # noqa: BLE001 — запрет держит сторож, а не эта дверь
        sys.exit(0)
