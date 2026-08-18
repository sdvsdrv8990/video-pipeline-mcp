#!/usr/bin/env python3
"""comment_guard.py — сторож текста в коде: правило + храповик долга.

## Назначение
Комментарий и докстринг объясняют НЕОЧЕВИДНОЕ поведение кода, терсово (правило владельца
2026-07-05). Разбор задачи, история решений, дерево каталогов, формат YAML — в
`docs/roadmap/_sessions.md` и commit: дубль декларации в прозе гниёт молча.

## Границы
Единственная копия правила; хук `~/.claude/hooks/vpm-comment-guard.py` — шим сюда.
Режимы: `--hook` (PostToolUse: предупреждает, не блокирует), `--scan` (отчёт),
`--check` (храповик против baseline: превышение = exit 1), `--bless` (переписать baseline
после прополки — потолок хранится ровно в одном файле).
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).with_name("comment_guard_baseline.txt")
TARGETS = ["core", "tools", "server.py", "tests", "scripts"]

MODULE_DOC_MAX = 15          # шапка модуля: роль + неочевидное, а не пересказ подсистемы
FUNC_DOC_MAX = 12            # докстринг функции: что делает + почему так, без эссе
COMMENT_RUN_MAX = 5          # подряд идущих строк `#`
SECTIONS_MAX = 2             # терсовый `## Назначение` — конвенция; три раздела — уже документ

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


def _module_and_func_docs(text: str) -> list[tuple[int, int, bool]]:
    """Докстринги файла: (первая_строка, длина, это_шапка_модуля)."""
    out, lines = [], text.split("\n")
    i, first_code_seen = 0, False
    while i < len(lines):
        s = lines[i].strip()
        # Префикс `r`/`f`/`b` перед кавычками — обычный докстринг: без него одна буква снимала правило.
        opener = re.match(r"[rRbBuUfF]{0,2}(\"\"\"|''')", s)
        quote = opener.group(1) if opener else ""
        if quote and not (len(s) - opener.start(1) > 3 and s.endswith(quote)):
            start, length = i + 1, 1
            i += 1
            while i < len(lines) and not lines[i].strip().endswith(quote):
                length += 1
                i += 1
            out.append((start, length + 1, not first_code_seen))
        elif s and not s.startswith("#") and not quote:
            first_code_seen = True
        i += 1
    return out


def review(path: Path | str, text: str) -> list[str]:
    """Замечания по одному файлу. Пусто = чисто."""
    notes: list[str] = []
    seen: set[str] = set()
    lines = text.split("\n")
    for start, length, is_module in _module_and_func_docs(text):
        limit = MODULE_DOC_MAX if is_module else FUNC_DOC_MAX
        if length > limit:
            kind = "шапка модуля" if is_module else "докстринг"
            notes.append(f"{path}:{start} — {kind} на {length} строк (предел {limit}): "
                         f"оставь роль и неочевидное, остальное — в журнал и commit")
        sections = sum(1 for ln in lines[start - 1:start - 1 + length]
                       if re.match(r"\s*#{2,}\s\S", ln))
        if sections > SECTIONS_MAX:
            notes.append(f"{path}:{start} — {sections} разделов в одном докстринге: это документ, "
                         f"а не пояснение к коду (предел {SECTIONS_MAX})")
    prose = {n for start, length, _ in _module_and_func_docs(text)
             for n in range(start, start + length)}
    run = 0
    for n, line in enumerate(lines, 1):
        if line.strip().startswith("#"):
            run += 1
            if run == COMMENT_RUN_MAX + 1:
                notes.append(f"{path}:{n - run + 1} — {COMMENT_RUN_MAX}+ строк комментария подряд: "
                             f"это абзац рассуждения, а не пояснение к строке кода")
        else:
            run = 0
        # Правило про ТЕКСТ: маркеры ищем в комментариях и докстрингах, не в коде — иначе
        # таблица шаблонов этого же сторожа ловит сама себя.
        if not (line.strip().startswith("#") or n in prose):
            continue
        for pattern, why in NARRATIVE + DUPLICATION:
            if re.search(pattern, line) and why not in seen:
                # Одно дерево каталогов — одно замечание, а не 29 одинаковых строк: сторож,
                # который повторяется, читается как шум и его перестают читать.
                seen.add(why)
                notes.append(f"{path}:{n} — {why}")
                break
    return notes


def collect(targets: list[str]) -> dict[str, list[str]]:
    """{путь относительно корня: замечания} по целям. Недостижимая цель = отказ, не тишина."""
    out: dict[str, list[str]] = {}
    for t in targets:
        p = (PROJECT / t) if not Path(t).is_absolute() else Path(t)
        if not p.exists():
            raise SystemExit(f"comment_guard: цель не найдена — {p}")
        files = [p] if p.is_file() else [f for f in p.rglob("*.py") if "__pycache__" not in str(f)]
        for f in sorted(files):
            rel = str(f.relative_to(PROJECT))
            out[rel] = review(rel, f.read_text(encoding="utf-8", errors="replace"))
    if not out:
        raise SystemExit(f"comment_guard: не найдено ни одного .py в {targets} — проверка не состоялась")
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
    found = collect(targets or TARGETS)
    total = 0
    for notes in found.values():
        total += len(notes)
        for note in notes:
            print(note)
    print(f"\nВсего замечаний: {total} в {len(found)} файлах")
    return 0


def cmd_bless() -> int:
    found = collect(TARGETS)
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
    found = collect(TARGETS)
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
    notes = review(path, text)
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
