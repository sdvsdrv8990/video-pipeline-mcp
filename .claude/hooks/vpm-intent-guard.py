#!/usr/bin/env python3
"""vpm-intent-guard.py — сторож НАМЕРЕНИЙ: остаток не должен пылиться.

`SessionStart` показывает незакрытые остатки: хвост, записанный вчера, сам о себе не напомнит, и
без показа его пришлось бы переносить в новую сессию руками. `Stop` не даёт кончить сессию
обещанием: «вернёмся», «остаётся», «завтра» в последних словах при нуле подписей `ОСТАТОК` значит,
что работа объявлена незакрытой ПРОЗОЙ — и завтра о ней не узнает никто.

Остаток записывается подписью, а не статусом: статус стареет молча, подпись несёт намерение, чего
ждали, чем продолжить и ключ, по которому её поднимут (правило владельца 2026-09-02).

Запрет снимает ЧЕЛОВЕК, а не сторож и не ИИ: `scripts/guards/_permit.py`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
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
# Машинерия здесь наравне с сервером: правка хука ни одним сценарием не судится, и зона,
# сужённая до core/, молчала бы ровно там, где правило и нарушают.
ЗОНА_ЦИКЛА = re.compile(r"^(core/|tools/|server\.py|scripts/guards/[\w.]+\.py|\.claude/hooks/)")
# Судится ПОЗИЦИЯ, а не вхождение: те же слова внутри тела heredoc — данные, а не запуск, и
# запрет по ним блокирует даже подпись, описывающую эту самую ошибку (поймано на себе).
ТЕЛО_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?.*?^\1$", re.S | re.M)
КОММИТ = re.compile(r"(?:\A|[\n;&|(])\s*(?:\w+=\S+\s+)*git\s+(?:-\S+\s+)*commit\b")

START = """## Незакрытые остатки — {n} шт.

Их не помнит никто, кроме подписи: они записаны уликой, а не статусом, поэтому пережили сессию.

{tails}
Закрыть остаток — подписью, называющей его ключ:

    .venv/bin/python scripts/guards/_stamp.py --kind проба --role ВЕРДИКТ \\
        --what "чем закрыт" --closes {ключ}     ← ключ старшего; для другого возьми из списка

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

{дверь}"""


ЗАПРЕТ = """Сторож намерений: незакрытый остаток — {n} шт. Работа не начинается, пока он не признан.

{tails}
Признать — одно из двух, и оба остаются на диске:

  закрыть:   .venv/bin/python scripts/guards/_stamp.py --kind проба --role ВЕРДИКТ \\
                 --what "чем закрыт" --closes {ключ}
  отложить:  .venv/bin/python scripts/guards/_stamp.py --kind проба --role ОТЛОЖЕН \\
                 --what "почему сейчас не он" --closes {ключ} --cmd "<чем вернуться>"
  взять:     .venv/bin/python scripts/guards/_stamp.py --kind проба --role "В РАБОТЕ" \\
                 --what "чем занимаюсь по нему сейчас" --closes {ключ}

Ключ подставлен — старшего из показанных; для другого возьми его ключ из списка выше.

Признанный из показа не исчезает — он не закрыт; исчезает только запрет. Чужую развилку
(«решение владельца», «подтвердить») своим решением не закрывают — спрашивают.

Читать и смотреть можно: запрет стоит только на записи. {дверь}"""


ЦИКЛ = """Сторож намерений: коммит трогает зону цикла, а вердикта цикла свежее правки нет.

В зоне сравнения — {n}: {файлы}
Последний вердикт цикла: {когда}

Гейт поставки на этот вопрос НЕ отвечает. Он говорит «сейчас ничего не сломано» (семь джоб
`ci.yml`), а цикл — «правка не сменила цвет ни одной проверки СКРЫТНО»: гоняет наборы поведения
и дом-наборы сторожей дважды, на `HEAD` и на патче, и сравнивает исходы. Списки не пересекаются,
поэтому зелёный гейт про скрытую смену цвета не говорит ничего.

Прогнать надо СЕЙЧАС: цикл сравнивает НЕЗАКОММИЧЕННЫЙ патч, после коммита сравнивать нечем.

    .venv/bin/python scripts/guards/what_if.py --intent <объявление>.yaml

Объявление — `intent`, `where`, `why` и `expect` (список ожиданий либо `[]`, если утверждаешь,
что цвет не меняется). {цена} — это осознанная плата.

