#!/usr/bin/env python3
"""vpm-delivery-gate.py — Stop-хук проекта video_pipeline_mcp (скилы `verified-edits`, `docs-governance`).

Механический гейт на СЛОВО «сделано». Проверяет два факта, оба машинно-проверяемые:

A. Заявлено «зелёное» — хук САМ гонит дешёвые проверки. Стенограмма ни при чём: «мелькнула
   команда» не значит «прошло». Дорогие джобы не здесь — поведение проверяет C, форма стоит секунды.
B. Сессия сложная (>=3 правок), а решение→факт нигде не записан: ни журнал, ни реестр, ни память,
   ни коммит за сегодня.
C. Тронут код сервера — нужна УЛИКА прогона, а не слова о нём; по умолчанию ПРОВАЛ. Улика —
   вердикт в `tests/.journal/*.jsonl`, СВЕЖЕЕ последней правки, зелёный и покрывающий задетые
   сценарии. Нет или несвежая — хук гонит задетое САМ и судит по настоящему выводу.

Блокирует ровно один раз (stop_hook_active). Выключить: VPM_DELIVERY_GATE=off; ошибка = пропуск.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Корень — от места самого хука (`.claude/hooks/` в репозитории), а не зашит путём одной
# машины: зашитый путь делает хук неперемещаемым, и у всех, кроме автора, он молчит.
PROJ = Path(__file__).resolve().parents[2]
# Слуг памяти считается из корня тем же правилом, каким его строит сам Claude Code.
MEM = Path.home() / ".claude/projects" / str(PROJ).replace("/", "-") / "memory"

COMPLEX_EDITS = 3
EDIT_CALL = re.compile(r'"name":\s*"(?:Edit|Write|MultiEdit|NotebookEdit)"')
# Доказательство прогона: имя раннера внутри команды инструмента, а не в прозе.
# Дешёвые джобы гейта: секунды на всё. Дорогие (pytest, conformance, pip-audit) — не здесь.
CHEAP = [("ruff", ["ruff", "check", "."]),
         ("mypy", ["mypy"]),
         ("comment-guard", ["python3", "scripts/guards/comment_guard.py", "--check"]),
         ("invariants", ["python3", "scripts/guards/invariants.py", "--check"]),
         ("findings-count", ["python3", "scripts/guards/findings_count.py", "--check"])]

CLAIM = re.compile(
    r"(гейт\w*\s+зел[её]н|тест\w*\s+зел[её]н|вс[её]\s+зел[её]н|зел[её]ные?\s+тест"
    r"|тесты\s+проход|линт\w*\s+чист|all\s+green|tests?\s+pass|gate\s+is\s+green)",
    re.I,
)
# Модальность отменяет утверждение: «нужно, чтобы тесты были зелёные» — это не заявление.
MODAL = re.compile(r"(чтобы|должн|нужно|надо|если|проверь|убедись|перед тем|should|must|need to)", re.I)

RECORDS = ["docs/roadmap/_sessions.md", "docs/roadmap/02_findings.md", "docs/roadmap/18_full_pass.md"]

# Доказательство ПОВЕДЕНЧЕСКОГО прогона: сценарии, маршруты, карта радиуса.
MEASURED = ("core/", "tools/", "server.py")

MSG_A = """Гейт поставки: заявлено «{claim}», а ФОРМА прямо сейчас красная — {failed}.

{tail}

Утверждение о зелёном проверено запуском, а не поиском команды в переписке: сказанное разошлось
с тем, что на диске. Почини и заявляй заново — либо убери утверждение.
Хук прогнал только ФОРМУ (линт, типы, тексты, межфайловые инварианты, счёт реестра) — это НЕ тесты.
Заявление о зелёных ТЕСТАХ подтверждается прогоном сценариев (его проверяет отдельная проверка по
журналу) и полным `pytest`; conformance, pip-audit и gitleaks гоняешь сам."""

MSG_B = """Гейт поставки: {n} правок за сессию, а решение→факт нигде не записан.

Ни одно из этого не тронуто сегодня и коммита за сегодня нет:
  docs/roadmap/_sessions.md · docs/roadmap/02_findings.md · docs/roadmap/18_full_pass.md · память проекта

