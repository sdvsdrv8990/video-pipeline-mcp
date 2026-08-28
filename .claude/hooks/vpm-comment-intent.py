#!/usr/bin/env python3
"""vpm-comment-intent.py — текст, попадающий в код, обосновывается ДО записи.

Ценность комментария машина судить не умеет: регекс меряет длину, а нужность — утверждение о
читателе. Поэтому хук не судит, а ТРЕБУЕТ обоснование: правка отклоняется один раз с тремя
вопросами, повтор проходит. То, что нельзя обосновать одной строкой, обычно и не нужно.

Ловит добавление комментариев в код (`.py`, `.yaml`), не в документы: там проза и есть содержание.
Правка скриптом идёт мимо `Edit`, поэтому после Bash смотрится дифф — запись уже произошла, но
вопрос задаётся в том же ходу. Один отказ на файл за сессию: спрашивающий на каждой правке
превращается в шум, и его выключают. По существу не проверяет ничего — это speed bump, не проверка.
Выключить: VPM_COMMENT_INTENT=off.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

# Корень — от места самого хука (`.claude/hooks/` в репозитории), а не зашит путём одной
# машины: зашитый путь делает хук неперемещаемым, и у всех, кроме автора, он молчит.
PROJ = Path(__file__).resolve().parents[2]
STATE_DIR = Path.home() / ".claude" / "state" / "vpm-comment-intent"
STATE_TTL = 12 * 3600
SESSION_CAP = 6                       # больше отказов за сессию — уже помеха, а не напоминание

CODE_SUFFIX = (".py", ".yaml", ".yml")
COMMENT_LINE = re.compile(r'^\s*#\s*\S|^\s*(?:"""|\'\'\')')

MSG = """Гейт текста: правка добавляет комментарии/докстринг в {path}.

Обоснуй КАЖДЫЙ добавленный комментарий — коротко, до повтора правки:
1. Что НЕОЧЕВИДНОГО в поведении он объясняет (не пересказ имён: почему так, а не иначе).
2. Цитата — строка кода, к которой он относится.
3. Чем ошибётся читатель, если комментария не будет.

Нет ответа на все три — комментарий не нужен, убери его. Судить ценность машина не умеет, поэтому
обоснование даёшь ты; проверка не повторится по этому файлу в этой сессии.
Рацио задачи (`D#`/`F#`, история, планы) идёт в commit и журнал, в код — только неочевидное поведение.

Выключить: VPM_COMMENT_INTENT=off."""


def state_path(sid: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in STATE_DIR.glob("*.json"):
        try:
            if now - old.stat().st_mtime > STATE_TTL:
                old.unlink()
        except OSError:
            pass
    return STATE_DIR / f"{sid}.json"


def added_text(tool: str, inp: dict) -> str:
    """Текст, который правка ДОБАВЛЯЕТ. Для Edit — только новая сторона, иначе ловили бы чужое."""
    if tool == "Write":
        return str(inp.get("content") or "")
    if tool == "Edit":
        return str(inp.get("new_string") or "")
    if tool == "MultiEdit":
        return "\n".join(str(e.get("new_string") or "") for e in (inp.get("edits") or []))
    return ""


def added_by_command() -> list[str]:
    """Файлы кода, в которые НЕЗАКОММИЧЕННАЯ правка добавила строки текста."""
    import subprocess
    try:
        diff = subprocess.run(["git", "-C", str(PROJ), "diff", "-U0", "HEAD"],
                              capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found, current = [], ""
    for row in diff.splitlines():
        if row.startswith("+++ b/"):
            current = row[len("+++ b/"):]
        elif row.startswith("+") and not row.startswith("+++") and current.endswith(CODE_SUFFIX):
            if COMMENT_LINE.match(row[1:]) and current not in found:
                found.append(current)
    return found


def ask(rel: str, state: dict, path: Path, post: bool) -> None:
    state["asked"].append(rel)
    state["count"] = state.get("count", 0) + 1
    try:
        path.write_text(json.dumps(state))
    except OSError:
        pass
    if post:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": MSG.format(path=rel)}}))
    else:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": MSG.format(path=rel)}}))
    sys.exit(0)


def main() -> None:
    if os.environ.get("VPM_COMMENT_INTENT", "").lower() in ("off", "0", "false"):
        sys.exit(0)
    data = json.loads(sys.stdin.read())
    tool, inp = data.get("tool_name", ""), (data.get("tool_input") or {})
    path = state_path(data.get("session_id") or "нет-сессии")
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {"asked": [], "count": 0}

    if tool == "Bash":
        for rel in added_by_command():
            if rel not in state["asked"] and state.get("count", 0) < SESSION_CAP:
                ask(rel, state, path, post=True)
        sys.exit(0)
    if tool not in ("Edit", "Write", "MultiEdit"):
        sys.exit(0)

    target = Path(str(inp.get("file_path") or ""))
    try:
        rel = str(target.resolve().relative_to(PROJ))
    except ValueError:
        sys.exit(0)                                   # не наш проект
    if not rel.endswith(CODE_SUFFIX):
        sys.exit(0)

    body = added_text(tool, inp)
    if not any(COMMENT_LINE.match(line) for line in body.splitlines()):
        sys.exit(0)                                   # текста в код не добавляется

    if rel in state["asked"] or state.get("count", 0) >= SESSION_CAP:
        sys.exit(0)
    ask(rel, state, path, post=False)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
