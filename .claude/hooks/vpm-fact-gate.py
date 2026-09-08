#!/usr/bin/env python3
"""vpm-fact-gate.py — гейт фактов проекта video_pipeline_mcp (скилл `verified-edits`).

Требует не самооценку («ты уверен?» — ответ всегда «да»), а конкретные факты: кто читает, какой
контракт задет, что говорит история файла, что дословно просил владелец. Сам поиск меняет правку.

Два наблюдения, одно состояние:
  PreToolUse  — НАМЕРЕНИЕ: правка несущего файла, разрушительная команда и кириллица в
                ОБОЛОЧКЕ — отказ ДО действия.
  PostToolUse — ЭФФЕКТ: после Bash смотрим, что реально стало грязным в git. Наблюдается
                результат, а не текст команды, поэтому оболочкой это не обходится; отменить
                запись хук не может и требует факты до следующего шага.

Один отказ на файл/команду за сессию. Выключить: VPM_FACT_GATE=off. Ошибка = пропуск.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
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
PROJ = Path(__file__).resolve().parents[2]
STATE_DIR = Path.home() / ".claude" / "state" / "vpm-fact-gate"
STATE_TTL = 12 * 3600

# Зона задаётся ВЫЧИТАНИЕМ, а не перечнем несущих мест: перечень — снимок, и он гниёт. При перечне
# `run.sh`, `pyproject.toml` и сам `ci.yml` правились молча; вычитанием новый файл покрыт сразу.
EXEMPT = ("tests/", "docs/", "workspace/", "vendor/", ".venv/", "__pycache__/", "_archive/",
          "logs/", ".git/")

DESTRUCTIVE = re.compile(
    r"(\brm\s+-[a-z]*[rf]|\bgit\s+reset\s+--hard|\bgit\s+clean\s+-[a-z]*f"
    r"|\bgit\s+checkout\s+(--\s+)?\.|\bgit\s+push\s+(-f\b|--force)"
    r"|\bsed\s+-i\b|\btruncate\b|\bdd\s+if=|\bdrop\s+table\b)",
    re.I,
)
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
# Класс нарушения, а не замеченный пример: НЕ-ASCII в позиции, где bash ждёт ИМЯ. Алфавит не
# перечисляется (кириллица, греческий, украинская «і» ломаются одинаково), позиций ровно три —
# подстановка, присваивание и слово, забирающее имя. Имя из ASCII с одной чужой буквой внутри
# (`klon_дом=`) незаконно так же, как целиком чужое.
ИМЯ_ASCII = r"[A-Za-z0-9_]*"
ЧУЖАЯ = r"[^\x00-\x7F]"
# Подстановка ищется ВЕЗДЕ: `$имя` разворачивается и внутри двойных кавычек.
ПОДСТАНОВКА = re.compile(rf"\$\{{?{ЧУЖАЯ}")
# Присваивание — только ВНЕ двойных кавычек: `;`, `&`, `|`, `(` внутри аргумента это литералы,
# а не разделители команд. Дважды поймано на себе: `--what "…(было=0…"` и `grep -n "…|пишет=…"`.
ПРИСВАИВАНИЕ = re.compile(
    rf"(?:^|[;&|(]|\b(?:export|declare|local|readonly|typeset)\s+)"
    rf"\s*{ИМЯ_ASCII}{ЧУЖАЯ}[^\s=]*="
    rf"|\b(?:unset|read|for)\s+{ИМЯ_ASCII}{ЧУЖАЯ}",
    re.M)
DOUBLE_QUOTED = re.compile(r'"[^"]*"')


def чужое_имя(text):
    """Не-ASCII там, где оболочка ждёт ИМЯ. Подстановку ищем везде, присваивание — вне кавычек."""
    return ПОДСТАНОВКА.search(text) or ПРИСВАИВАНИЕ.search(DOUBLE_QUOTED.sub("", text))
COMMIT = re.compile(r"\bgit\s+(?:-\S+\s+)*commit\b")
# Команды, чей ВЕРДИКТ читают по коду возврата. Список поимённый: у произвольной команды кода
# возврата тоже ждут, но обвинять каждый конвейер значило бы выключить сторожа в первый же день.
# Судится ГОЛОВА звена, а не вхождение: имя набора встречается аргументом `grep` куда чаще,
# чем прогоном, и вхождение дало ложную тревогу на первой же команде после правки.
ЗАПУСК = re.compile(r"""^\s*(?:\w+=\S+\s+)*                      # env-присваивания
                        (?:sudo\s+|timeout\s+\d+\s+|npx\s+)*      # обёртки
                        (?:\S*(?:python3?|/python)\s+(?:-m\s+)?)? # интерпретатор
                        (pytest|ruff\s+check|mypy|bandit|pip-audit|gitleaks
                         |npm\s+run\s+(?:--silent\s+)?(?:typecheck|lint|build|geometry)
                         |\S*scripts/guards/\w+\.py
                         |\S*tests/\S+\.py)\b""", re.X)
# Код возврата берётся у самой команды: `${PIPESTATUS[0]}` либо вывод в файл, а код отдельно.
КОД_У_КОМАНДЫ = re.compile(r"PIPESTATUS|\|\s*tee\b")
SKIP_DOOR = re.compile(r"--no-verify\b|\s-n\b")
# Тело here-document — ДАННЫЕ, а не команда. Без этого файл, в тексте которого упомянута
# опасная команда, запрещает сам себя записать (поймано на этом же хуке).
# После маркера в строке законно стоит ещё команда (`cmd <<'EOF' && next`), а тело всё равно
# начинается со следующей строки: без хвоста правило снимало ровно одну форму записи.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\r?\n.*?\r?\n\2\b", re.S)

# Запись файла — тоже правка, но приходит она не Edit-ом, а Bash-ем: `cat > f <<EOF`,
# `python3 - <<PY … write_text`. Слушать только Edit/Write значит пропустить весь рабочий день.
REDIRECT = re.compile(r">>?\s*(?P<path>[\w./-]+\.(?:py|md|ya?ml|json|txt|sh|toml|cfg|ini))")
EXEC_HEREDOC = re.compile(r"\b(?:python3?|bash|sh|zsh)\b[^\n|;&]*<<-?\s*[\'\"]?\w+", re.I)
# `python3 -c` пишет ровно так же, как heredoc, и литерал в нём разрешим ТОЧНО: без этой строки
# граница шла бы по ФОРМЕ команды, тогда как объявлена она по литералам.
EXEC_INLINE = re.compile(r"\b(?:python3?)\b[^\n|;&]*\s-c\b", re.I)
# Данные, а не оболочка: тело here-document И аргумент `-c`. Обе формы несут ЧУЖОЙ язык,
# в котором кириллица законна; судить их правилами bash значило бы отказывать на работе.
INLINE_CODE = re.compile(r"-c\s+'[^']*'|-c\s+\"[^\"]*\"", re.S)
# В ОДИНАРНЫХ кавычках оболочка не подставляет ничего — это данные (шаблон grep, текст
# сообщения). В двойных подстановка есть, поэтому они судятся по-прежнему.
SINGLE_QUOTED = re.compile(r"'[^']*'")
# Цель записи достаётся ТОЧНО, а не «любой путь в тексте»: скрипт, который читает несущий файл и
# пишет журнал, ложного отказа получать не должен — выключенный сторож не ловит ничего.
PY_DIRECT = re.compile(r"""Path\(\s*['"]([^'"]+)['"]\s*\)\s*\.write_text|"""
                       r"""open\(\s*['"]([^'"]+)['"]\s*,\s*['"][wa]""")
PY_BIND = re.compile(r"""(\w+)\s*=\s*Path\(\s*['"]([^'"]+)['"]""")
# `корень = Path("a")` плюс `цель = корень / "b" / "c.md"`: путь собран из ЛИТЕРАЛОВ, поэтому
# разрешается точно и судится наравне с прямым `Path("…")`. Вычисляемый (f-строка с переменной,
# глоб) здесь не разрешим и остаётся на постфактумной половине.
PY_JOIN = re.compile(r"""(\w+)\s*=\s*(\w+)((?:\s*/\s*['"][^'"]+['"])+)""")
JOIN_PART = re.compile(r"""/\s*['"]([^'"]+)['"]""")
PY_VAR_WRITE = re.compile(r"""(\w+)\.(?:write_text|writelines|write_bytes)\(""")
REAL_PATH = re.compile(r"^/?(?:[\w.-]+/)*[\w.-]+\.(?:py|md|ya?ml|json|txt|sh|toml|cfg|ini)$")

FACTS = """1. Кто это читает/вызывает: `grep -rn "<имя>" --include='*.py' core server.py tools tests`
   Ноль вызывающих — это находка (мёртвая половина), а не разрешение править молча.
2. Какой контракт задет: ToolResult/ErrorDetail, код реакции в config/server_reactions.yaml,
   объявление в config/*.yaml. Контракт, видимый клиенту, меняется отдельным решением.
3. Что уже решено здесь: `git log --oneline -5 -- <файл>` + известные D#/F#
   (docs/roadmap/02_findings.md) — не переоткрываешь ли закрытое.
4. Дословная текущая инструкция владельца — процитируй, не пересказывай."""

EDIT_MSG = f"""Гейт фактов: {{path}} — правка несущего файла без предъявленных фактов.

Предъяви (командами, не по памяти), потом повтори правку — она пройдёт:
{FACTS}

Не отвечай по памяти: смысл гейта в самом поиске. Выключить: VPM_FACT_GATE=off."""

ХВОСТ_MSG = """⚠️ Вердикт читается у ХВОСТА конвейера, а не у прогона: `{кусок}`

Код возврата конвейера — это код ПОСЛЕДНЕЙ команды. `pytest … | tail` вернёт 0 при красных
тестах, и «зелёно» будет названо по коду `tail` (F247, поймано на этом проекте 2026-09-07:
рапорт «exit code 0» при `1 failed, 35 passed`).

Прочитать вердикт у самой команды — одно из двух:
    <команда> > /tmp/прогон.log 2>&1; echo "код=$?"; tail -20 /tmp/прогон.log
    <команда> 2>&1 | tail -20; echo "код=${{PIPESTATUS[0]}}"

Если конвейер здесь ради ЧТЕНИЯ вывода, а не ради вердикта — это сообщение не про тебя.

Выключить: VPM_FACT_GATE=off.
"""


POST_MSG = f"""⚠️ Гейт фактов (постфактум): команда изменила несущие файлы мимо Edit/Write — {{paths}}

Запись уже произошла, отменить её хук не может. Предъяви факты СЕЙЧАС, до следующего шага:
{FACTS}

Не сходится — откати файл и сделай заново. Наблюдение идёт по диску, а не по тексту команды:
обойти оболочкой нельзя, но и предотвратить нельзя — см. `verified-edits` §0."""

SUITE_MSG = """Гейт фактов: {path} — РОЖДЕНИЕ набора, а это решение «не плодить», не правка.

Ответь тремя строками (правило tests/CATALOG.md §2), потом повтори — пройдёт:
1. Естественный хозяин: чья зона ближе всего к предмету? (таблица ниже)
2. Его запас исчерпан? Впитать сделало бы набор разнородным — чем именно?
3. Почему это НЕ выражается объявлением? Переход карты умеет `call` инструмента сервера;
   предмет — сервер? тогда место не здесь, а в tests/scenarios/.

Ответы идут в строку-зону нового набора, а не в переписку. Выключить: VPM_FACT_GATE=off."""

DOOR_MSG = """Гейт фактов: коммит мимо двери — `{cmd}`

{reason}

Дверь коммита (`pre-commit`: ruff, mypy, import-linter, три сторожа) — единственное, что не пускает
немой промах в историю; CI ловит то же самое уже после `push`, когда история написана.
Поставить: `.venv/bin/pre-commit install`. Красный хук — вердикт, а не помеха: чини причину.

Выключить гейт: VPM_FACT_GATE=off."""

NON_ASCII_MSG = """Гейт фактов: не-ASCII в имени оболочки — `{кусок}`

Имена переменных bash берёт только из ASCII, поэтому это не подстановка: доллар и буквы доедут
до команды БУКВАЛЬНО и создадут файл или каталог с таким именем. Дважды так и вышло — `$ДОМ/` и
`$К/` в корне репозитория; оба пришлось сносить, отката у них нет.

Перепиши: имя переменной латиницей (`KLON=…`, `"$KLON"`), а русские пути и тексты держи в
КАВЫЧКАХ как значения. В коде на Python кириллица законна и правилом не задета — речь только
об оболочке."""

BASH_MSG = """Гейт фактов: разрушительная команда — `{cmd}`

Предъяви, потом повтори:
1. Что именно она изменит/удалит — перечисли по факту (`git status`, `ls`), не по намерению.
2. Откат одной строкой. Отката нет — скажи это прямо и спроси владельца.
3. Дословная инструкция, по которой ты это делаешь.

Для правки на месте третий пункт заменяется на пост-условие: какая команда докажет, что правка
попала (промах немой — exit 0 без изменений). См. `verified-edits`."""

GROWTH_MSG = """Подсказка зоны: правка ДОБАВЛЯЕТ проверки в существующий набор {path}.

Ответь двумя строками, потом повтори — пройдёт:
1. Что стоит ЗАПАСОМ этого набора (строка ниже) — добавляемое попадает в него дословно?
2. Не попадает — чья зона ближе и что мешает положить туда?

Правило «не плодить» держится на обеих сторонах: новый набор рядом — решение, но и рост ВНУТРИ
набора мимо его запаса делает набор разнородным ровно так же, а на это не смотрит ни одна ось.
Выключить: VPM_FACT_GATE=off."""

SHORT = "Гейт фактов ({n}-й отказ): факты по {what} не предъявлены — см. `verified-edits`."
AGAIN = ("Гейт фактов: с прошлого отказа по {what} ты ничего не СМОТРЕЛ — ни grep, ни git log, ни чтения.\n"
         "Предъяви факты командой (её вывод и есть улика), потом повтори правку.")

# Команда разведки: ею смотрят, а не правят. Список широкий намеренно — задача не поймать
# хитреца, а отличить «пошёл и посмотрел» от «повторил ту же правку второй раз».
# `ls` и `wc` из списка сняты намеренно: перечислить имена — не значит посмотреть содержимое,
# а ритуал, снимаемый ритуалом, не проверяет ничего.
PROBE = re.compile(r"\b(grep|rg|git\s+(log|show|diff|blame)|find|head|tail|sed\s+-n|cat|python3?\s+-c)\b")
# Запоминается ИМЯ файла, а не факт хождения: без имени «смотрел вообще» отпирало бы любую правку,
# а взгляд ДО первой правки не отпирал бы ничего — и отказ печатался бы на предъявленные факты.
ПУТЬ_В_КОМАНДЕ = re.compile(r"[\w./-]+\.(?:py|md|ya?ml|json|txt|sh|toml|cfg|ini)")


def load_state(sid: str) -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in STATE_DIR.glob("*.json"):
        try:
            if now - old.stat().st_mtime > STATE_TTL:
                old.unlink()
        except OSError:
            pass
    try:
        return json.loads((STATE_DIR / f"{sid}.json").read_text())
    except (OSError, ValueError):
        return {"cleared": [], "denials": 0, "probe": 0.0, "denied": {}}


def save_state(sid: str, st: dict) -> None:
    try:
        (STATE_DIR / f"{sid}.json").write_text(json.dumps(st))
    except OSError:
        pass


RADIUS = PROJ / "tests" / ".blast" / "radius.json"
MEASURED = ("core/", "tools/", "server.py")


def radius_hint(rel: str) -> str:
    """Замер ВМЕСТО требования замера: что держит это место, гейт знает сам и говорит сразу.

    Просить «сделай замер» — ритуал: он удовлетворяется одной фразой. Отдать сам замер дешевле и
    честнее, а «карты нет» и «место не держит никто» — разные ответы, и путать их нельзя.
    """
    if not rel.startswith(MEASURED):
        return ""
    try:
        data = json.loads(RADIUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ("\n\nЗамер места: карты радиуса нет — чем держится этот файл, неизвестно "
                "(`python3 scripts/guards/blast_radius.py --build`).")
    holders = sorted(data.get(rel) or {})
    target = PROJ / rel
    stale = target.exists() and RADIUS.stat().st_mtime < target.stat().st_mtime
    if not holders:
        head = f"\n\nЗамер места: {rel} не исполняет НИ ОДИН сценарий — правка здесь тестам невидима."
    else:
        head = (f"\n\nЗамер места: {rel} держат {len(holders)} сценариев — "
                f"{', '.join(holders[:6])}{' …' if len(holders) > 6 else ''}.")
    if stale:
        head += " ⚠ карта старше файла: имена верны, номера строк уже сдвинулись."
    return head + "\n  Радиус правки и слепые зоны: `blast_radius.py --affected` (обе улики, включая граф вызовов)."


def gated(rel: str) -> bool:
    return not rel.startswith(EXEMPT)


def dirty_gated() -> list[str]:
    """Грязные файлы несущих зон по git. Пустой список и при любой ошибке — fail-open."""
    try:
        out = subprocess.run(["git", "-C", str(PROJ), "status", "--porcelain", "-z"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    fields, res, i = out.stdout.split("\0"), [], 0
    while i < len(fields):
        f = fields[i]
        i += 1
        if len(f) < 4:
            continue
        code, path = f[:2], f[3:]
        if code[0] in "RC":          # переименование: следом идёт исходный путь
            i += 1
        if gated(path):
            res.append(path)
    return sorted(res)


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(0)


def deny(reason: str) -> None:
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                 "permissionDecision": "deny",
                                 "permissionDecisionReason": reason}})


def in_tree(path: str) -> str | None:
    try:
        return Path(path).resolve().relative_to(PROJ).as_posix()
    except (ValueError, OSError):
        return None


def relative(path: str) -> str | None:
    rel = in_tree(path)
    return rel if rel and gated(rel) else None


def written_paths(raw: str) -> list[str]:
    """Пути в дереве, в которые команда ПИШЕТ. Пусто — записи не обнаружено (это не «чисто»)."""
    # Кавычки снимаем, как и для разрушительных: путь внутри строки — упоминание, а не цель.
    found: list[str] = [m.group("path")
                        for m in REDIRECT.finditer(QUOTED.sub("", HEREDOC.sub("", raw)))]
    if EXEC_HEREDOC.search(raw) or EXEC_INLINE.search(raw):
        found += [g for m in PY_DIRECT.finditer(raw) for g in m.groups() if g]
        bound = {m.group(1): m.group(2) for m in PY_BIND.finditer(raw)}
        # Склейка бывает многоступенчатой (`a = Path(...)`, `b = a / "x"`, `c = b / "y.md"`),
        # поэтому проход повторяется, пока прибавляются имена, а не один раз.
        joins = [(m.group(1), m.group(2), m.group(3)) for m in PY_JOIN.finditer(raw)]
        for _ in range(len(joins)):
            for имя, база, хвост in joins:
                if имя not in bound and база in bound:
                    bound[имя] = "/".join([bound[база], *JOIN_PART.findall(хвост)])
        found += [bound[m.group(1)] for m in PY_VAR_WRITE.finditer(raw) if m.group(1) in bound]
    out: list[str] = []
    for cand in found:
        # Строка в коде — ещё не путь: текст про связку Path/write_text поймал сам себя на первом
        # же применении. Цель обязана иметь известное расширение и существующий каталог-родитель.
        if not REAL_PATH.match(cand):
            continue
        rel = in_tree(cand if cand.startswith("/") else str(PROJ / cand))
        if rel and rel not in out and (PROJ / rel).parent.is_dir():
            out.append(rel)
    return out


NEW_SUITE = re.compile(r"^tests/(?:[\w.-]+/)*test_[\w.-]+\.py$")


def suite_birth(rel: str) -> bool:
    """Рождение набора — решение «не плодить», а не правка. Правки существующих тестов не трогаем."""
    return bool(NEW_SUITE.match(rel)) and not (PROJ / rel).exists()


CHECK_LINE = re.compile(r"^\s*(?:ok\(|assert |def test_|-\s*(?:name|call):)")


def suite_growth(rel: str, added: str) -> bool:
    """Рост ВНУТРИ набора: проверки прибавляются к существующему файлу тестов.

    Рождение набора — соседняя дверь; здесь файл уже есть, и вопрос не «плодить ли», а «в своей ли
    зоне он растёт». Пустая правка (переименование, чистка) двери не открывает: улика — добавленная
    строка ПРОВЕРКИ, а не факт касания файла.
    """
    return (rel.startswith("tests/") and rel.endswith((".py", ".yaml", ".yml"))
            and (PROJ / rel).exists()
            and any(CHECK_LINE.match(line) for line in added.splitlines()))


def zone_row(rel: str) -> str:
    """Строка ЭТОГО набора из каталога зон: своя зона и свой запас, а не таблица целиком."""
    catalog = PROJ / "tests" / "CATALOG.md"
    # Зона объявляется файлу ЛИБО каталогу: у сценариев хозяин — `scenarios/` целиком, и поиск
    # только по имени файла выдавал бы «зоны нет» там, где она есть. Те же ключи, что у сторожа.
    keys = [Path(rel).name, f"{Path(rel).parent.name}/"]
    name = keys[0]
    if catalog.exists():
        for line in catalog.read_text(encoding="utf-8").splitlines():
            if line.startswith("| `") and any(k in line.split("|")[1] for k in keys):
                cells = [c.strip() for c in line.strip("|").split(" | ")]
                if len(cells) >= 5:
                    return (f"\n\nЗона {cells[0]}: {cells[1][:220]}"
                            f"\n  ЗАПАС: {cells[3][:220]}"
                            f"\n  Новый рядом оправдан: {cells[4][:120]}")
    return (f"\n\nЗоны у {name} в tests/CATALOG.md НЕТ — набор растёт вне объявленной "
            "ответственности, и это уже находка, а не вопрос.")


def zone_hint() -> str:
    """Замер ВМЕСТО требования: таблица зон с запасами отдаётся сразу, её не надо просить."""
    catalog = PROJ / "tests" / "CATALOG.md"
    if not catalog.exists():
        return "\n\nЗамер зон: tests/CATALOG.md нет — реестра зон не существует."
    rows = []
    for line in catalog.read_text(encoding="utf-8").splitlines():
        if line.startswith("| `test"):
            cells = [c.strip() for c in line.strip("|").split(" | ")]
            if len(cells) >= 4:
                rows.append(f"  {cells[0]:32} запас: {cells[3][:88]}")
    return (f"\n\nЗамер зон: наборов с объявленной зоной — {len(rows)}. Их запас расширения:\n"
            + "\n".join(rows))


def door_path(root: Path = PROJ) -> Path:
    """Где лежит дверь. В worktree `.git` — ФАЙЛ, а хуки общие, поэтому путь спрашиваем у git:
    иначе сторож объявил бы «двери нет» ровно там, где она стоит (поймано циклом)."""
    try:
        done = subprocess.run(["git", "rev-parse", "--git-path", "hooks/pre-commit"],
                              cwd=str(root), capture_output=True, text=True, timeout=10)
        named = Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        named = None
    if named is None:
        return root / ".git" / "hooks" / "pre-commit"
    return named if named.is_absolute() else root / named


def commit_door(command: str, root: Path = PROJ) -> str:
    """Причина отказать коммиту: дверь снимают флагом или её нет на месте. Иначе пустая строка."""
    if not COMMIT.search(command):
        return ""
    if SKIP_DOOR.search(command):
        return "Флаг снимает проверки коммита — ровно то, ради чего дверь стоит."
    if not door_path(root).exists():
        return ("Двери нет вовсе: `.git/hooks/pre-commit` отсутствует, и её молчание неотличимо "
                "от пройденных проверок.")
    return ""


def вердикт_у_хвоста(command: str) -> str:
    """Кусок конвейера, чей код возврата припишут прогону, — либо пустая строка.

    Судится ПЕРВОЕ звено: код отдаёт последнее, поэтому виноват тот конвейер, что начинается
    прогоном. Конвейер ради чтения вывода законен и встречается всюду; отличает их только
    попытка взять код — `${PIPESTATUS[0]}` или `tee`, — и при ней сторож молчит.
    """
    if КОД_У_КОМАНДЫ.search(command):
        return ""
    for кусок in re.split(r"&&|\|\||;|\n", command):
        if "|" not in кусок:
            continue
        первое = кусок.split("|", 1)[0]
        if ЗАПУСК.match(первое):
            return кусок.strip()[:120]
    return ""


def verdict(rel: str, added: str = "") -> tuple[str, str, str] | None:
    """(ключ, о чём, сообщение) для пути в дереве — либо None, если гейта он не касается."""
    if suite_birth(rel):
        return f"suite:{rel}", rel, SUITE_MSG.format(path=rel) + zone_hint()
    if suite_growth(rel, added):
        return f"zone:{rel}", rel, GROWTH_MSG.format(path=rel) + zone_row(rel)
    if gated(rel):
        return f"file:{rel}", rel, EDIT_MSG.format(path=rel) + radius_hint(rel)
    return None


def pre(data: dict, st: dict, sid: str) -> None:
    tool, inp = data.get("tool_name", ""), (data.get("tool_input") or {})
    if tool in ("Edit", "Write", "MultiEdit"):
        rel = in_tree(inp.get("file_path", ""))
        added = "\n".join([str(inp.get("content") or ""), str(inp.get("new_string") or "")]
                          + [str(e.get("new_string") or "") for e in (inp.get("edits") or [])])
        found = verdict(rel, added) if rel else None
        if not found:
            sys.exit(0)
        key, what, full = found
    elif tool == "Bash":
        raw = inp.get("command", "")
        # Дверь коммита — СОСТОЯНИЕ, а не размышление: повтор его не меняет, поэтому отказ
        # идёт мимо счётчика послаблений и держится, пока состояние не исправят.
        door = commit_door(QUOTED.sub("", HEREDOC.sub("", raw)))
        if door:
            deny(DOOR_MSG.format(cmd=raw[:200], reason=door))
        # Тело here-document — не оболочка, а данные: питон с русскими именами законен, и без
        # снятия тел правило запретило бы половину рабочих команд.
        чужое = чужое_имя(SINGLE_QUOTED.sub("", INLINE_CODE.sub("", HEREDOC.sub("", raw))))
        if чужое:
            deny(NON_ASCII_MSG.format(кусок=чужое.group(0)[:40]))
        # Запись файла из Bash — та же правка: цель ищется в команде, а не ожидается от Edit.
        for rel in written_paths(raw):
            found = verdict(rel, raw)
            if found and found[0] not in st["cleared"]:
                key, what, full = found
                break
        else:
            m = DESTRUCTIVE.search(QUOTED.sub("", HEREDOC.sub("", raw)))
            if not m:
                sys.exit(0)
            key, what = f"cmd:{m.group(0).lower()}", "команде"
            full = BASH_MSG.format(cmd=raw[:200])
    else:
        sys.exit(0)

    if key in st["cleared"]:
        sys.exit(0)

    # Повторный заход пропускается не «потому что второй», а если между отказом и им ты ХОДИЛ
    # смотреть. Иначе гейт был бы ритуалом: отклонил раз, пропустил что угодно на второй.
    # Взгляд НА ЭТОТ файл до правки — те самые предъявленные факты. Без этой ветки гейт брал бы
    # пошлину по одному отказу на файл даже с дисциплинированного пути: замер дал 18 отказов при
    # 18 разных ключах и нуле упёртых.
    if key.startswith("file:") and key[5:] in (st.get("смотрел") or {}):
        st["cleared"].append(key)
        save_state(sid, st)
        sys.exit(0)

    denied_at = float((st.get("denied") or {}).get(key) or 0)
    if denied_at:
        looked = float(st.get("probe") or 0) > denied_at
        stuck = int((st.get("denied_count") or {}).get(key) or 0) >= 3
        if looked or stuck:
            st["cleared"].append(key)
            save_state(sid, st)
            sys.exit(0)
        st.setdefault("denied_count", {})[key] = int((st.get("denied_count") or {}).get(key) or 0) + 1
        save_state(sid, st)
        deny(AGAIN.format(what=what))

    st.setdefault("denied", {})[key] = time.time()
    st["denials"] = st.get("denials", 0) + 1
    save_state(sid, st)
    # Первые три отказа — полный список; дальше строка: одинаковые блоки в окне работают как шум.
    deny(full if st["denials"] <= 3 else SHORT.format(n=st["denials"], what=what))


def post(data: dict, st: dict, sid: str) -> None:
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    команда = (data.get("tool_input") or {}).get("command", "")
    if PROBE.search(команда):
        st["probe"] = time.time()               # ходил смотреть — это и отпирает повтор правки
        смотрел = st.setdefault("смотрел", {})
        for путь in ПУТЬ_В_КОМАНДЕ.findall(команда):
            смотрел[путь.lstrip("./")] = st["probe"]
        save_state(sid, st)
    if (кусок := вердикт_у_хвоста(команда)) and f"хвост:{кусок}" not in st["cleared"]:
        st["cleared"].append(f"хвост:{кусок}")
        save_state(sid, st)
        emit({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                     "additionalContext": ХВОСТ_MSG.format(кусок=кусок)}})
    new = [p for p in dirty_gated()
           if f"file:{p}" not in st["cleared"] and p not in st.get("baseline", [])]
    if not new:
        sys.exit(0)
    st["cleared"].extend(f"file:{p}" for p in new)
    save_state(sid, st)
    emit({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                 "additionalContext": POST_MSG.format(paths=", ".join(new[:6]))}})


def main() -> None:
    if os.environ.get("VPM_FACT_GATE", "").lower() in ("off", "0", "false"):
        sys.exit(0)
    if not PROJ.is_dir():
        sys.exit(0)

    data = json.loads(sys.stdin.read())
    # Хвост-хеш обязателен: вырезание не-ASCII схлопывает РАЗНЫЕ ключи сессий в одно имя,
    # и состояние одной протекает в другую — гейт молчит там, где должен отказать.
    сырой = str(data.get("session_id", "nosession"))
    sid = ((re.sub(r"[^A-Za-z0-9_-]", "", сырой)[:48] or "nosession") + "-"
           + hashlib.sha1(сырой.encode("utf-8")).hexdigest()[:8])
    st = load_state(sid)

    if data.get("hook_event_name", "") == "PostToolUse":
        post(data, st, sid)
    else:
        # Снимок «грязного до нас» берётся на ПЕРВОМ намерении: сделай его позже — первая же
        # запись через оболочку попала бы в базу и осталась незамеченной.
        if "baseline" not in st:
            st["baseline"] = dirty_gated()
            save_state(sid, st)
        pre(data, st, sid)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
