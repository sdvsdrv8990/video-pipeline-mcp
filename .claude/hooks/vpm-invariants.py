#!/usr/bin/env python3
"""Шим: межфайловые инварианты живут В РЕПОЗИТОРИИ (scripts/guards/invariants.py) — там же гейт CI.

Копия правила снаружи гнила бы молча. Нет репозитория (другой проект) — молчим, событие не наше.
Режим `--hook`: при чистом результате печатает НИЧЕГО, иначе перечисляет нарушения.
"""

import os
import subprocess
import sys
from pathlib import Path

# Корень — от места самого хука (`.claude/hooks/` в репозитории), а не зашит путём одной
# машины: зашитый путь делает хук неперемещаемым, и у всех, кроме автора, он молчит.
PROJ = Path(__file__).resolve().parents[2]
GUARD = str(PROJ / "scripts" / "guards" / "invariants.py")
VENV = str(PROJ / ".venv" / "bin" / "python")

if not os.path.exists(GUARD):
    sys.exit(0)
python = VENV if os.path.exists(VENV) else sys.executable
done = subprocess.run([python, GUARD, "--hook"], capture_output=True, text=True, timeout=60)
if done.stdout.strip():
    print("⚠️ Межфайловые инварианты нарушены:\n" + done.stdout.strip(), file=sys.stderr)
    sys.exit(2)     # видно модели: правка развела два файла, которые обязаны совпадать
sys.exit(0)
