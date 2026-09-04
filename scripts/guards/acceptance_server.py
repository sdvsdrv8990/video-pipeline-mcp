#!/usr/bin/env python3
"""scripts/guards/acceptance_server.py — ПРИЁМКА правки ИИ в дереве сервера (Python).

Те же условия, что у приёмки студии, прочитанные для бэкенда:
  П5 файл объявляет СВОЮ зону — шапка модуля называет его самого; без неё зоны ответственности
     у файла нет вовсе, и код ложится в первый подвернувшийся;
  П6 файл лежит в объявленном каталоге (`structure_server.yaml`), а не куда попало;
  П7 имена не из словаря «ни о чём», звёздный импорт и стирающий алиас названы поимённо;
  П3а тронутое внутри зоны задачи; П11 явные ошибки и мёртвое — вердикт `ruff`, а не свой разбор.

Механизмы берём готовыми: этажность судит import-linter, ошибки — `ruff`, текст в коде —
`comment_guard`. Здесь только то, чего не судит ни один из них, и свод их вердиктов в одну приёмку.
"""
import argparse
import ast
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _acceptance as общее                                                # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STRUCTURE = Path(__file__).resolve().parent / "structure_server.yaml"
SKIP = ("tests", ".venv", "workspace", "__pycache__", ".git")


def declared(path: Path = STRUCTURE) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def sources(root: Path = ROOT) -> list[str]:
    """Файлы дерева сервера под git. Наборы не судятся здесь: у них свой каталог зон."""
    done = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "*.py"],
                          capture_output=True, text=True)
    return sorted(p for p in done.stdout.split("\0")
                  if p and not any(часть in SKIP for часть in Path(p).parts))


def _role(файл: str, каталоги: dict) -> tuple[str | None, dict | None]:
    каталог = str(Path(файл).parent).replace("\\", "/")
    for шаблон, роль in каталоги.items():
        if каталог == шаблон or fnmatch.fnmatch(каталог, шаблон):
            return шаблон, роль
    return None, None


def kinds_of(text: str) -> set[str]:
    """Роды верхнего уровня — из настоящего разбора, а не по виду строки."""
    try:
        дерево = ast.parse(text)
    except SyntaxError:
        return {"неразбираемое"}
    роды = set()
    for узел in дерево.body:
        if isinstance(узел, ast.ClassDef):
            роды.add("класс")
        elif isinstance(узел, ast.FunctionDef | ast.AsyncFunctionDef):
            роды.add("тест" if узел.name.startswith("test_") else "функция")
        elif isinstance(узел, ast.Assign | ast.AnnAssign):
            роды.add("константа")
        elif isinstance(узел, ast.If) and ast.dump(узел.test).count("__main__"):
            # Скриптом файл делает ТОЧКА ВХОДА, а не любое условие верхнего уровня: `if
            # TYPE_CHECKING` и разбор версии — не запуск, и обвинять их значило бы врать.
            роды.add("скрипт")
    return роды


def place(root: Path = ROOT, **_) -> list[str]:
    """П6: файл лежит в объявленном каталоге, и род, который он определяет, каталогу разрешён."""
    объявлено = declared().get("каталоги")
    if not объявлено:
        return []
    notes = []
    for файл in sources(root):
        шаблон, роль = _role(файл, объявлено)
        if роль is None:
            notes.append(f"{файл} — каталог не объявлен в structure_server.yaml: у файла нет зоны "
                         f"ответственности, и правило «что здесь можно» к нему не применяется")
            continue
        текст = (root / файл).read_text(encoding="utf-8", errors="replace")
        for род in sorted(kinds_of(текст) - set(роль.get("можно", []))):
            notes.append(f"{файл} — здесь определён род «{род}», а зона каталога {шаблон!r} другая: "
                         f"{роль.get('роль')}")
    return notes


def header(root: Path = ROOT, **_) -> list[str]:
    """П5: шапка модуля называет САМ файл — иначе объявленной зоны ответственности у него нет."""
    объявлено = declared().get("каталоги")
    if not объявлено:
        return []
    notes = []
    for файл in sources(root):
        _, роль = _role(файл, объявлено)
        if not (роль or {}).get("нужна_шапка"):
            continue
        try:
            doc = ast.get_docstring(ast.parse((root / файл).read_text(encoding="utf-8",
                                                                     errors="replace")))
        except SyntaxError:
            continue                       # синтаксис судит ruff: второй вердикт о том же не нужен
        первая = (doc or "").strip().splitlines()[0] if doc else ""
        # У пакета зону объявляет имя ПАКЕТА, а не `__init__`: требовать в шапке имя файла значило
        # бы обвинять каждый `__init__.py` в дереве и выключить проверку в первый же день.
        своё = {Path(файл).name, Path(файл).stem, str(Path(файл).parent), Path(файл).parent.name}
        if not doc or not any(имя and имя in первая for имя in своё):
            notes.append(f"{файл} — шапка модуля не называет его самого: зона ответственности "
                         f"не объявлена, и следующий код ляжет сюда «потому что файл открыт»")
    return notes


