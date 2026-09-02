#!/usr/bin/env python3
"""vpm-skill-guard.py — сторож скилов: знание и инструкция да, статус нет.

## Назначение
Скилл читают как истину, поэтому счётное утверждение о нашей базе («столько-то инструментов»,
«столько-то строк») гниёт молча и врёт следующей сессии. Правило: число удаляем, способ
померить оставляем. Статус находки и снимок замера — в commit и память, не в скилл.

## Границы
Режимы: `--hook` (PostToolUse, предупреждает), `--scan` (отчёт по библиотеке), `--check`
(exit 1 при находках). Точность важнее полноты: ложное срабатывание учит игнорировать сторожа,
поэтому пороги, версии, внешние цитаты и явно помеченное строкой-исключением не трогаем.
"""

import argparse
import ast
import json
import re
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
PROJECT = Path(__file__).resolve().parents[2]
SKILLS = PROJECT / ".claude" / "skills"
# Явное исключение в той же строке: `<!-- снимок ок: цитата чужого замера -->`.
ESCAPE = re.compile(r"<!--\s*снимок ок:")

# Русские и английские основы: CLAUDE.md и часть скилов написаны по-английски, и словарь
# только из русских основ давал бы покрытие, которое ничего не видит.
COUNTABLE = (r"(?:строк|строки|инструмент|групп|кодов|скил|файл|правил|замечани|функци|классов|пакет"
             r"|skills?|tools?|files?|lines?|rules?|checks?|findings?|packages?|groups?|scenarios?)")
RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("счётное утверждение о нашей базе",
     re.compile(rf"(?<![≤<>~.\d])\b\d{{2,}}\s*(?:\*\*\s*)?{COUNTABLE}\w*", re.I),
     "число устареет молча — оставь способ померить, а не результат"),
    ("снимок замера",
     re.compile(r"(?i)\b(?:замер|итого|всего|осталось|нашлось)\b[^.\n]{0,40}?\b\d{2,}\b"),
     "результат прогона — в commit и память; в скиле живёт команда, а не её вывод"),
    ("статус находки",
     re.compile(r"(?i)\b[DFGM]\d{1,3}\b[^.\n]{0,24}?\b(?:закрыт|снят|починен|исправлен)\w*"
                r"|\b(?:закрыт|снят|починен|исправлен)\w*[^.\n]{0,24}?\b[DFGM]\d{1,3}\b"),
     "состояние реестра находок меняется без правки скила — статусу тут не место"),
]
# Пороги и пределы — это ПРАВИЛО, а не снимок: их сторож пропускает.
LIMIT = re.compile(r"(?i)≤|>=|<=|не\s+более|не\s+менее|предел|порог|лимит|максимум|минимум")



# Перечень имён через разделитель: `a` · `b` · `c`.
RUN = re.compile(r"(?:`[A-Za-z_][\w./-]{2,}`\s*(?:[/·,]|\bи\b)\s*){2,}`[A-Za-z_][\w./-]{2,}`")
NAME = re.compile(r"`([A-Za-z_][\w./-]{2,})`")
# Со скольких имён перечня, найденных в самом проекте, он перестаёт быть иллюстрацией.
OURS_ENOUGH = 3