{дверь}"""


def _дверь() -> str:
    """Текст двери берётся у источника: три отказа не должны держать три расходящиеся копии."""
    try:
        import _permit
        return _permit.текст("VPM_INTENT_GUARD")
    except Exception:                          # noqa: BLE001 — источника нет, но путь назвать надо
        return "Запрет снимает человек: scripts/guards/_permit.py --запрос VPM_INTENT_GUARD"


STOP, ЗАПРЕТ, ЦИКЛ = (шаблон.replace("{дверь}", _дверь()) for шаблон in (STOP, ЗАПРЕТ, ЦИКЛ))


def зона_коммита(root: Path = PROJ) -> list[str]:
    """Файлы будущего коммита, попадающие в зону сравнения цикла — и в индексе, и в дереве."""
    файлы: set[str] = set()
    for аргументы in (["diff", "--cached", "--name-only", "-z"], ["diff", "--name-only", "-z"]):
        try:
            вывод = subprocess.run(["git", "-C", str(root), *аргументы],
                                   capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return []                          # git не ответил — молчим, а не обвиняем
        файлы |= {p for p in вывод.split("\0") if p}
    return sorted(p for p in файлы if ЗОНА_ЦИКЛА.match(p))


def вердикт_цикла(root: Path = PROJ) -> float:
    """Время последнего вердикта цикла. Журнала нет — ноль, а не отказ."""
    свежий = 0.0
    for path in sorted(root.joinpath("tests", ".journal").glob("stamps-*.jsonl")):
        for row in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                запись = json.loads(row)
            except json.JSONDecodeError:
                continue
            if запись.get("kind") == "цикл" and запись.get("role") == "ВЕРДИКТ":
                свежий = max(свежий, float(запись.get("ts") or 0))
    return свежий


def цена_прогона(root: Path = PROJ) -> str:
    """Сколько стоит цикл — по ЗАМЕРУ из журнала: константа в тексте стареет молча."""
    try:
        import _stamp
        замеры = [м["секунд"] / м["прогонов"] for м in _stamp.цена(root).values() if м["секунд"]]
    except Exception:                          # noqa: BLE001 — цена не важнее самого отказа
        замеры = []
    if not замеры:
        return "Длительность прогона на этом дереве ещё не замерялась"
    return (f"Прогон на этом дереве стоит около {sum(замеры) / len(замеры):.0f} секунд "
            f"(замер по {len(замеры)} намерениям)")


def судить_цикл(data: dict, root: Path = PROJ) -> str:
    """Причина запрета на коммит — либо пустая строка. Свежесть судится ПРАВКОЙ, а не наличием."""
    if data.get("tool_name") != "Bash":
        return ""
    команда = (data.get("tool_input") or {}).get("command", "")
    if not КОММИТ.search(ТЕЛО_HEREDOC.sub("", команда)):
        return ""
    зона = зона_коммита(root)
    if not зона:
        return ""                              # правка вне зоны: циклу там сравнивать нечего
    правка = max((( root / f).stat().st_mtime for f in зона if (root / f).exists()), default=0.0)
    вердикт = вердикт_цикла(root)
    if вердикт > правка:
        return ""
    когда = time.strftime("%m-%d %H:%M", time.localtime(вердикт)) if вердикт else "нет ни одного"
    return ЦИКЛ.format(n=len(зона), файлы=", ".join(зона[:6]) + (" …" if len(зона) > 6 else ""),
                       когда=когда, цена=цена_прогона(root))


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
    """Сколько подписей роли поставлено с начала суток. Дома нет — ноль, а не отказ.

    Спрашивается ДОМ остатков, а не след дня: у остатка один хозяин, и второй читатель разошёлся
    бы с ним ровно в тот день, когда след почистят.
    """
    path = root / "scripts" / "guards" / "tails.jsonl"
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
    return START.format(n=len(хвосты), tails="\n".join(строки), ключ=хвосты[0]["key"])


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


ЗАМЫСЕЛ_ЗАПРЕТ = """Сторож намерений: правка в судимой зоне без объявленного ЗАМЫСЛА.

Задет `{цель}` — дерево, где правка меняет поведение, а не запись о нём.

Замысел объявляется ДО кода, и в этом весь смысл: после правки честно объявить можно только
«ничего не изменится» либо «появится проверка, которую я сам дописал». Замер по журналу: 538
ожиданий из 547 были именно такими (F241). Предсказание в этой точке невозможно физически.

    .venv/bin/python scripts/guards/_stamp.py --kind проба --role ЗАМЫСЕЛ \\
        --what "что делаю и зачем" \\
        --expected "какая СУЩЕСТВУЮЩАЯ проверка сменит цвет и почему"

Подпись сама запишет, была ли зона чиста в этот момент: объявленный на уже правленной зоне
замысел засчитывается как пересказ и поднимает свою ось. Обмануть можно, но не молча.