def naming(root: Path = ROOT, **_) -> list[str]:
    """П7: имя говорит, что внутри; звёздный импорт и стирающий алиас — потеря имени на входе."""
    объявлено = declared().get("имена")
    if not объявлено:
        return []
    запрещённые = {имя.lower() for имя in объявлено.get("запрещённые", [])}
    notes = []
    for файл in sources(root):
        основа = Path(файл).stem
        if основа.lower() in запрещённые:
            notes.append(f"{файл} — имя {основа!r} не говорит ни о чём: в такой файл сваливают "
                         f"всё подряд, и зона ответственности исчезает первой")
        try:
            дерево = ast.parse((root / файл).read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for узел in ast.walk(дерево):
            if isinstance(узел, ast.ImportFrom) and any(и.name == "*" for и in узел.names):
                notes.append(f"{файл}:{узел.lineno} — звёздный импорт из {узел.module}: что именно "
                             f"пришло, не видно ни человеку, ни поиску")
            if isinstance(узел, ast.Import | ast.ImportFrom):
                notes += [f"{файл}:{узел.lineno} — {и.name} переименован в {и.asname!r}: имя стёрто "
                          f"на входе" for и in узел.names if и.asname and len(и.asname) == 1]
        notes += [f"{файл} — {узел.name!r} не говорит ни о чём" for узел in дерево.body
                  if getattr(узел, "name", "") and узел.name.lower() in запрещённые]
    return notes


def errors(root: Path = ROOT, zones: tuple[str, ...] = (), **_) -> list[str]:
    """П11/П3б: явные ошибки и мёртвое — вердикт `ruff`. Нет его — «улики нет», а не «чисто»."""
    цели = [z for z in zones if z.endswith(".py")] or ["."]
    done = subprocess.run([sys.executable, "-m", "ruff", "check", "--output-format", "concise",
                           *цели], cwd=root, capture_output=True, text=True)
    if done.returncode not in (0, 1):
        return [f"ruff недоступен ({done.stderr.strip()[:80]}) — улики нет, и это не «чисто»"]
    # Вердиктом считается только строка с координатой: «All checks passed!» — это отчёт о чистоте,
    # и принять его за находку значит краснеть ровно тогда, когда всё хорошо.
    return [f"{строка} — вердикт ruff" for строка in done.stdout.splitlines()
            if re.match(r"\S+:\d+:\d+:", строка.strip())]


CHECKS = (("П5 файл объявляет свою зону", header),
          ("П6 структура дерева", place),
          ("П7 имена говорят сами за себя", naming),
          ("П11 явные ошибки и мёртвое (ruff)", errors),
          ("П3а вышел за рамки задачи", общее.outside))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--суд", action="store_true", help="приёмка правки в дереве сервера")
    parser.add_argument("--зона", action="append", default=[], help="glob разрешённой зоны задачи")
    parser.add_argument("--совет", action="store_true", help="сценарии приёмки по роду улики")
    parser.add_argument("--улика", action="append", default=[], help="род улики")
    parser.add_argument("--файл", action="append", default=[], help="тронутый файл")
    args = parser.parse_args(argv)

    if args.совет:
        config = общее.scenarios()
        роды = list(dict.fromkeys(args.улика + общее.rods_of(args.файл, config)))
        неизвестные = [r for r in роды if r not in config.get("роды", {})]
        if неизвестные:
            print(f"acceptance_server: род улики не объявлен: {неизвестные}", file=sys.stderr)
            return 2
        return общее.advise(config, роды, "сервер")
    if not args.суд:
        parser.print_help()
        return 2
    if not args.зона:
        print("── П3а вышел за рамки задачи: зона не объявлена (--зона), критерий не судится")
    failed = False
    for title, check in CHECKS:
        if check is общее.outside and not args.зона:
            continue
        notes = (общее.outside(tuple(args.зона)) if check is общее.outside
                 else check(root=ROOT, zones=tuple(args.зона)))
        print(f"── {title}: {'чисто' if not notes else str(len(notes)) + ' шт.'}")
        for note in notes:
            print(f"   ✗ {note}")
        failed = failed or bool(notes)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
