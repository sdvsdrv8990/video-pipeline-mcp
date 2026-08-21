#!/usr/bin/env python3
"""comment_guard.py — сторож текста в коде: правило + храповик долга.

## Назначение
Комментарий и докстринг объясняют НЕОЧЕВИДНОЕ поведение кода, терсово (правило владельца
2026-07-05). Разбор задачи, история решений, дерево каталогов, формат YAML — в
`docs/roadmap/_sessions.md` и commit: дубль декларации в прозе гниёт молча.

## Границы
Единственная копия правила; хук `~/.claude/hooks/vpm-comment-guard.py` — шим сюда.
Режимы: `--hook` (PostToolUse: предупреждает), `--scan` (отчёт), `--check` (храповик:
превышение = exit 1), `--bless` (переписать потолок после прополки).
Границы текста даёт `ast`+`tokenize`, цели — `git ls-files`: свой построчный разбор путал
кавычки константы с докстрингом, а фиксированный список целей не видел новый пакет.
В не-Python цели (декларации, CI, ignore) комментарием считается только строка целиком.
"""

import argparse
import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).with_name("comment_guard_baseline.txt")

MODULE_DOC_MAX = 15          # шапка модуля: роль + неочевидное, а не пересказ подсистемы
FUNC_DOC_MAX = 12            # докстринг функции: что делает + почему так, без эссе
COMMENT_RUN_MAX = 5          # подряд идущих строк `#`
SECTIONS_MAX = 2             # терсовый `## Назначение` — конвенция; три раздела — уже документ
DECL_LINES_MAX = 3           # `ключ: значение` прозой — это пересказ декларации, а не пояснение

NARRATIVE = [
    (r"(?i)^\s*#{2,}\s*(4\s+уровня|уровни\s+анализа|поток\s+данных|долгосрочный|порядок\s+полей|"
     r"что\s+было|история)", "раздел-разбор задачи в шапке — его место в журнале сессий"),
    (r"(?i)прежде\s+было|раньше\s+(?:было|тут|здесь)|было\s*:", "история «прежде было» — её место в commit"),
    (r"(?i)4\s+уровня|уровня\s+анализа|5\s+вопросов|что\s*/\s*почему\s*/\s*сценарий",
     "разбор задачи (уровни/вопросы) — их место в журнале сессий"),
    (r"(?i)^\s*#?\s*поток\s+данных|^\s*#?\s*долгосрочный|^\s*#?\s*порядок\s+полей",
     "шапка-оглавление подсистемы вместо роли модуля"),
    (r"(?i)TODO\s*\(\s*сесси|в\s+следующей\s+сессии", "план работ в коде — он живёт в журнале прохода"),
]
DUPLICATION = [
    (r"^\s*```", "блок кода в докстринге (формат/пример) — дубль декларации"),
    (r"[├└│]──", "дерево каталогов в докстринге — источник правды `config/templates/`"),
]
DECL_LINE = re.compile(r"^\s*[-*•]?\s*`?[\w.\[\]]+`?\s*:\s+\S")

# Координата задачи: указатель в реестр находок, сессию прохода, фазу плана или мутацию.
# Ловим по форме, а не по словарю: реестр растёт, а форма постоянна. Отсечки слева отбивают
# хвост адреса ячейки книги, справа — букву внутри слова (имя директивы линтера, формулы, цвета).
TASK_TAG = re.compile(r"(?<![\w:!.$])(?:[DFGM]\d{1,3}|S1\d(?:-[a-z])?|S2\d|Ф\d)(?![\w])")
# Имя правила линтера выглядит координатой ровно так же; отличает его только то, чьё оно.
LINTERS = ("ruff", "pydocstyle", "flake8", "pylint", "bandit", "mypy", "pytest", "semgrep")


def _task_tag(line: str) -> str | None:
    """Координата задачи в строке текста, либо None. Имя правила чужого линтера — не она."""
    for m in TASK_TAG.finditer(line):
        before = line[:m.start()].rstrip().rsplit(" ", 1)[-1].strip("`(«\"'").lower()
        if before not in LINTERS:
            return m.group()
    return None


def _spans(text: str) -> tuple[list[tuple[int, int, bool]], list[tuple[int, str, bool]]]:
    """Границы текста: докстринги (строка, длина, это_шапка) и комментарии (строка, текст, отдельный)."""
    tree = ast.parse(text)
    head = tree.body[0] if tree.body else None
    docs = {}
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            # У `IfExp`/`Lambda` те же имена полей несут ОДИН узел, а не список.
            for stmt in [x for x in [getattr(node, field, None)] if isinstance(x, list) for x in x]:
                # Текст — ЛЮБАЯ голая строка-выражение, а не только докстринг по версии Python:
                # `f"""…"""` первой строкой тела докстрингом не считается и прошло бы насквозь.
                if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, (ast.Constant, ast.JoinedStr)):
                    continue
                if isinstance(stmt.value, ast.Constant) and not isinstance(stmt.value.value, (str, bytes)):
                    continue
                docs[stmt.lineno] = ((stmt.end_lineno or stmt.lineno) - stmt.lineno + 1, stmt is head)
    docs = [(n, length, is_head) for n, (length, is_head) in docs.items()]
    comments = [(t.start[0], t.string, t.line.strip().startswith("#"))
                for t in tokenize.generate_tokens(io.StringIO(text).readline)
                if t.type == tokenize.COMMENT]
    return sorted(docs), comments