Выключить: VPM_INTENT_GUARD=off (решает человек, не сторож)."""


def _судимые_корни(root: Path = PROJ) -> list[str]:
    """Деревья, где замысел обязателен. Объявление, а не список в коде: границу двигают правкой
    `evidence.yaml`, и она та же, что у радиуса."""
    try:
        import _evidence
        return [к for д in (_evidence.деревья() or {}).values() if д.get("замысел")
                for к in д.get("корни") or []]
    except Exception:                          # noqa: BLE001 — нет объявления, нет и требования
        return []


def цель_в_зоне(data: dict, root: Path = PROJ) -> str:
    """Путь из правки, попадающий в судимое дерево. Пусто — зона не задета.

    Смотрится и `file_path`, и ТЕКСТ команды: правка через heredoc в Bash — такая же правка,
    и не увидеть её значило бы судить только дисциплинированный путь.
    """
    корни = _судимые_корни(root)
    if not корни:
        return ""
    поле = data.get("tool_input") or {}
    # Из команды берутся только ЦЕЛИ ЗАПИСИ (`> путь`, `>> путь`, `tee путь`), а не всякий
    # путь в тексте: heredoc с примером пути внутри — упоминание, а не правка, и первая же
    # редакция запретила собственный набор проверок именно так.
    кандидаты = [поле.get("file_path") or ""]
    кандидаты += re.findall(r"(?:>>?|\btee)\s+([\w./-]+\.(?:py|yaml|yml|toml))",
                            поле.get("command") or "")
    for путь in кандидаты:
        # `lstrip("./")` срезал бы ЛЮБЫЕ точки и слэши: `.claude/hooks/x.py` стал бы
        # `claude/hooks/x.py` и перестал попадать в своё дерево — дыра ровно у хуков.
        rel = путь.replace(f"{root}/", "")
        rel = rel[2:] if rel.startswith("./") else rel
        if any(rel == к or rel.startswith(к.rstrip("/") + "/") for к in корни):
            return rel
    return ""


def замыслов_сегодня(root: Path = PROJ) -> int:
    """Сколько ЗАМЫСЛОВ подписано за сутки. Читается журнал дня: в дом остатков замысел не идёт."""
    день = time.strftime("%Y%m%d", time.localtime())
    путь = root / "tests" / ".journal" / f"stamps-{день}.jsonl"
    if not путь.exists():
        return 0
    счёт = 0
    for строка in путь.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            счёт += json.loads(строка).get("role") == "ЗАМЫСЕЛ"
        except json.JSONDecodeError:
            continue
    return счёт


def судить_правку(data: dict, root: Path = PROJ) -> str:
    """Причина запрета на запись — либо пустая строка, если непризнанных хвостов нет."""
    import _stamp
    инструмент = data.get("tool_name", "")
    команда = (data.get("tool_input") or {}).get("command", "")
    пишет = инструмент in ПИШУЩИЕ or (инструмент == "Bash" and ПИШЕТ_КОМАНДА.search(команда))
    if not пишет:
        return ""
    непризнанные = [x for x in _stamp.tails(root) if not x.get("признан")]
    if not непризнанные:
        # Замысел требуется ТОЛЬКО в судимых деревьях: на доках и журнале предсказывать нечего.
        # Требуется его НАЛИЧИЕ, а не чистота зоны: иначе первая же правка запирала бы работу
        # без выхода — своевременность судит ось, а не дверь.
        цель = цель_в_зоне(data, root)
        if цель and not замыслов_сегодня(root):
            return ЗАМЫСЕЛ_ЗАПРЕТ.format(цель=цель)
        return ""
    строки = [f"- `{x['key']}` · {x['what']}\n  ждали: {x.get('expected') or '—'}\n"
              f"  продолжить: `{x.get('cmd') or '—'}`\n" for x in непризнанные]
    return ЗАПРЕТ.format(n=len(непризнанные), tails="\n".join(строки),
                         ключ=непризнанные[0]["key"])


def снят() -> bool:
    """Запрет снят человеком? Ошибка = НЕ снят: выключатель, ломающийся в «выкл», не выключатель."""
    try:
        import _permit
        return _permit.снят("VPM_INTENT_GUARD")
    except Exception:                          # noqa: BLE001 — моста нет, значит и разрешения нет
        return False


def main() -> None:
    if снят():
        sys.exit(0)
    try:
        import _trace
        _trace.mark("vpm-intent-guard.py")
    except Exception:                          # noqa: BLE001 — след не важнее самой проверки
        pass
    data = json.loads(sys.stdin.read() or "{}")
    if data.get("hook_event_name") == "PreToolUse":
        # Хвосты судятся ПЕРВЫМИ: непризнанный остаток запрещает и правку, и коммит, а цикл
        # спрашивается только у того, кому уже позволено писать.
        if (запрет := судить_правку(data) or судить_цикл(data)):
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
