#!/usr/bin/env python3
"""Шим: правило текста в коде живёт В РЕПОЗИТОРИИ (scripts/guards/comment_guard.py) — там же гейт CI.

Копия правила снаружи гнила бы молча: две половины одной правды работают наполовину.
Нет репозитория (другой проект / чужая машина) — молчим, событие не наше.
"""

import os
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _trace
    _trace.mark(Path(__file__).name)
except Exception:                              # noqa: BLE001 — след не важнее самой проверки
    pass

# Корень — от места самого хука (`.claude/hooks/` в репозитории), а не зашит путём одной
# машины: зашитый путь делает хук неперемещаемым, и у всех, кроме автора, он молчит.
PROJ = Path(__file__).resolve().parents[2]
GUARD = str(PROJ / "scripts" / "guards" / "comment_guard.py")

if not os.path.exists(GUARD):
    sys.exit(0)
os.execv(sys.executable, [sys.executable, GUARD, *(sys.argv[1:] or ["--hook"])])