def _tag_note(path: Path | str, n: int, tag: str) -> str:
    return (f"{path}:{n} — координата задачи `{tag}` в тексте: указывает в реестр находок, "
            f"а не объясняет код — её место в commit")


def _review_text(path: Path | str, text: str) -> list[str]:
    """Не-Python цель: комментарий = строка, начинающаяся с `#`.

    Хвостовой `#` после значения не разбираем: без парсера языка он неотличим от решётки
    внутри кавычек, а ложное замечание храповик заморозил бы как принятый долг.
    """
    notes: list[str] = []
    seen: set[str] = set()
    for n, line in enumerate(text.split("\n"), 1):
        if not line.lstrip().startswith("#"):
            continue
        for pattern, why in NARRATIVE:
            if re.search(pattern, line) and why not in seen:
                seen.add(why)
                notes.append(f"{path}:{n} — {why}")
                break
        if (tag := _task_tag(line)) is not None:
            notes.append(_tag_note(path, n, tag))
    return notes


def review(path: Path | str, text: str) -> list[str]:
    """Замечания по одному файлу. Пусто = чисто."""
    if not str(path).endswith(".py"):
        return _review_text(path, text)
    notes: list[str] = []
    seen: set[str] = set()
    lines = text.split("\n")
    try:
        docs, comments = _spans(text)
    except (SyntaxError, ValueError, tokenize.TokenError) as e:
        # Неразбираемый файл = слепое пятно: молчание тут читалось бы как «чисто».
        return [f"{path}:1 — файл не разбирается ({type(e).__name__}): сторож на нём слеп"]

    prose: dict[int, str] = {}
    for start, length, is_module in docs:
        body = lines[start - 1:start - 1 + length]
        prose.update({start + k: ln for k, ln in enumerate(body)})
        limit = MODULE_DOC_MAX if is_module else FUNC_DOC_MAX
        if length > limit:
            kind = "шапка модуля" if is_module else "докстринг"
            notes.append(f"{path}:{start} — {kind} на {length} строк (предел {limit}): "
                         f"оставь роль и неочевидное, остальное — в журнал и commit")
        sections = sum(1 for ln in body if re.match(r"\s*#{2,}\s\S", ln))
        if sections > SECTIONS_MAX:
            notes.append(f"{path}:{start} — {sections} разделов в одном докстринге: это документ, "
                         f"а не пояснение к коду (предел {SECTIONS_MAX})")
        decl = sum(1 for ln in body if DECL_LINE.match(ln))
        if decl > DECL_LINES_MAX:
            notes.append(f"{path}:{start} — {decl} строк вида «ключ: значение»: пересказ декларации "
                         f"прозой (предел {DECL_LINES_MAX}) — источник правды в `config/`, не здесь")

    alone = sorted(n for n, _, standalone in comments if standalone)
    run_start = None
    for i, n in enumerate(alone):
        if run_start is None or n != alone[i - 1] + 1:
            run_start = n
        if n - run_start + 1 == COMMENT_RUN_MAX + 1:
            notes.append(f"{path}:{run_start} — {COMMENT_RUN_MAX}+ строк комментария подряд: "
                         f"это абзац рассуждения, а не пояснение к строке кода")

    # Маркеры ищем ТОЛЬКО в тексте (докстринг/комментарий): таблица шаблонов этого же сторожа —
    # код, и построчный разбор принимал её за замечание.
    for n, line in sorted(list(prose.items()) + [(n, s) for n, s, _ in comments]):
        for pattern, why in NARRATIVE + DUPLICATION:
            if re.search(pattern, line) and why not in seen:
                # Одно дерево каталогов — одно замечание, а не 29 одинаковых: сторож,
                # который повторяется, читается как шум и его перестают читать.
                seen.add(why)
                notes.append(f"{path}:{n} — {why}")
                break
        # Координату называем НА КАЖДОМ вхождении, а не один раз на файл: каждая снимается
        # отдельно, и схлопнутый счётчик перестал бы убывать по мере прополки.
        if (tag := _task_tag(line)) is not None:
            notes.append(_tag_note(path, n, tag))
    return notes


