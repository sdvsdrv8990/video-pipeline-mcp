#!/usr/bin/env python3
"""vpm-intent-guard.py — сторож НАМЕРЕНИЙ: остаток не должен пылиться.

`SessionStart` показывает незакрытые остатки: хвост, записанный вчера, сам о себе не напомнит, и
без показа его пришлось бы переносить в новую сессию руками. `Stop` не даёт кончить сессию
обещанием: «вернёмся», «остаётся», «завтра» в последних словах при нуле подписей `ОСТАТОК` значит,
что работа объявлена незакрытой ПРОЗОЙ — и завтра о ней не узнает никто.

Остаток записывается подписью, а не статусом: статус стареет молча, подпись несёт намерение, чего
ждали, чем продолжить и ключ, по которому её поднимут (правило владельца 2026-09-02).

Выключатель: `VPM_INTENT_GUARD=off`.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

PROJ = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
# Два каталога, а не один: `_trace` лежит рядом с хуками, а `_stamp` — у сторожей, и словарь
# подписи живёт там же, где сторожа, которые его читают.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(PROJ / "scripts" / "guards"))

# Ищутся в ПОСЛЕДНИХ словах сессии: обещание, данное в середине и потом закрытое, обещанием уже
# не является, и обвинять за него значило бы наказывать за ход работы.
PROMISE = re.compile(
    r"\b(верн[уё]мся|вернуться|продолж|остаётся|остается|осталось|остаток|доделаю|доделать"
    r"|завтра|следующ(ей|ая) сесси|потом сдела|отложил|не успел)", re.I)
# Слово о решении владельца — отдельным рядом: его нельзя утопить в «сделано», даже если код зелёный.
DECISION = re.compile(r"\b(решени[ея] владельца|ждёт владельца|ждет владельца|вопрос владельцу)", re.I)
# Инструменты, которыми правка ЛОЖИТСЯ НА ДИСК. Чтение не блокируется: чтобы закрыть или отложить
# хвост, на него надо посмотреть, и запрет чтения дал бы тупик на первом же требовании сторожа.
ПИШУЩИЕ = ("Edit", "Write", "MultiEdit", "NotebookEdit")
# `2>&1` и `>/dev/null` — перенаправление ПОТОКА, а не запись в дерево. Без этого различия сторож
# блокирует собственную диагностическую команду: поймано на себе же при первом прогоне.
ПИШЕТ_КОМАНДА = re.compile(
    r">>?\s*(?!&\d|/dev/)[\w./-]+|\btee\b|\bsed\s+-i\b|write_text"
    r"|\bmv\b|\bcp\b|\brm\b|\bgit\s+(?:commit|add|apply)\b")

START = """## Незакрытые остатки — {n} шт.

Их не помнит никто, кроме подписи: они записаны уликой, а не статусом, поэтому пережили сессию.

{tails}
Закрыть остаток — подписью, называющей его ключ:

    .venv/bin/python scripts/guards/_stamp.py --kind проба --role ВЕРДИКТ \\
        --what "чем закрыт" --closes <ключ>

Спросить владельца ПРЕЖДЕ, чем закрывать своим решением: остаток заведён как ЕГО развилка."""

STOP = """Сторож намерений: сессия кончается обещанием, а остаток не записан.

В последних словах: «{цитата}»
Подписей `ОСТАТОК` за сутки: 0.

Обещание в ленте живёт до конца сессии и исчезает вместе с ней — завтра о нём не узнает никто, и
владельцу придётся помнить его самому. Запиши остаток подписью (правило 2026-09-02: статусом
остаток записывать нельзя, только уликой):

    .venv/bin/python scripts/guards/_stamp.py --kind проба --role ОСТАТОК \\
        --what "что осталось" --expected "чем это закроется" \\
        --cmd "<команда, которой продолжить>"

Выключить: VPM_INTENT_GUARD=off."""


ЗАПРЕТ = """Сторож намерений: незакрытый остаток — {n} шт. Работа не начинается, пока он не признан.

{tails}
Признать — одно из двух, и оба остаются на диске:

  закрыть:   .venv/bin/python scripts/guards/_stamp.py --kind проба --role ВЕРДИКТ \\
                 --what "чем закрыт" --closes <ключ>
  отложить:  .venv/bin/python scripts/guards/_stamp.py --kind проба --role ОТЛОЖЕН \\
                 --what "почему сейчас не он" --closes <ключ> --cmd "<чем вернуться>"