Запиши, что решено и что стало фактом (`docs-governance`: закрыл шаг — отметь в плане,
в реестре находок и в журнале), либо закоммить сделанное. Работа без записи не переживает сессию."""


MSG_C_RED = """Гейт поставки: тронут код сервера ({files}) — прогнал задетые сценарии, и они КРАСНЫЕ.

    {command}

{tail}

Чинить причину, а не ожидание: провал печатает фактический код отказа и сообщение сервера.
Поток данных задет (что доезжает до клиента) — добавь маршрут и прогони `tests/routes/test_routes.py`.
Меняешь поведение осознанно — покажи диффером: `python3 scripts/guards/what_if.py --intent …`.

Выключить гейт: VPM_DELIVERY_GATE=off."""

MSG_C_BLIND = """Гейт поставки: тронут код сервера ({files}), а сценариев на этих строках НЕТ.

{tail}

Правка в зоне, которую тесты не исполняют, не проверена ничем — гнать нечего. Сначала сценарий
(`tests/scenarios/<тема>.yaml`), потом код. Если поток к клиенту задет — ещё и маршрут
в `tests/routes/routes.yaml`.

Выключить гейт: VPM_DELIVERY_GATE=off."""

MSG_C_STALE = """Гейт поставки: тронут код сервера ({files}), а выбрать прогон НЕЧЕМ — {why}.

Картой радиуса нельзя выбирать сценарии, если она не знает их всех: она спокойно выберет набор,
в котором нужного нет, прогон будет зелёным, и откат уедет как проверенный. Именно так и уезжал.

    python3 scripts/guards/blast_radius.py --build      # пересобрать (минуты, сервер под покрытием)
    python3 tests/scenarios/test_scenarios.py    # либо прогнать матрицу целиком

Выключить гейт: VPM_DELIVERY_GATE=off."""

MSG_C_SLOW = """Гейт поставки: прогон задетых сценариев не уложился в {limit} с и был снят.

    {command}

Прогони сам и посмотри результат: молча пропустить нельзя — непрошедший прогон не является пройденным.

