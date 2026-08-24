"""scripts/invariants.py — межфайловые инварианты: то, что живёт в двух местах и обязано совпадать.

## Назначение
Ни `ruff`, ни `mypy`, ни `pytest` не видят разрыв МЕЖДУ файлами: пропуск набора и установка бинаря
в CI, объявление `enum` и его значения, брошенный код отказа и реестр реакций. Каждый такой разрыв
уже стоил находки, и каждый молчал до чужого прогона.

## Границы
Долг не чинится задним числом: `enum` без значений считается ХРАПОВИКОМ (вниз можно, вверх нет),
остальное — жёсткие проверки, потому что нарушений в них сейчас ноль.
"""

from __future__ import annotations

import ast
import json
import builtins
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).with_name("invariants_baseline.txt")

# Пути НЕ зашиты: корень приходит параметром, иначе сторожа нельзя проверить, не насорив в
# репозитории, — а проверка, требующая мусора, делается редко.
TABLES = ("config", "templates", "tables")
TESTS = ("tests",)
CI = (".github", "workflows", "ci.yml")
REGISTRY = ("config", "server_reactions.yaml")
RESOURCES = ("config", "resources.yaml")
ROADMAP = ("docs", "roadmap")
FINDINGS = ("docs", "roadmap", "02_findings.md")
INVENTORY = ("tests", "quick", "tools_inventory.golden.json")


def _at(root: Path, parts: tuple[str, ...]) -> Path:
    return root.joinpath(*parts)

# Код отказа бросают либо исключением зоны, либо обёрткой контекста.
RAISED_CODE = re.compile(r'(?:Error|\berr|_err|err_path)\(\s*\n?\s*"([A-Z][A-Z_0-9]{3,})"')
WHICH = re.compile(r'shutil\.which\(\s*"([^"]+)"\s*\)')


def enum_without_values(root: Path = ROOT) -> list[str]:
    """`type: enum` без перечня значений — не enum, а строка с обещанием: писать можно что угодно."""
    notes = []
    for schema in sorted(_at(root, TABLES).glob("*.schema.yaml")):
        data = yaml.safe_load(schema.read_text(encoding="utf-8")) or {}
        for sheet in data.get("sheets") or []:
            for column in sheet.get("columns") or []:
                if column.get("type") == "enum" and not column.get("enum"):
                    notes.append(f"{schema.name}:{sheet['name']}.{column['name']} — "
                                 f"enum без значений: проверить запись нечем")
    return notes


def skips_without_ci(root: Path = ROOT) -> list[str]:
    """Набор пропускает проверки без бинаря — значит в CI бинарь ставится ИЛИ пропуск запрещён.

    Иначе половина набора в CI не исполняется, а зелёный цвет означает «не проверяли»: замер по
    трём наборам монтажа дал 166 проверок с бинарём и 60 без него.
    """
    ci_file, tests_dir = _at(root, CI), _at(root, TESTS)
    if not ci_file.exists():
        return []
    # Ищем в КОМАНДАХ и переменных джоб, а не в тексте файла: упоминание в комментарии — не
    # установка, и поиск по сырому тексту принял бы объяснение за механизм (поймано мутацией).
    workflow = yaml.safe_load(ci_file.read_text(encoding="utf-8")) or {}
    commands, env_names = [], set()
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            commands.append(str(step.get("run") or ""))
            commands.append(str((step.get("with") or {}).get("node-version") and "node" or ""))
            env_names |= set((step.get("env") or {}))
        env_names |= set(job.get("env") or {})
    ci_run = "\n".join(commands)
    notes = []
    for suite in sorted(tests_dir.rglob("test_*.py")):
        text = suite.read_text(encoding="utf-8")
        required = {name for name in re.findall(r'os\.environ\.get\("([A-Z_]*REQUIRED)"\)', text)
                    if name in env_names}
        for binary in sorted(set(WHICH.findall(text))):
            if binary in ci_run or required:
                continue
            notes.append(f"{suite.relative_to(root)} — пропускается без `{binary}`, а в CI он не "
                         f"ставится и пропуск не запрещён флагом *_REQUIRED")
    return notes


SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
          ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _blocks(stmt: ast.stmt) -> list[list[ast.stmt]]:
    """Вложенные блоки оператора: их тела разбираются отдельно и ПОСЛЕ его заголовка."""
    if isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While)):
        return [stmt.body, stmt.orelse]
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return [stmt.body]
    if isinstance(stmt, ast.Try):
        return [stmt.body, stmt.orelse, stmt.finalbody]      # тела `except` — отдельно, с их именем
    return []