Отложенный из показа не исчезает — он не закрыт; исчезает только запрет. Чужую развилку
(«решение владельца», «подтвердить») своим решением не закрывают — спрашивают.

Читать и смотреть можно: запрет стоит только на записи. Выключить: VPM_INTENT_GUARD=off."""


def тексты(data: dict) -> str:
    """Последние слова сессии: сообщение целиком, иначе хвост стенограммы."""
    last = data.get("last_assistant_message") or ""
    if last:
        return last
    tp = data.get("transcript_path")
    if not tp:
        return ""
    try:
        return Path(os.path.expanduser(tp)).read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        return ""


def подписей(role: str, начало: float, root: Path = PROJ) -> int:
    """Сколько подписей роли поставлено с начала суток. Журнала нет — ноль, а не отказ."""
    day = time.strftime("%Y%m%d", time.localtime(начало))
    path = root / "tests" / ".journal" / f"stamps-{day}.jsonl"
    if not path.exists():
        return 0
    счёт = 0
    for row in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            запись = json.loads(row)
        except json.JSONDecodeError:
            continue
        счёт += запись.get("role") == role and float(запись.get("ts") or 0) >= начало
    return счёт


def сутки() -> float:
    """Начало текущих суток: журнал подписей ведётся по дням, другой границы у нас нет."""
    return time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))


def показать_остатки(root: Path = PROJ) -> str:
    """Текст для начала сессии. Пусто — показывать нечего."""
    import _stamp
    хвосты = _stamp.tails(root)
    if not хвосты:
        return ""
    строки = []
    for x in хвосты:
        дней = (time.time() - float(x.get("ts") or 0)) / 86400
        строки.append(f"- `{x['key']}` · {x['what']}\n"
                      f"  ждали: {x.get('expected') or '—'} · дней: {дней:.0f}\n"
                      f"  продолжить: `{x.get('cmd') or '—'}`\n")
    return START.format(n=len(хвосты), tails="\n".join(строки))


def судить_конец(data: dict, root: Path = PROJ) -> str:
    """Причина отказа на `Stop` — либо пустая строка."""
    if data.get("stop_hook_active"):
        return ""
    слова = тексты(data)
    найдено = PROMISE.search(слова) or DECISION.search(слова)
    if not найдено or подписей("ОСТАТОК", сутки(), root):
        return ""
    кусок = слова[max(0, найдено.start() - 60):найдено.end() + 60].replace("\n", " ")
    return STOP.format(цитата=кусок.strip())


def судить_правку(data: dict, root: Path = PROJ) -> str:
    """Причина запрета на запись — либо пустая строка, если непризнанных хвостов нет."""
    import _stamp
    инструмент = data.get("tool_name", "")
    команда = (data.get("tool_input") or {}).get("command", "")
    пишет = инструмент in ПИШУЩИЕ or (инструмент == "Bash" and ПИШЕТ_КОМАНДА.search(команда))
    if not пишет:
        return ""
    непризнанные = [x for x in _stamp.tails(root) if not x.get("отложен")]
    if not непризнанные:
        return ""
    строки = [f"- `{x['key']}` · {x['what']}\n  ждали: {x.get('expected') or '—'}\n"
              f"  продолжить: `{x.get('cmd') or '—'}`\n" for x in непризнанные]
    return ЗАПРЕТ.format(n=len(непризнанные), tails="\n".join(строки))


def main() -> None:
    if os.environ.get("VPM_INTENT_GUARD", "").lower() in ("off", "0", "false"):
        sys.exit(0)
    try:
        import _trace
        _trace.mark("vpm-intent-guard.py")
    except Exception:                          # noqa: BLE001 — след не важнее самой проверки
        pass
    data = json.loads(sys.stdin.read() or "{}")
    if data.get("hook_event_name") == "PreToolUse":
        if (запрет := судить_правку(data)):
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": запрет}}, ensure_ascii=False))
        sys.exit(0)
    if data.get("hook_event_name") == "SessionStart":
        if (текст := показать_остатки()):
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart", "additionalContext": текст}}, ensure_ascii=False))
        sys.exit(0)
    if (причина := судить_конец(data)):
        print(json.dumps({"decision": "block", "reason": причина}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:                          # noqa: BLE001 — сторож не вправе ронять сессию
        sys.exit(0)