def _tracked() -> list[Path]:
    """Цели под контролем git: новый пакет и новая декларация попадают в замер сами."""
    globs = ["*.py", "*.yaml", "*.yml", "*.toml", "*.sh", ".gitignore", ".env.example"]
    try:
        out = subprocess.run(["git", "-C", str(PROJECT), "ls-files", "-z", *globs],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        raise SystemExit(
            f"comment_guard: git не ответил ({type(e).__name__}) — замер не состоялся") from e
    return [PROJECT / p for p in out.split("\0") if p]


def _rel(p: Path) -> str:
    """Путь для отчёта: вне репозитория relative_to роняет сырой ValueError."""
    try:
        return str(p.relative_to(PROJECT))
    except ValueError:
        return str(p)


def collect(targets: list[str] | None = None) -> dict[str, list[str]]:
    """{путь: замечания}. Недостижимая цель и пустой замер = отказ, не тишина."""
    if targets:
        files = []
        for t in targets:
            p = Path(t) if Path(t).is_absolute() else (PROJECT / t)
            if not p.exists():
                raise SystemExit(f"comment_guard: цель не найдена — {p}")
            files += [p] if p.is_file() else [f for f in p.rglob("*.py") if "__pycache__" not in str(f)]
    else:
        files = _tracked()
    out = {_rel(f): review(_rel(f), f.read_text(encoding="utf-8", errors="replace"))
           for f in sorted(set(files))}
    if not out:
        raise SystemExit(f"comment_guard: не найдено ни одного .py в {targets or 'git ls-files'} — "
                         f"проверка не состоялась")
    return out


def _load_baseline() -> dict[str, int]:
    if not BASELINE.exists():
        raise SystemExit(f"comment_guard: нет потолка {BASELINE} — прогони --bless")
    ceiling = {}
    for ln in BASELINE.read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.startswith("#"):
            path, _, count = ln.rpartition(" ")
            ceiling[path.strip()] = int(count)
    return ceiling


def cmd_scan(targets: list[str]) -> int:
    found = collect(targets)
    total = 0
    for notes in found.values():
        total += len(notes)
        for note in notes:
            print(note)
    print(f"\nВсего замечаний: {total} в {len(found)} файлах")
    return 0


def cmd_bless() -> int:
    found = collect()
    total = sum(len(n) for n in found.values())
    body = "".join(f"{p} {len(n)}\n" for p, n in sorted(found.items()) if n)
    BASELINE.write_text(
        "# Потолок замечаний сторожа текста в коде (scripts/comment_guard.py --check).\n"
        "# Вниз можно, вверх — нет: превышение красит джобу comment-guard в CI.\n"
        f"# После прополки: python3 scripts/comment_guard.py --bless. Сейчас всего: {total}.\n"
        + body, encoding="utf-8")
    print(f"Потолок переписан: {total} замечаний в {sum(1 for n in found.values() if n)} файлах → {BASELINE}")
    return 0


def cmd_check() -> int:
    found = collect()
    ceiling = _load_baseline()
    total, cap = sum(len(n) for n in found.values()), sum(ceiling.values())
    grown = [(p, ceiling.get(p, 0), n) for p, n in sorted(found.items()) if len(n) > ceiling.get(p, 0)]
    for path, was, notes in grown:
        print(f"✗ {path}: было ≤{was}, стало {len(notes)} — добавилось {len(notes) - was}")
        for note in notes:
            print(f"    {note}")
    dropped = sorted(p for p, n in found.items() if len(n) < ceiling.get(p, 0))
    stale = sorted(set(ceiling) - set(found))
    if grown:
        print(f"\nХраповик: {total} замечаний при потолке {cap}. Мусор ИИ в коде растёт — "
              f"почисти файлы выше или объясни в ревью, почему потолок поднимается "
              f"(scripts/comment_guard.py --bless).")
        return 1
    if dropped or stale:
        print(f"↓ прополото: {', '.join(dropped + stale) or '—'} — опусти потолок: --bless")
    print(f"Храповик: {total} замечаний при потолке {cap} в {len(found)} файлах — не выше.")
    return 0


def cmd_hook() -> int:
    """Событие PostToolUse: предупреждение уходит в контекст, запись не отменяется."""
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    path = Path(str((event.get("tool_input") or {}).get("file_path") or ""))
    if path.suffix != ".py" or PROJECT not in path.parents:
        return 0
    # Смотрим НАПИСАННОЕ на диске: у Edit в событии только фрагмент, а правило про файл целиком.
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    notes = review(_rel(path), text)
    if not notes:
        return 0
    head = "\n".join(f"  • {n}" for n in notes[:6])
    more = f"\n  … и ещё {len(notes) - 6}" if len(notes) > 6 else ""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            f"⚠️ Текст в коде (правило владельца: комментарий объясняет НЕОЧЕВИДНОЕ поведение кода, "
            f"терсово; разбор задачи и история — в `_sessions.md` + commit):\n{head}{more}\n"
            f"Почини сейчас, пока файл в руках. Правило и примеры — скилы `project-conventions` "
            f"(стиль) и `code-quality` (ось 7a)."),
    }}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--scan", action="store_true", help="отчёт по целям (или переданным путям)")
    mode.add_argument("--check", action="store_true", help="храповик против потолка; превышение = exit 1")
    mode.add_argument("--bless", action="store_true", help="переписать потолок текущим замером")
    mode.add_argument("--hook", action="store_true", help="событие PostToolUse со stdin")
    ap.add_argument("paths", nargs="*", help="цели для --scan")
    a = ap.parse_args()
    if a.check:
        return cmd_check()
    if a.bless:
        return cmd_bless()
    if a.scan:
        return cmd_scan(a.paths)
    return cmd_hook()


if __name__ == "__main__":
    sys.exit(main())
