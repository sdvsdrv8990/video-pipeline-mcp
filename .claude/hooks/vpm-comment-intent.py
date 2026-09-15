#!/usr/bin/env python3
""".claude/hooks/vpm-comment-intent.py — текст, попадающий в код, обосновывается ДО записи.

Паспорт: будит редактирование, создание, событие PreToolUse, событие PostToolUse · читает stdin,
  scripts/guards/comment_guard.py · шагов не расписывает · не подписывает.
Зовёт: _comments, comment_guard.
Зовут: событие PreToolUse, событие PostToolUse и test_hooks.

Ценность комментария машина судить не умеет: регекс меряет длину, а нужность — утверждение о
читателе. Хук не судит, а ТРЕБУЕТ обоснование: правка отклоняется один раз с вопросами, повтор
проходит. Правка скриптом идёт мимо `Edit`, поэтому после Bash смотрится дифф. Один отказ на файл
за сессию: спрашивающий на каждой правке превращается в шум.

Содержимое здесь не судится: правило живёт в `comment_guard`, и хук ведёт ТУДА. После записи
управление уходит судье ДО рубильника: `VPM_COMMENT_INTENT=off` снимает вопрос о цене, но не
храповик — иначе один рубильник гасил бы двух судей.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _trace
    _trace.mark(Path(__file__).name)
except Exception:                              # noqa: BLE001 — след не важнее самой проверки
    pass

# Корень — от места самого хука (`.claude/hooks/` в репозитории), а не зашит путём одной
# машины: зашитый путь делает хук неперемещаемым, и у всех, кроме автора, он молчит.
# Дерево берётся из объявления среды, как у соседних хуков: иначе хук умеет судить только
# СВОЁ дерево, и обе его стороны недостижимы набором.
PROJ = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
СУДЬЯ = PROJ / "scripts" / "guards" / "comment_guard.py"
STATE_DIR = Path.home() / ".claude" / "state" / "vpm-comment-intent"
STATE_TTL = 12 * 3600
SESSION_CAP = 6                       # больше отказов за сессию — уже помеха, а не напоминание

CODE_SUFFIX = (".py", ".yaml", ".yml")
COMMENT_LINE = re.compile(r'^\s*#\s*\S|^\s*(?:"""|\'\'\')')

MSG = """Гейт текста: правка добавляет комментарии/докстринг в {path}.

У ЗАКОННОГО комментария есть КЛАСС — четыре условия разом, ноль противоречий (владелец
2026-09-11). Назови их для КАЖДОГО добавленного, коротко, до повтора правки:
1. НЕОЧЕВИДНО — что в ПОВЕДЕНИИ он объясняет: почему так, а не иначе. Пересказ имён не считается.
2. УЛИКА — что здесь можно проверить и посчитать: условие, следствие, ключ прогона. Плюс цитата
   строки, к которой комментарий относится.
3. НЕТ ТАВТОЛОГИИ — его слова не стоят уже в соседнем коде. Смешанный комментарий режут: улику
   оставляют, тавтологию сносят.
4. НЕ ВЫВОДИТСЯ ИЗ ФАЙЛА — этого нет ни в коде рядом, ни в объявлении, ни в графе вызовов.
   И ЦЕНА: не получив его, человек или ИИ что сломает и ГДЕ, либо сколько времени потеряет.
   Цена нулевая («просто понятнее») — комментарий не нужен: понятность делается именем и структурой.

Провалено любое из четырёх — комментарий не нужен, убери его. Цифра в комментарии почти всегда
провал условия 4: число живёт в объявлении или измеряется прогоном, а в прозе оно лжёт молча.

Исключения два — сложный код с плавающим дефектом и зона под передачу разработчику. Объявляет их
ВЛАДЕЛЕЦ подписью с обоснованием, не ИИ: иначе каждый комментарий станет «зоной для передачи».

Судить ценность машина не умеет, поэтому обоснование даёшь ты; проверка не повторится по этому
файлу в этой сессии. Рацио задачи (история, планы) идёт в commit и журнал, в код — только
неочевидное поведение. Классы мусора и форма шапки — скил `project-conventions`.

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


def зона_владельца(rel: str):
    """Действующая зона исключения для этого пути, либо `None`. Разбор один — источник улики.

    Хук молчать в зоне ОБЯЗАН: требовать обоснование там, где владелец уже объяснил и подписал,
    значит спорить с его решением на каждой правке.
    """
    try:
        # КОД источника берём из репозитория, где живёт хук; ДАННЫЕ (дом объявлений) — из
        # судимого дерева. Смешать их значит потерять способность судить чужое дерево набором.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "guards"))
        import _comments
        return _comments.покрыт(rel, root=PROJ)
    except Exception:  # noqa: BLE001 — дома может не быть (клон без сторожей): тогда зон нет
        return None


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


def судье(argv: list[str]) -> None:
    """Передать управление судье содержимого. Нет репозитория — событие не наше, молчим."""
    if not СУДЬЯ.exists():
        sys.exit(0)
    os.execv(sys.executable, [sys.executable, str(СУДЬЯ), *(argv or ["--hook"])])


def main() -> None:
    if sys.argv[1:]:
        судье(sys.argv[1:])
    data = json.loads(sys.stdin.read())
    tool, inp = data.get("tool_name", ""), (data.get("tool_input") or {})
    if data.get("hook_event_name") == "PostToolUse" and tool in ("Edit", "Write", "MultiEdit"):
        судье([])
    if os.environ.get("VPM_COMMENT_INTENT", "").lower() in ("off", "0", "false"):
        sys.exit(0)
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
    зона = зона_владельца(rel)
    if зона:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
              f"Гейт текста: {rel} — зона исключения объявлена владельцем до {зона['до']} "
              f"({зона['класс']}), подпись {зона['подпись']}. Обоснование комментария здесь не "
              f"требуется; истечёт срок — вернётся обычный суд."}}))
        sys.exit(0)
    ask(rel, state, path, post=False)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