def _header(stmt: ast.stmt) -> list[ast.AST]:
    """Часть оператора, исполняемая ДО его тела: условие, выражение цикла, менеджер контекста."""
    if isinstance(stmt, ast.If):
        return [stmt.test]
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return [stmt.iter]
    if isinstance(stmt, ast.While):
        return [stmt.test]
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return list(stmt.items)
    if isinstance(stmt, ast.Try):
        return []
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # Тело функции исполняется при ВЫЗОВЕ, а не здесь: до него дела нет. Исполняются сейчас
        # только декораторы и значения по умолчанию.
        return [*stmt.decorator_list, *[d for d in stmt.args.defaults if d is not None],
                *[d for d in stmt.args.kw_defaults if d is not None]]
    if isinstance(stmt, ast.ClassDef):
        return [*stmt.decorator_list, *stmt.bases, *stmt.keywords]
    return [stmt]


def _walk_own_scope(node: ast.AST):
    """Обход БЕЗ захода в чужие области имён: у функции, класса и генератора свои имена."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, SCOPES):
            continue
        yield child
        yield from _walk_own_scope(child)


def _loads(part: ast.AST) -> list[ast.Name]:
    nodes = [part] if isinstance(part, ast.Name) else []
    nodes += [n for n in _walk_own_scope(part) if isinstance(n, ast.Name)]
    return [n for n in nodes if isinstance(n.ctx, ast.Load)]


def _bound(part: ast.AST) -> set[str]:
    """Имена, которые оператор ОБЪЯВЛЯЕТ: присваивания, импорты, имена функций и классов."""
    names = set()
    for node in [part, *_walk_own_scope(part)]:
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def used_before_declared(root: Path = ROOT) -> list[str]:
    """Имя, объявленное НИЖЕ по набору, ловится только исполнением: набор — линейный скрипт.

    `ruff` молчит (имя определено, просто позже), `mypy` тоже, а если блок лежит в пропускаемой
    ветке, ошибка доживёт до первого локального прогона — гейт её не увидит.
    """
    notes = []
    for suite in sorted(_at(root, TESTS).rglob("test_*.py")):
        try:
            tree = ast.parse(suite.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            notes.append(f"{suite.relative_to(root)} — не разбирается: {exc}")
            continue
        known = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__"}
        # Имена функций и классов известны заранее: тела их не исполняются до вызова.
        known |= {n.name for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}

        def flag(name: ast.Name) -> None:
            note = (f"{suite.relative_to(root)}:{name.lineno} — `{name.id}` используется до "
                    f"объявления (набор идёт сверху вниз)")
            if note not in notes:
                notes.append(note)

        def check(part: ast.AST) -> None:
            for name in _loads(part):
                if name.id not in known:
                    flag(name)

        def visit(block: list[ast.stmt]) -> None:
            for stmt in block:
                # `with A() as a, B(a) as b`: второй менеджер видит имя из первого, поэтому
                # элементы разбираются по очереди, а не все сразу.
                if isinstance(stmt, (ast.With, ast.AsyncWith)):
                    for item in stmt.items:
                        check(item.context_expr)
                        if item.optional_vars is not None:
                            known.update(_bound(item.optional_vars))
                elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                    check(stmt.iter)
                    known.update(_bound(stmt.target))
                else:
                    for part in _header(stmt):
                        check(part)
                for nested in _blocks(stmt):
                    visit(nested)
                # `except E as e:` объявляет имя на своё тело — иначе оно выглядит незнакомым.
                if isinstance(stmt, ast.Try):
                    for handler in stmt.handlers:
                        if handler.name:
                            known.add(handler.name)
                        visit(handler.body)
                known.update(_bound(stmt))

        visit(tree.body)
    return notes


def codes_outside_registry(root: Path = ROOT, known: set[str] | None = None) -> list[str]:
    """Брошенный код отказа обязан быть и в реестре реакций, и в `KNOWN_ERROR_CODES`."""
    if known is None:
        sys.path.insert(0, str(root))
        from core.contracts.error_detail import KNOWN_ERROR_CODES as known

    registry = set(yaml.safe_load(_at(root, REGISTRY).read_text(encoding="utf-8")) or {})
    notes = []
    for source in sorted(list((root / "core").rglob("*.py")) + list((root / "tools").rglob("*.py"))):
        if "__pycache__" in str(source):
            continue
        for code in sorted(set(RAISED_CODE.findall(source.read_text(encoding="utf-8")))):
            where = source.relative_to(root)
            if code not in registry:
                notes.append(f"{where} — код `{code}` не объявлен в server_reactions.yaml: "
                             f"клиент получит его без класса и без рекавери")
            elif code not in known:
                notes.append(f"{where} — код `{code}` не в KNOWN_ERROR_CODES: контракт предупредит "
                             f"о неизвестном коде на боевом пути")
    return notes


# Статус ВПЛОТНУЮ к номеру находки — единственная форма, где смысл однозначен: это поле, а не
# проза. Разбирать прозу бессмысленно (замер дал от 17 до 370 ложных срабатываний), а поле точно.
STATUS_NEAR = re.compile(r"(F\d{1,3})\s*(✅|🔴|🟠|🟡|🟢)|(✅|🔴|🟠|🟡|🟢)\s*(F\d{1,3})")
REGISTRY_ROW = re.compile(r"\|\s*(~~)?\*{0,2}(F\d+)\*{0,2}(~~)?\s*\|\s*([^|]*)\|")


def status_off_registry(root: Path = ROOT) -> list[str]:
    """Статус находки в плане разошёлся с реестром — хозяин факта один, копии разъезжаются молча.

    Журнал сессий и сам реестр исключены: история обязана хранить прежние статусы, а в реестре
    ниже канонической строки лежат таблицы переформулировок с тем же номером.
    """
    registry_file = _at(root, FINDINGS)
    if not registry_file.exists():
        return []
    registry: dict[str, str] = {}
    for line in registry_file.read_text(encoding="utf-8").splitlines():
        row = REGISTRY_ROW.match(line)
        if row and row.group(2) not in registry:          # первая строка — каноническая
            closed = bool(row.group(1)) or "✅" in row.group(4) or "🟢" in row.group(4)
            registry[row.group(2)] = "закрыт" if closed else "открыт"
    notes = []
    for path in sorted(_at(root, ROADMAP).glob("*.md")) + [root / "tests" / "CATALOG.md"]:
        if not path.exists() or path.name in ("02_findings.md", "_sessions.md"):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in STATUS_NEAR.finditer(line):
                ident, mark = m.group(1) or m.group(4), m.group(2) or m.group(3)
                says = "закрыт" if mark in "✅🟢" else "открыт"
                if ident in registry and says != registry[ident]:
                    notes.append(f"{path.name}:{n} — {ident} показан «{says}», в реестре «{registry[ident]}»")
    return notes


def resources_off_inventory(root: Path = ROOT) -> list[str]:
    """Инструмент объявлен тяжёлым, а такого инструмента нет — снятие с цикла молча не действует.

    Переименовали инструмент — строка в `resources.yaml` осталась указывать в пустоту, вызов снова
    исполняется в цикле событий и морозит сервер, и НИ ОДНА проверка об этом не скажет: сценарий
    живучести бьёт единственным именем. Тот же класс — класс ресурса, которого нет в `classes`:
    инструмент тихо падает в `default`.
    """
    declaration, inventory = _at(root, RESOURCES), _at(root, INVENTORY)
    if not declaration.exists() or not inventory.exists():
        return []
    data = yaml.safe_load(declaration.read_text(encoding="utf-8")) or {}
    known_tools = set(json.loads(inventory.read_text(encoding="utf-8")))
    classes = set((data.get("classes") or {}))
    notes = []
    for tool, klass in (data.get("tools") or {}).items():
        if tool not in known_tools:
            notes.append(f"config/resources.yaml: инструмента `{tool}` нет в инвентаре — строка указывает в пустоту")
        if str(klass) not in classes:
            notes.append(f"config/resources.yaml: у `{tool}` класс `{klass}`, которого нет в `classes` — "
                         "вызов молча падает в default")
    default = str(data.get("default") or "")
    if default and default not in classes:
        notes.append(f"config/resources.yaml: `default: {default}` не объявлен в `classes`")
    return notes


HARD = (("пропуск набора без покрытия в CI", skips_without_ci),
        ("имя используется до объявления", used_before_declared),
        ("код отказа мимо реестра", codes_outside_registry),
        ("объявление ресурсов мимо инвентаря", resources_off_inventory),
        ("статус находки мимо реестра", status_off_registry))


def ratchet(notes: list[str]) -> tuple[int, int]:
    """Долг по `enum` без значений: вниз можно, вверх нет. Потолок — рядом, в baseline."""
    limit = int(BASELINE.read_text(encoding="utf-8").strip()) if BASELINE.exists() else len(notes)
    return len(notes), limit


def main() -> int:
    # Режим хука: молчим, когда чисто. Сторож, печатающий «всё хорошо» после каждой правки,
    # превращается в шум, и его перестают читать.
    quiet = "--hook" in sys.argv
    failed = False
    for title, check in HARD:
        notes = check()
        if not quiet:
            print(f"── {title}: {'чисто' if not notes else str(len(notes)) + ' шт.'}")
        elif notes:
            print(f"── {title}: {len(notes)} шт.")
        for note in notes:
            print(f"   ✗ {note}")
        failed = failed or bool(notes)

    notes = enum_without_values()
    count, limit = ratchet(notes)
    if not quiet or count > limit:
        print(f"── enum без значений: {count} при потолке {limit}")
    if "--bless" in sys.argv:
        BASELINE.write_text(f"{count}\n", encoding="utf-8")
        print(f"   потолок записан: {count}")
        return 0
    if count > limit:
        for note in notes:
            print(f"   ✗ {note}")
        print("   Долг вырос. Почини столбцы выше или объясни в ревью: --bless")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