def _vocabulary() -> set[str]:
    """Имена, которые проект объявляет САМ: ключи и значения деклараций, функции, каталоги тестов.

    Список внешних инструментов (`bandit`, `gitleaks`) в проекте не объявлен и потому под правило не
    попадает — гниёт не всякий перечень, а тот, у которого есть второй источник в репозитории.
    """
    names: set[str] = set()
    if not PROJECT.is_dir():
        return names
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        for path in (PROJECT / "config").glob("*.yaml"):
            def walk(node):
                if isinstance(node, dict):
                    for key, value in node.items():
                        names.add(str(key))
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)
                elif isinstance(node, str):
                    names.add(node)
            try:
                walk(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
            except Exception:
                continue
    for base in ("core", "tools", "scripts"):
        for source in (PROJECT / base).rglob("*.py"):
            if "__pycache__" in str(source):
                continue
            try:
                tree = ast.parse(source.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
    tests = PROJECT / "tests"
    if tests.is_dir():
        names |= {d.name for d in tests.iterdir() if d.is_dir()}
    return names


VOCABULARY = _vocabulary()


def enumerates_our_base(line: str) -> str:
    """Перечень нашей лексики в скиле: второй источник того же набора, и он разойдётся молча.

    Пусто — перечень не наш (внешние инструменты, встроенные функции языка, чужие примеры).
    """
    match = RUN.search(line)
    if not match:
        return ""
    listed = NAME.findall(match.group())
    ours = [n for n in listed if n in VOCABULARY or n.split("/")[-1].removesuffix(".py") in VOCABULARY]
    if len(ours) < OURS_ENOUGH:
        return ""
    return match.group()[:60]


def review(path: str, text: str) -> list[str]:
    """Замечания по одному файлу скила. Пусто = чисто."""
    notes: list[str] = []
    body = text.split("\n---\n", 1)[-1] if text.startswith("---\n") else text
    shift = text.count("\n", 0, len(text) - len(body))
    fenced = False
    for n, line in enumerate(body.split("\n"), shift + 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        # Внутри блока кода живут ПРИМЕРЫ, а не утверждения о нашей базе: «Write 500 lines of code»
        # в иллюстрации — не снимок замера. Ловить их значит учить игнорировать сторожа.
        if fenced or ESCAPE.search(line) or LIMIT.search(line):
            continue
        found = enumerates_our_base(line)
        if found:
            notes.append(f"{path}:{n} — перечень нашей базы («{found}»): у него есть второй источник "
                         "в репозитории, и он разойдётся молча — оставь способ померить, а не результат")
            continue
        for name, pattern, why in RULES:
            m = pattern.search(line)
            if m:
                notes.append(f"{path}:{n} — {name} («{m.group().strip()[:40]}»): {why}")
                break
    return notes


def label(f: Path) -> str:
    """Имя в отчёте: внутри библиотеки — относительное, снаружи (CLAUDE.md) — как есть."""
    try:
        return str(f.relative_to(SKILLS))
    except ValueError:
        return str(f)


def collect() -> dict[str, list[str]]:
    """{путь: замечания} по всей библиотеке. Пустой замер = отказ, не тишина."""
    # CLAUDE.md загружается КАЖДУЮ сессию — устаревшее счётное утверждение там дороже, чем в скиле,
    # а до сих пор он не проверялся вовсе (там нашлись «13 skills» при 17 на диске).
    files = sorted(SKILLS.rglob("*.md")) + [f for f in (Path.home() / ".claude" / "CLAUDE.md",
                                                        Path.cwd() / "CLAUDE.md") if f.is_file()]
    if not files:
        raise SystemExit(f"vpm-skill-guard: ни одного .md в {SKILLS} — проверка не состоялась")
    return {label(f): review(label(f), f.read_text(encoding="utf-8")) for f in files}


def cmd_scan(check: bool) -> int:
    found = collect()
    total = 0
    for notes in found.values():
        for note in notes:
            print(note)
        total += len(notes)
    print(f"\nВсего замечаний: {total} в {len(found)} файлах скилов")
    return 1 if (check and total) else 0


def cmd_hook() -> int:
    """PostToolUse: предупреждение уходит в контекст, запись не отменяется."""
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    path = Path(str((event.get("tool_input") or {}).get("file_path") or ""))
    if path.suffix != ".md" or SKILLS not in path.parents:
        return 0
    try:
        notes = review(label(path), path.read_text(encoding="utf-8"))
    except OSError:
        return 0
    if not notes:
        return 0
    head = "\n".join(f"  • {n}" for n in notes[:6])
    more = f"\n  … и ещё {len(notes) - 6}" if len(notes) > 6 else ""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            f"⚠️ Статус в скиле (скилл несёт ЗНАНИЕ и ИНСТРУКЦИЮ; счётные величины и статусы "
            f"устаревают молча и врут следующей сессии):\n{head}{more}\n"
            f"Замени число на способ его померить. Осознанная цитата чужого замера помечается "
            f"`<!-- снимок ок: причина -->` в той же строке. Правило — скилл `skill-curator`."),
    }}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--scan", action="store_true", help="отчёт по библиотеке скилов")
    mode.add_argument("--check", action="store_true", help="то же, но находки = exit 1")
    mode.add_argument("--hook", action="store_true", help="событие PostToolUse со stdin")
    a = ap.parse_args()
    if a.scan or a.check:
        return cmd_scan(a.check)
    return cmd_hook()


if __name__ == "__main__":
    sys.exit(main())
