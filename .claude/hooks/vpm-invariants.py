#!/usr/bin/env python3
"""Шим межфайловых инвариантов + форма того, что правка ОСТАВИЛА в дереве.

Правило межфайловых инвариантов живёт В РЕПОЗИТОРИИ (scripts/guards/invariants.py) — там же гейт CI.
Копия правила снаружи гнила бы молча. Нет репозитория (другой проект) — молчим, событие не наше.

Вторая обязанность — не второе правило, а ЗАПУСК линтера проекта его же настройкой: между правкой и
следующим полным прогоном форму не смотрел никто, и незакрытый импорт или имя без импорта
доживали до чужой команды. Файлы берутся из ДЕРЕВА, а не из события: правка скриптом идёт
мимо `Edit`, а ломает так же.
"""

import json
import os
import shutil
import subprocess
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
GUARD = str(PROJ / "scripts" / "guards" / "invariants.py")
VENV = str(PROJ / ".venv" / "bin" / "python")


def linter(root: Path = PROJ) -> str | None:
    """Линтер ПРОЕКТА: настройку берёт из `pyproject.toml`, лежать может не только в `.venv`.

    Одного пути мало: в worktree цикла и на машине CI окружения проекта нет, и сторож отвечал бы
    «не судил» именно там, где судить обязан.
    """
    for candidate in (root / ".venv" / "bin" / "ruff", Path(sys.executable).with_name("ruff")):
        if candidate.exists():
            return str(candidate)
    return shutil.which("ruff")


def dirty_python(root: Path = PROJ) -> list[Path]:
    """`.py`, оставленные незакоммиченными: правка через оболочку файла в событии не называет."""
    names: set[str] = set()
    for argv in (["git", "diff", "--name-only", "-z", "HEAD"],
                 ["git", "ls-files", "--others", "--exclude-standard", "-z"]):
        try:
            done = subprocess.run(argv, cwd=str(root), capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return []
        names.update(item for item in done.stdout.split("\0") if item.endswith(".py"))
    return sorted({root / name for name in names if (root / name).is_file()})


def event_python(data: dict, root: Path = PROJ) -> list[Path]:
    """Файл, названный событием: правку, вернувшую файл к состоянию `HEAD`, дерево не покажет."""
    named = (data.get("tool_input") or {}).get("file_path")
    if not named:
        return []
    path = Path(named)
    return [path] if path.suffix == ".py" and path.is_file() and str(path).startswith(str(root)) else []


def form_complaints(paths: list[Path], root: Path = PROJ, ruff: str | None = None) -> list[str]:
    """Жалобы на форму: файл обязан компилироваться и быть чист по линтеру проекта.

    Компиляция — своя улика, она есть всегда; линтера может не быть, и тогда сторож говорит об этом
    вслух: молчание о непроверенном неотличимо от чистоты — ровно тот немой промах, против которого
    ось и заведена.
    """
    if not paths:
        return []
    notes, sane = [], []
    for path in paths:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            notes.append(f"{_rel(path, root)}:{exc.lineno} — не компилируется: {exc.msg}")
            continue
        except OSError as exc:
            notes.append(f"{_rel(path, root)} — не читается: {exc}")
            continue
        sane.append(str(path))
    if not sane:
        return notes
    if ruff is None:
        return notes + [f"линтер не найден ({root}/.venv/bin/ruff): {len(sane)} файлов не судились "
                        f"им вовсе — проверена только компиляция"]
    try:
        done = subprocess.run([ruff, "check", "--force-exclude", "--no-fix", "--quiet",
                               "--output-format", "concise", *sane],
                              cwd=str(root), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return notes + [f"линтер не запустился ({exc}) — проверена только компиляция"]
    notes += [line.strip() for line in done.stdout.splitlines() if line.strip()]
    return notes


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def main() -> int:
    if not os.path.exists(GUARD):
        return 0                    # не наш репозиторий — событие чужое
    data = {}
    if not sys.stdin.isatty():
        try:
            data = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            data = {}
    reports = []
    broken = form_complaints(sorted(set(dirty_python()) | set(event_python(data))), ruff=linter())
    if broken:
        reports.append("⚠️ Правка оставила в дереве неисправный файл:\n"
                       + "\n".join(f"  {note}" for note in broken))
    done = subprocess.run([VENV if os.path.exists(VENV) else sys.executable, GUARD, "--hook"],
                          capture_output=True, text=True, timeout=60)
    if done.stdout.strip():
        reports.append("⚠️ Межфайловые инварианты нарушены:\n" + done.stdout.strip())
    if reports:
        print("\n\n".join(reports), file=sys.stderr)
        return 2                    # видно модели: правка развела файлы либо сломала форму
    return 0


if __name__ == "__main__":
    sys.exit(main())