Выключить гейт: VPM_DELIVERY_GATE=off."""


def cheap_failures() -> list[tuple[str, str]]:
    """Дешёвые джобы гейта прямо сейчас: [(имя, хвост вывода)] у тех, что красные."""
    red = []
    for name, argv in CHEAP:
        try:
            done = subprocess.run(argv, cwd=str(PROJ), capture_output=True, text=True, timeout=180,
                                  env={**os.environ, "PATH": f"{PROJ}/.venv/bin:{os.environ.get('PATH','')}"})
        except (OSError, subprocess.SubprocessError):
            continue                              # инструмента нет — не выдумываем провал
        if done.returncode != 0:
            red.append((name, (done.stdout + done.stderr).strip()[-300:]))
    return red


def newest_code_change(touched: list[str]) -> float:
    """Время последней правки измеряемого кода. По нему судится свежесть улики."""
    stamps = []
    for rel in touched:
        try:
            stamps.append((PROJ / rel).stat().st_mtime)
        except OSError:
            continue
    return max(stamps, default=0.0)


def journal_verdict() -> dict:
    """Последний прогон сценариев как АРТЕФАКТ: что гонялось, чем кончилось, когда."""
    files = sorted((PROJ / "tests" / ".journal").glob("scenarios-*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:5]:
        try:
            rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for row in reversed(rows[-5:]):
            try:
                entry = json.loads(row)
            except ValueError:
                continue
            if entry.get("scenario") == "__run__":
                entry["_path"] = str(path)
                return entry
    return {}


def scenarios_on_disk() -> set[str]:
    """Имена сценариев и карт по объявлениям. Без yaml-парсера: хук ходит системным питоном."""
    names: set[str] = set()
    for path in (PROJ / "tests" / "scenarios").glob("*.yaml"):
        for row in path.read_text(encoding="utf-8", errors="replace").splitlines():
            row = row.strip()
            if row.startswith("- scenario:"):
                names.add(row.split(":", 1)[1].strip())
            elif row.startswith("map:"):
                names.add(row.split(":", 1)[1].strip())
    return names


def map_untrustworthy(touched: list[str], radius: dict) -> str:
    """Причина, по которой картой НЕЛЬЗЯ выбирать прогон. Пусто — можно.

    Признак не время сборки (оно устаревает от любой правки), а ПОЛНОТА: карта, не знающая
    сегодняшних сценариев, спокойно выберет набор, в котором нужного нет, — и гейт пропустит
    откат, потому что «выбранное зелёное».
    """
    if not radius:
        return "карты радиуса нет вовсе"
    known = {str(n).split("#")[0] for names in radius.values() for n in names}
    missing = sorted(scenarios_on_disk() - known)
    if missing:
        return f"карта не знает сценариев: {', '.join(missing[:6])}" + (" …" if len(missing) > 6 else "")
    blind = [f for f in touched if f not in radius]
    if blind:
        return f"тронутых файлов нет в карте: {', '.join(blind[:4])}"
    return ""


def affected_scenarios(touched: list[str]) -> tuple[set[str], str]:
    """Сценарии, стоящие на тронутых ФАЙЛАХ, плюс вывод карты радиуса про слепые строки.

    По файлам, а не по строкам: `--affected` считает только незакоммиченное, и закоммиченная
    правка проскакивала бы мимо гейта. Пофайловый счёт — надмножество построчного: лишний
    сценарий прогнать не жалко, пропущенный стоит дефекта.
    """
    try:
        radius = json.loads((PROJ / "tests" / ".blast" / "radius.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set(), ""                          # карты нет — гейт не выдумывает, а молчит
    names: set[str] = set()
    for rel in touched:
        names |= set((radius.get(rel) or {}))
    try:
        out = subprocess.run([sys.executable, "scripts/guards/blast_radius.py", "--affected"],
                             cwd=str(PROJ), capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    return names, out


def verify_blindness() -> None:
    """Слепая зона может только сокращаться: новый непокрытый путь — это молчащая карта.

    Проверка дешёвая (разбор файлов, без прогона), поэтому идёт на каждое «сделано» с правкой
    сервера: иначе непокрытый код накапливается, а карта о нём молчит по построению.
    """
    try:
        done = subprocess.run([sys.executable, "scripts/guards/blast_radius.py", "--blind"],
                              cwd=str(PROJ), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return                                     # карты нет — гейт не выдумывает, а молчит
    # Два разных исхода, и путать их нельзя: «улики нет» — не обвинение. Сторож, который винит
    # за отсутствие карты, выключают в первый же день, и вместе с ним пропадает настоящая проверка.
    if done.returncode == 2:
        block("Гейт поставки: улики нет — карта радиуса не собрана, и проверить молчание нечем.\n\n"
              + done.stdout[-600:] + "\n\n    python3 scripts/guards/blast_radius.py --build")
    if done.returncode == 1:
        block("Гейт поставки: молчание карты ВЫРОСЛО — появились функции, которых не исполняет "
              "ни один сценарий.\n\n" + done.stdout[-1000:] +
              "\n\nЛибо объяви сценарий на новый путь, либо опусти потолок осознанно:\n"
              "    python3 scripts/guards/blast_radius.py --blind --bless")


def run_scenarios(names: set[str]) -> tuple[int, str]:
    """Запасной путь: гоним задетое сами. Улику пишет сам прогон — она и станет доказательством."""
    limit = int(os.environ.get("VPM_GATE_RUN_LIMIT") or 540)
    try:
        done = subprocess.run([sys.executable, "tests/scenarios/test_scenarios.py"],
                              cwd=str(PROJ), capture_output=True, text=True, timeout=limit,
                              env={**os.environ, "VPM_SCENARIO": ",".join(sorted(names))})
    except subprocess.TimeoutExpired:
        return 2, f"прогон не уложился в {limit} с и был снят"
    except (OSError, subprocess.SubprocessError) as exc:
        return 0, str(exc)                       # окружение не дало прогнать — не мешаем работать
    fails = [row for row in done.stdout.splitlines() if row.lstrip().startswith("✗")]
    return done.returncode, "\n".join(fails[:8]) or done.stdout[-900:]


def verify_behaviour(touched: list[str]) -> None:
    """Улика прогона обязана быть свежей, зелёной и покрывать задетое. Нет — гоним сами."""
    files = ", ".join(touched[:6]) + (" …" if len(touched) > 6 else "")
    try:
        radius = json.loads((PROJ / "tests" / ".blast" / "radius.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        radius = {}
    stale = map_untrustworthy(touched, radius)
    if stale:
        block(MSG_C_STALE.format(files=files, why=stale))
        return
    names, radius_out = affected_scenarios(touched)
    if not names:
        if "без единого сценария" in radius_out:
            block(MSG_C_BLIND.format(files=files, tail=radius_out.strip()[:900]))
        return                                    # правка вне измеряемой зоны либо карты нет

    verdict = journal_verdict()
    fresh = bool(verdict) and float(verdict.get("ts") or 0) > newest_code_change(touched)
    # Карта радиуса знает пути карты поимённо (`карта#путь1`), журнал пишет саму карту:
    # сравнивать надо по имени до `#`, иначе улика НИКОГДА не сойдётся и гейт гоняет прогон
    # на каждом ходу — так сторожа и выключают.
    covers = _bare(names) <= _bare(verdict.get("scenarios") or [])
    if fresh and covers and verdict.get("ok"):
        return                                    # улика есть, свежая, зелёная — молчим

    if fresh and covers and not verdict.get("ok"):
        block(MSG_C_RED.format(files=files, command=_command(names),
                               tail=f"журнал {verdict.get('_path')}: провалов {verdict.get('failed')}"))
        return

    code, tail = run_scenarios(names)             # улики нет/несвежая/не покрывает — проверяем сами
    if code == 2:
        block(MSG_C_SLOW.format(limit=os.environ.get("VPM_GATE_RUN_LIMIT") or 540,
                                command=_command(names)))
    elif code != 0:
        block(MSG_C_RED.format(files=files, command=_command(names), tail=tail))


def _bare(names) -> set[str]:
    return {str(n).split("#")[0] for n in names}


def _command(names: set[str]) -> str:
    return f"VPM_SCENARIO='{','.join(sorted(names))}' python3 tests/scenarios/test_scenarios.py"


def touched_today() -> bool:
    today = datetime.date.today()
    paths = [PROJ / r for r in RECORDS] + (list(MEM.glob("*.md")) if MEM.is_dir() else [])
    for p in paths:
        try:
            if datetime.date.fromtimestamp(p.stat().st_mtime) == today:
                return True
        except OSError:
            continue
    try:
        out = subprocess.run(
            ["git", "-C", str(PROJ), "log", "--since=midnight", "--oneline"],
            capture_output=True, text=True, timeout=5,
        )
        if out.stdout.strip():
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def server_code_touched() -> list[str]:
    """Файлы измеряемой зоны в незакоммиченной правке и в сегодняшних коммитах."""
    files: set[str] = set()
    for args in (["diff", "--name-only", "HEAD"], ["log", "--since=midnight", "--name-only", "--pretty="]):
        try:
            out = subprocess.run(["git", "-C", str(PROJ), *args], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        files |= {row.strip() for row in out.stdout.splitlines() if row.strip()}
    return sorted(f for f in files if f.endswith(".py") and f.startswith(MEASURED))


def block(reason: str) -> None:
    print(reason, file=sys.stderr)
    sys.exit(2)


def main() -> None:
    if os.environ.get("VPM_DELIVERY_GATE", "").lower() in ("off", "0", "false"):
        sys.exit(0)
    if not str(Path.cwd()).startswith(str(PROJ)):
        sys.exit(0)

    data = json.loads(sys.stdin.read())
    if data.get("stop_hook_active"):  # уже блокировали в этой цепочке — второй раз не мешаем
        sys.exit(0)

    transcript = ""
    tp = data.get("transcript_path")
    if tp:
        try:
            transcript = Path(os.path.expanduser(tp)).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass

    last = data.get("last_assistant_message") or transcript[-4000:]

    # A. Заявление о зелёном сверяется ЗАПУСКОМ, а не поиском слова в стенограмме
    for sentence in re.split(r"[.!?\n]", last):
        m = CLAIM.search(sentence)
        if m and not MODAL.search(sentence):
            red = cheap_failures()
            if red:
                block(MSG_A.format(claim=m.group(0), failed=", ".join(name for name, _ in red),
                                   tail="\n".join(tail for _, tail in red)[:900]))
            break

    # B. Сложная сессия без записи решение→факт
    edits = len(EDIT_CALL.findall(transcript))
    if edits >= COMPLEX_EDITS and not touched_today():
        block(MSG_B.format(n=edits))

    # C. Тронут код сервера — гоним задетое сами
    touched = server_code_touched()
    if touched:
        verify_behaviour(touched)
        verify_blindness()

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
