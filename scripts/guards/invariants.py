"""scripts/guards/invariants.py — межфайловые инварианты: то, что живёт в двух местах и обязано совпадать.

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
import importlib.util
import json
import builtins
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from findings_count import scan as scan_registry  # noqa: E402  разбор реестра — один на проект

BASELINE = Path(__file__).with_name("invariants_baseline.txt")
DISPATCH_BASELINE = Path(__file__).with_name("dispatch_baseline.txt")
UNSCRIPTED_BASELINE = Path(__file__).with_name("unscripted_baseline.txt")
DEAD_RECOVERY_BASELINE = Path(__file__).with_name("dead_recovery_baseline.txt")
ORPHAN_BASELINE = Path(__file__).with_name("orphan_codes_baseline.txt")
MIRROR_BASELINE = Path(__file__).with_name("mirror_baseline.txt")
FACT_EMITTER_BASELINE = Path(__file__).with_name("fact_emitters_baseline.txt")
FACT_EXEMPT_BASELINE = Path(__file__).with_name("fact_exempt_baseline.txt")
# Две ветки — это выбор, три и больше по одному значению — уже таблица.
DISPATCH_LIMIT = 3

# Пути НЕ зашиты: корень приходит параметром, иначе сторожа нельзя проверить, не насорив в
# репозитории, — а проверка, требующая мусора, делается редко.
TABLES = ("config", "templates", "tables")
TESTS = ("tests",)
CI = (".github", "workflows", "ci.yml")
REGISTRY = ("config", "server_reactions.yaml")
CONTRACT = ("core", "contracts", "error_detail.py")
RESOURCES = ("config", "resources.yaml")
ROADMAP = ("docs", "roadmap")
FINDINGS = ("docs", "roadmap", "02_findings.md")
INVENTORY = ("tests", "quick", "tools_inventory.golden.json")
CATALOG = ("tests", "CATALOG.md")
GUARDS = ("scripts", "guards")
GUARDS_CATALOG = ("scripts", "guards", "CATALOG.md")
GATE = ("tests", "test_suites.py")
SCENARIOS = ("tests", "scenarios")
ROUTES = ("tests", "routes")
FACT_TYPES = ("core", "contracts", "fact.py")
OBSERVATIONS = ("tests", "harness", "observations.yaml")
HOOK_SETTINGS = (".claude", "settings.json")
HOOKS = (".claude", "hooks")


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
    """Брошенный код отказа обязан быть и в реестре реакций, и в `KNOWN_ERROR_CODES`.

    Реестра нет — сверять не с чем, и это «улики нет», а не находка. Соседи по семейству молчат
    так же; здесь проверки не было, и сторож умирал трейсом вместо вердикта.
    """
    registry_file = _at(root, REGISTRY)
    if not registry_file.exists():
        return []
    if known is None:
        sys.path.insert(0, str(root))
        from core.contracts.error_detail import KNOWN_ERROR_CODES as known

    registry = set(yaml.safe_load(registry_file.read_text(encoding="utf-8")) or {})
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


def _registry_status(root: Path = ROOT) -> dict[str, str]:
    """{идентификатор: «закрыт»/«открыт»} по реестру. Разбор берётся у `findings_count`."""
    registry_file = _at(root, FINDINGS)
    if not registry_file.exists():
        return {}
    rows = scan_registry(registry_file.read_text(encoding="utf-8"))
    return {ident: "закрыт" if closed else "открыт" for ident, closed in rows.items()}


def status_off_registry(root: Path = ROOT) -> list[str]:
    """Статус находки в плане разошёлся с реестром — хозяин факта один, копии разъезжаются молча.

    Журнал сессий и сам реестр исключены: история обязана хранить прежние статусы, а в реестре
    ниже канонической строки лежат таблицы переформулировок с тем же номером.
    """
    registry = _registry_status(root)
    if not registry:
        return []
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
    for name, body in (data.get("classes") or {}).items():
        # Класс, снимаемый с цикла, без предела одновременности: сто тяжёлых вызовов поднимут
        # сто потоков, и заморозка вернётся с другой стороны.
        if (body or {}).get("offload") and not (body or {}).get("max_concurrent"):
            notes.append(f"config/resources.yaml: класс `{name}` снимается с цикла, но предела "
                         "одновременности не объявлено")
    return notes



NUMERAL = {"два": 2, "двух": 2, "три": 3, "трёх": 3, "четыре": 4, "четырёх": 4, "пять": 5,
           "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8}
JOBS_CLAIM = re.compile(r"(\d+|" + "|".join(NUMERAL) + r")\s+джоб", re.I)


def ci_jobs_off_docs(root: Path = ROOT) -> list[str]:
    """Сколько джоб в гейте — сказано в `ci.yml` и повторено в доках; копия стареет молча.

    Журналы исключены, и список их не зашит: роль объявляет реестр `docs/roadmap/README.md`, а
    запись о ПРОШЛОМ состоянии («тогда джоб было пять») обязана остаться такой, какой была.
    """
    ci = _at(root, (".github", "workflows", "ci.yml"))
    if not ci.exists():
        return []
    jobs = (yaml.safe_load(ci.read_text(encoding="utf-8")) or {}).get("jobs") or {}
    if not jobs:
        return []
    registry = _at(root, ROADMAP + ("README.md",))
    history = set()
    if registry.exists():
        for row in registry.read_text(encoding="utf-8").splitlines():
            if row.startswith("|") and "журнал" in row.lower():
                history |= set(re.findall(r"\(([\w.]+\.md)\)", row))
    notes = []
    for doc in sorted(_at(root, ROADMAP).glob("*.md")) + [root / "README.md"]:
        if doc.name in history or not doc.exists():
            continue
        for line, text in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for raw in JOBS_CLAIM.findall(text):
                said = int(raw) if raw.isdigit() else NUMERAL[raw.lower()]
                if said != len(jobs):
                    notes.append(f"{doc.relative_to(root).as_posix()}:{line} — сказано «{raw} джоб», "
                                 f"а в ci.yml их {len(jobs)}: {', '.join(jobs)}")
    return notes


def module_without_reader(root: Path = ROOT) -> list[str]:
    """Модуль ядра, которого не зовёт НИ импорт, НИ объявление, — половина, которую никто не читает.

    Живым модуль делает одно из двух: его импортируют либо на него указывает декларация
    (`config/providers.yaml: img.onnx_bg:OnnxBGRemoval` — импорта нет и не будет). Считать
    объявление чтением обязательно: иначе вся декларативная архитектура числилась бы мёртвой.
    Пакеты и `__main__` грузятся по построению, спрашивать с них нечего.
    """
    def dotted(path: Path) -> str:
        return path.relative_to(root).as_posix()[:-3].replace("/", ".").removesuffix(".__init__")

    targets = {dotted(p): p for group in ("core", "tools")
               for p in _at(root, (group,)).rglob("*.py") if "__pycache__" not in str(p)}
    if not targets:
        return []
    readers: dict[str, set[str]] = {name: set() for name in targets}
    for source in root.rglob("*.py"):
        if {"__pycache__", ".venv", "vendor"} & set(source.parts):
            continue
        package = dotted(source) if source.name == "__init__.py" else dotted(source).rsplit(".", 1)[0]
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            mentions: set[str] = set()
            if isinstance(node, ast.Import):
                mentions |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    # Относительный импорт разрешается от пакета ИСТОЧНИКА: иначе `from .x import y`
                    # не совпадёт ни с одним модулем и живой файл окажется сиротой.
                    parts = package.split(".")
                    base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
                if base:
                    mentions.add(base)
                    mentions |= {f"{base}.{alias.name}" for alias in node.names}
            for name in mentions:
                if name in targets and targets[name] != source:
                    readers[name].add(str(source))

    declarations = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                             for p in _at(root, ("config",)).rglob("*.yaml"))
    notes = []
    for name, path in sorted(targets.items()):
        if path.name in ("__init__.py", "__main__.py") or readers[name]:
            continue
        if ".".join(name.split(".")[-2:]) in declarations or path.stem in declarations:
            continue
        notes.append(f"{path.relative_to(root).as_posix()} — модуль без единого читателя: ни импорта, "
                     "ни строки в config/*.yaml. Либо он мёртв и сносится, либо потерял проводку")
    return notes


def _gate_suites(root: Path) -> list[Path] | None:
    """Список наборов берём у САМОГО гейта, а не заводим второй: разошлись бы молча.

    Модуль грузится по пути, поэтому его `ROOT` считается от переданного корня — сторож
    проверяется на временном каталоге, не мусоря в репозитории.
    """
    gate = _at(root, GATE)
    if not gate.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"_vpm_gate_{abs(hash(str(gate)))}", gate)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module._discover())


def _catalog_zones(root: Path) -> set[str]:
    """Зона объявлена ПЕРВОЙ ячейкой строки таблицы. Упоминание в прозе зоной не считается:
    сторож, которого удовлетворяет любое упоминание имени, проверяет след, а не вещь."""
    path = _at(root, CATALOG)
    if not path.exists():
        return set()
    zones = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("|"):
            cell = line.split("|")[1].strip().strip("*").strip("`").strip()
            if cell:
                zones.add(cell)
    return zones


SUITE_ZONE = re.compile(r"^(?:[a-z_0-9]+/)?test_[a-z_0-9]+\.py$")


def suites_off_catalog(root: Path = ROOT) -> list[str]:
    """Набор гоняется гейтом, а зоны ответственности в каталоге у него нет — так тесты и плодятся.

    Обратная сторона тоже красная: строка про набор, которого нет, отправляет расширять пустоту.
    """
    suites = _gate_suites(root)
    if suites is None:
        return []
    zones = _catalog_zones(root)
    if not zones:
        return []
    notes = []
    have: set[str] = set()
    for path in suites:
        keys = {path.name, f"{path.parent.name}/", f"{path.parent.name}/{path.name}"}
        have |= keys
        if not (keys & zones):
            notes.append(f"{path.relative_to(root)} — набор в гейте без строки-зоны в tests/CATALOG.md: "
                         "сначала расширь хозяина по его запасу; заводишь свой — объяви зону и запас")
    for zone in sorted(zones):
        if SUITE_ZONE.match(zone) and zone not in have:
            notes.append(f"tests/CATALOG.md: зона `{zone}` объявлена, а набора нет — "
                         "расширять предлагается несуществующее")
        # Судим по наборам, а не по каталогу: `git rm` уносит файлы, но каталог остаётся жив
        # из-за `__pycache__`, и зона по существованию каталога считалась бы живой после сноса.
        elif zone.endswith("/") and not any(k.startswith(zone) for k in have):
            notes.append(f"tests/CATALOG.md: зона `{zone}` объявлена, а наборов в ней нет — "
                         "снесённый набор продолжает числиться живым")
    return notes


def _chain(node: ast.If) -> list[ast.Compare]:
    """Цепочка `if/elif`: в питоне `elif` — это вложенный `If` в `orelse`."""
    out, current = [], node
    while True:
        if not (isinstance(current.test, ast.Compare) and len(current.test.ops) == 1
                and isinstance(current.test.ops[0], ast.Eq)
                and len(current.test.comparators) == 1
                and isinstance(current.test.comparators[0], ast.Constant)
                and isinstance(current.test.comparators[0].value, str)):
            break
        out.append(current.test)
        if len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
            current = current.orelse[0]
            continue
        break
    return out


def dispatch_by_value(root: Path = ROOT) -> list[str]:
    """Ветвление по значению длиннее порога — это таблица, а не ветки.

    `if` уместен там, где ветки разной природы; когда одно и то же ВЫЧИСЛЯЕТСЯ по значению, механизм
    обязан приходить из объявления, иначе новый случай добавляется правкой кода, а не строкой конфига,
    и о забытой ветке никто не узнает. Храповик: вниз можно, вверх нет.
    """
    notes = []
    for source in sorted(list((root / "core").rglob("*.py")) + list((root / "tools").rglob("*.py"))
                         + [root / "server.py"]):
        if "__pycache__" in str(source) or not source.exists():
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        seen: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.If) or id(node) in seen:
                continue
            chain = _chain(node)
            if len(chain) < DISPATCH_LIMIT:
                continue
            subject = ast.unparse(chain[0].left)
            if any(ast.unparse(c.left) != subject for c in chain):
                continue
            for extra in ast.walk(node):
                seen.add(id(extra))
            values = [c.comparators[0].value for c in chain]
            notes.append(f"{source.relative_to(root)}:{node.lineno} — ветвление по `{subject}` на "
                         f"{len(chain)} значений {values}: механизм из объявления, политика кодом")
    return notes


def _scenario_declarations(root: Path) -> tuple[dict[str, set[str]], set[str]]:
    """Что объявления сценариев ЗОВУТ (инструмент → ожидаемые от него коды) и какие коды ЖДУТ.

    Разбор YAML, а не грепом: ожидание пишут и блоком, и потоком (`{ok: false, code: X}`), и на
    втором виде греп теряет строку молча — покрытие мерялось бы меньше настоящего. Ожидание
    хранится ПРИ инструменте, потому что вызов несуществующего имени бывает и предметом проверки.
    """
    calls: dict[str, set[str]] = {}
    codes: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("call"), str):
                expect = node.get("expect") or {}
                want = str(expect.get("code") or "") if isinstance(expect, dict) else ""
                calls.setdefault(node["call"], set()).add(want)
            if isinstance(node.get("code"), str):
                codes.add(node["code"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for path in sorted(_at(root, SCENARIOS).glob("*.yaml")) + sorted(_at(root, ROUTES).glob("*.yaml")):
        walk(yaml.safe_load(path.read_text(encoding="utf-8")))
    return calls, codes


def scenario_calls_unknown_tool(root: Path = ROOT) -> list[str]:
    """Сценарий зовёт инструмент, которого нет в описи: переименование оставляет объявление
    указывать в пустоту, и узнаётся это только на многоминутном прогоне против живого сервера.

    Исключение — шаг, который ЖДЁТ `TOOL_NOT_FOUND`: там несуществующее имя и есть предмет.
    """
    inventory = _at(root, INVENTORY)
    if not inventory.exists():
        return []
    known = set(json.loads(inventory.read_text(encoding="utf-8")))
    calls, _ = _scenario_declarations(root)
    return [f"tests/scenarios: сценарий зовёт `{tool}`, которого нет в описи инструментов"
            for tool, wanted in sorted(calls.items())
            if tool not in known and "TOOL_NOT_FOUND" not in wanted]


QUOTED_CODE = re.compile(r'["\']([A-Z][A-Z_0-9]{3,})["\']')


def _emitted_codes(root: Path) -> set[str]:
    """Коды, которые сервер вообще способен бросить.

    Перечень контракта `error_detail.py` исключён: он коды ОБЪЯВЛЯЕТ, и с ним живым выглядит
    каждый. Берётся любая упомянутая в кавычках строка, а не одна форма вызова: `RAISED_CODE`
    видит 61 код из 66, и сторож на нём обвинял бы живые пути — ложное обвинение выключают
    быстрее, чем отсутствие сторожа.
    """
    sources = [p for p in sorted(list((root / "core").rglob("*.py")) + list((root / "tools").rglob("*.py")))
               if "__pycache__" not in str(p) and p != _at(root, CONTRACT)]
    if (root / "server.py").exists():
        sources.append(root / "server.py")
    found: set[str] = set()
    for source in sources:
        found |= set(QUOTED_CODE.findall(source.read_text(encoding="utf-8")))
    return found


def codes_without_emitter(root: Path = ROOT) -> list[str]:
    """Код объявлен клиенту реестром — а бросить его некому: обещание, которого никто не держит.

    Обратная сторона `codes_outside_registry`, и она молчала: ИИ читает реестр как перечень
    возможных отказов и строит на нём восстановление, а такой отказ не придёт никогда.
    """
    registry = _at(root, REGISTRY)
    if not registry.exists():
        return []
    declared = {k for k, v in (yaml.safe_load(registry.read_text(encoding="utf-8")) or {}).items()
                if isinstance(v, dict)}
    return [f"код `{code}` объявлен в server_reactions.yaml, но ни один путь сервера его не бросает"
            for code in sorted(declared - _emitted_codes(root))]


def declared_but_unscripted(root: Path = ROOT) -> list[str]:
    """Сервер умеет ответить, но НИ ОДНО объявление сценария этого не ждёт.

    Слепота растёт молча: новый код отказа попадает в реестр, новый инструмент — в опись, а
    покрытие остаётся вчерашним. Храповик держит потолок, и долг закрывается ОБЪЯВЛЕНИЕМ в
    `tests/scenarios/*.yaml` — python-скрипт на новый код писать не нужно.
    """
    calls, codes = _scenario_declarations(root)
    notes = []
    registry = _at(root, REGISTRY)
    if registry.exists():
        declared = {k for k, v in (yaml.safe_load(registry.read_text(encoding="utf-8")) or {}).items()
                    if isinstance(v, dict)}
        # Код, который бросить некому, сценарием не закрывается в принципе — он у соседнего
        # сторожа, и считать его здесь значило бы требовать невозможного.
        notes += [f"код отказа `{code}` сервер бросает, но ни один сценарий его не ждёт"
                  for code in sorted((declared & _emitted_codes(root)) - codes)]
    inventory = _at(root, INVENTORY)
    if inventory.exists():
        notes += [f"инструмент `{tool}` есть в описи, но ни один сценарий его не зовёт"
                  for tool in sorted(set(json.loads(inventory.read_text(encoding="utf-8"))) - set(calls))]
    return notes



# Факт — третья вещь, которую сервер объявляет клиенту наряду с кодом отказа и инструментом
# (он доезжает в `structuredContent`), и концов у него три: реестр типов, место эмиссии,
# наблюдатель харнесса. Каждый разрыв между ними молчит по-своему.
FACT_NAME = re.compile(r"""["']([A-Z][A-Za-z0-9]{3,})["']""")


def _fact_sources(root: Path) -> list[Path]:
    sources = [p for p in sorted(list((root / "core").rglob("*.py")) + list((root / "tools").rglob("*.py")))
               if "__pycache__" not in str(p) and p != _at(root, FACT_TYPES)]
    if (root / "server.py").exists():
        sources.append(root / "server.py")
    return sources


def _literal_branches(node: ast.AST) -> set[str]:
    """Только ВЕТВИ выражения, не его условие: `"A" if c["kind"] == "folder" else "B"` даёт две
    строки, а не четыре — обход целиком тащил бы в реестр фактов сравниваемые литералы."""
    if isinstance(node, ast.Constant):
        return {node.value} if isinstance(node.value, str) else set()
    if isinstance(node, ast.IfExp):
        return _literal_branches(node.body) | _literal_branches(node.orelse)
    return set()


def _facts_emitted(root: Path) -> set[str]:
    """Типы, которые действительно строятся вызовом `Fact(type=...)`.

    Разбором, а не строкой в тексте: обвинение здесь адресное, и занижение безопаснее завышения.
    Тип, собранный из переменной, отсюда не виден — обратную сторону считает
    `_fact_names_mentioned`, где занижение как раз опасно.
    """
    found: set[str] = set()
    for source in _fact_sources(root):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "Fact"):
                continue
            for kw in node.keywords:
                if kw.arg == "type":
                    found |= _literal_branches(kw.value)
    return found


def _fact_names_mentioned(root: Path) -> set[str]:
    """Любое имя в кавычках: `_created_result(..., "FileWritten")` — тоже эмиссия, через параметр.

    Тот же выбор, что у `_emitted_codes`: обвинять «слать некому» по одной форме вызова значило бы
    краснеть на живых путях, а ложное обвинение выключают быстрее, чем его отсутствие.
    """
    found: set[str] = set()
    for source in _fact_sources(root):
        found |= set(FACT_NAME.findall(source.read_text(encoding="utf-8")))
    return found


def _facts_declared(root: Path) -> set[str]:
    """Реестр типов читается разбором: импорт втащил бы pydantic и половину сервера в сторожа."""
    path = _at(root, FACT_TYPES)
    if not path.exists():
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, (ast.Set, ast.Tuple, ast.List))
                and any(isinstance(t, ast.Name) and t.id == "KNOWN_FACT_TYPES" for t in node.targets)):
            return {e.value for e in node.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return set()


def _observations(root: Path) -> dict:
    path = _at(root, OBSERVATIONS)
    if not path.exists():
        return {}
    declared = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {k: v for k, v in declared.items() if isinstance(v, dict)} if isinstance(declared, dict) else {}


def _facts_observed(root: Path) -> set[str]:
    """Факты, у которых наблюдение ОБЪЯВЛЕНО — правилом или объявленной ненаблюдаемостью.

    Оба — решение. Разница в том, что правило спрашивает реальность, а `observes: нечего` называет
    причину, по которой спрашивать нечего; забытым факт не считается ни в том, ни в другом случае.
    """
    return {k for k, v in _observations(root).items() if "identity" in v or "observes" in v}


def _facts_exempt(root: Path) -> set[str]:
    return {k for k, v in _observations(root).items() if "observes" in v}


def facts_outside_registry(root: Path = ROOT) -> list[str]:
    """Факт эмитится, а в реестре типов его нет.

    Единственный читатель реестра — `warnings.warn` внутри самой модели: он срабатывает в чужом
    процессе и его никто не слышит. Тот же класс, что `codes_outside_registry`: перечень,
    объявленный клиенту, обязан совпадать с тем, что сервер действительно шлёт.
    """
    declared = _facts_declared(root)
    if not declared:
        return []
    return [f"факт `{name}` эмитится, а в KNOWN_FACT_TYPES его нет: предупреждение модели уходит "
            "в рантайм, где его никто не слышит"
            for name in sorted(_facts_emitted(root) - declared)]


def facts_without_observer(root: Path = ROOT) -> list[str]:
    """Факт говорит «сделано», а спросить об этом реальность нечем.

    Наблюдение не выводится из факта — расхождение между ними и есть находка, ради которой
    харнесс заведён. Без строки в `observations.yaml` воспроизводитель не построит карту на этот
    факт, то есть покрытие останется вчерашним молча.
    """
    emitted = _facts_emitted(root)
    if not emitted:
        return []
    return [f"факт `{name}` эмитится, а решения о наблюдении нет — ни правила, ни объявленной "
            f"ненаблюдаемости в {'/'.join(OBSERVATIONS)}"
            for name in sorted(emitted - _facts_observed(root))]


def facts_exempt_from_observation(root: Path = ROOT) -> list[str]:
    """Объявленная ненаблюдаемость — решение с ценой: спросить реальность про этот факт нечем.

    Держится храповиком именно поэтому: без потолка объявление превращается в дверь, через которую
    любой новый факт уходит от наблюдения одной строкой.
    """
    return [f"факт `{name}` объявлен ненаблюдаемым: {rule.get('why') or '(без причины)'}"
            for name, rule in sorted(_observations(root).items()) if "observes" in rule]


def observation_incomplete(root: Path = ROOT) -> list[str]:
    """Полуобъявленное наблюдение молчит так же, как отсутствующее, — и выглядит закрытым долгом.

    Правило без любой из своих частей роняет воспроизводителя на живой записи, а ненаблюдаемость
    без причины — это «потом разберусь», записанное как решение.
    """
    notes = []
    for name, rule in sorted(_observations(root).items()):
        if "observes" in rule:
            if not str(rule.get("why") or "").strip():
                notes.append(f"наблюдение `{name}`: `observes: нечего` без причины — решение без "
                             "адреса работы неотличимо от забывчивости")
            continue
        missing = [k for k in ("identity", "observe", "with", "present", "absent") if k not in rule]
        if missing:
            notes.append(f"наблюдение `{name}`: нет {', '.join(f'`{m}`' for m in missing)} — "
                         "воспроизводитель упадёт на живой записи, а до неё это выглядит покрытием")
    return notes


def facts_without_emitter(root: Path = ROOT) -> list[str]:
    """Тип объявлен реестром или наблюдателем, а слать его некому — обещание без держателя.

    Две разные потери правды, поэтому и совет разный: в реестре это мёртвая строка контракта,
    в наблюдателе — карта, построенная на факт, которого не бывает.
    """
    declared, observed = _facts_declared(root), _facts_observed(root)
    if not declared and not observed:
        return []
    mentioned = _fact_names_mentioned(root)
    if not mentioned:
        return []
    notes = [f"тип `{name}` объявлен в KNOWN_FACT_TYPES, но ни один путь сервера его не шлёт"
             for name in sorted(declared - mentioned)]
    notes += [f"наблюдение факта `{name}` объявлено в {'/'.join(OBSERVATIONS)}, а слать его сервер "
              "не умеет" for name in sorted(observed - mentioned)]
    return notes


def _raise_sites(root: Path) -> list[tuple[str, str, str]]:
    """Места `ЗонаError(код, …, suggested_tool=…)`: (адрес, код, советуемый инструмент).

    Рецепт у зонного исключения — третий позиционный аргумент или именованный; обе формы в ходу.
    """
    out = []
    sources = [p for p in sorted(list((root / "core").rglob("*.py")) + list((root / "tools").rglob("*.py")))
               if "__pycache__" not in str(p)]
    for source in sources:
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if not name.endswith("Error") or not isinstance(node.args[0], ast.Constant):
                continue
            code = node.args[0].value
            tool = None
            if len(node.args) > 3 and isinstance(node.args[3], ast.Constant):
                tool = node.args[3].value
            for kw in node.keywords:
                if kw.arg == "suggested_tool" and isinstance(kw.value, ast.Constant):
                    tool = kw.value.value
            if isinstance(code, str) and isinstance(tool, str):
                out.append((f"{source.relative_to(root)}:{node.lineno}", code, tool))
    return out


def dead_recovery_in_engine(root: Path = ROOT) -> list[str]:
    """Движок советует инструмент, а клиент получает совет реестра: половина написана в мёртвую.

    `ctx.err` отдаёт приоритет реестру для любого кода, который в нём объявлен, а жёсткая проверка
    `codes_outside_registry` доказывает, что других кодов нет, — значит `suggested_tool` из кода до
    клиента не доезжает НИКОГДА. Хуже мёртвого текста только текст, вводящий в заблуждение
    читающего: один код бросается с тремя разными советами, а уезжает четвёртый.
    """
    registry = _at(root, REGISTRY)
    if not registry.exists():
        return []
    reactions = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    notes = []
    for where, code, tool in _raise_sites(root):
        entry = reactions.get(code)
        if not isinstance(entry, dict):
            continue
        declared = ((entry.get("recovery") or {}).get("suggested_tool")) or None
        if tool != declared:
            notes.append(f"{where} — код `{code}` советует `{tool}`, а клиент получит "
                         f"{('`' + declared + '`') if declared else 'реестровый рецепт без инструмента'}")
    return notes



# Имя короче этого совпадает с ключом объявления по случайности: `mode`, `type`, `path`.
MIRROR_NAME = 6


def _singular_declarations(root: Path) -> dict[str, tuple[str, object]]:
    """Ключи config/*.yaml, называющие РОВНО ОДНО значение на всё дерево: {ключ: (путь, значение)}."""
    seen: dict[str, dict[str, object]] = {}

    def walk(node, key: str, origin: str, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k), origin, f"{path}.{k}")
        elif isinstance(node, list):
            # Перечень скаляров — ОДНО объявление под своим именем. Спуск по элементам дал бы
            # тому же ключу N значений, и ключ переставал считаться единственным.
            if key and all(not isinstance(v, (dict, list)) for v in node):
                seen.setdefault(key.lower(), {})[f"{origin}:{path}"] = list(node)
            else:
                for i, v in enumerate(node):
                    walk(v, key, origin, f"{path}[{i}]")
        elif node is not None and not isinstance(node, bool):
            seen.setdefault(key.lower(), {})[f"{origin}:{path}"] = node

    for declaration in sorted((root / "config").glob("*.yaml")):
        walk(yaml.safe_load(declaration.read_text(encoding="utf-8")) or {}, "", declaration.name, "")
    return {k: next(iter(v.items())) for k, v in seen.items()
            if len(v) == 1 and len(k) >= MIRROR_NAME}


def _named_literals(tree: ast.AST) -> list[tuple[str, ast.AST, int]]:
    """Что код связывает с ИМЕНЕМ: присваивание, дефолт параметра, именованный аргумент."""
    out: list[tuple[str, ast.AST, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.append((target.id, node.value, node.lineno))
                elif isinstance(target, ast.Attribute):
                    out.append((target.attr, node.value, node.lineno))
        elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
            out.append((node.target.id, node.value, node.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            positional = node.args.posonlyargs + node.args.args
            for arg, default in zip(positional[len(positional) - len(node.args.defaults):], node.args.defaults):
                out.append((arg.arg, default, default.lineno))
            for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if default is not None:
                    out.append((arg.arg, default, default.lineno))
        elif isinstance(node, ast.keyword) and node.arg:
            out.append((node.arg, node.value, node.value.lineno))
    return out


def _same_fact(value: object, declared: object) -> bool:
    """Одно ли это значение. Перечень сравнивается по составу: `frozenset` в коде против списка
    в декларации — тот же факт, а не другой, и порядок в YAML ничего не значит."""
    kinds = (list, set, frozenset, tuple)
    if isinstance(value, kinds) and isinstance(declared, kinds):
        return sorted(map(repr, value)) == sorted(map(repr, declared))
    if isinstance(value, kinds) or isinstance(declared, kinds):
        return False
    # `2` в YAML и `2.0` в коде — одно значение; строка и число — нет.
    if isinstance(value, str) != isinstance(declared, str):
        return False
    return value == declared


def mirrored_declaration(root: Path = ROOT) -> list[str]:
    """Код держит ВТОРУЮ копию объявленного факта: то же имя, то же значение.

    Литерал сам по себе не улика: поиск по одному лишь равенству значений тонет в шуме. Уликой его
    делает второй ИМЕНОВАННЫЙ источник: ключ, называющий одно-единственное значение во всех
    декларациях, и то же имя в коде под тем же значением. Расхождение имён при равном значении —
    совпадение (`status` = 403 против столбца таблицы), поэтому обвиняется только полное совпадение.
    """
    singular = _singular_declarations(root)
    if not singular:
        return []
    notes = []
    sources = [p for p in sorted(list((root / "core").rglob("*.py")) + list((root / "tools").rglob("*.py"))
                                 + [root / "server.py"]) if "__pycache__" not in str(p) and p.exists()]
    for source in sources:
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for name, node, line in _named_literals(tree):
            bare = name.lower().strip("_")
            fallback = any(bare.startswith(pre) for pre in ("default_", "fallback_"))
            for prefix in ("default_", "fallback_"):
                if bare.startswith(prefix):
                    bare = bare[len(prefix):]
            if bare not in singular:
                continue
            # `frozenset({...})` — перечень, а не вызов: literal_eval его не берёт, и запасной
            # список молча уходил бы от сторожа именно в той форме, в какой его чаще всего пишут.
            inner = node.args[0] if (isinstance(node, ast.Call) and len(node.args) == 1
                                     and getattr(node.func, "id", "") in ("frozenset", "set", "tuple", "list")) else node
            try:
                value = ast.literal_eval(inner)
            except (ValueError, SyntaxError, TypeError):
                continue
            path, declared = singular[bare]
            same = _same_fact(value, declared)
            if fallback and not same:
                notes.append(f"{source.relative_to(root)}:{line} — запасное `{name}` = {value!r:.60} УЖЕ "
                             f"разошлось с объявлением {path} = {declared!r:.60}")
            elif same:
                notes.append(f"{source.relative_to(root)}:{line} — `{name}` = {value!r:.60} повторяет "
                             f"объявление {path}: правка декларации молча разойдётся с кодом")
    return notes




# Путь хука в объявлении — от корня проекта через подстановку Claude Code, поэтому и сверяется
# он с деревом, а не с домашним каталогом автора.
HOOK_PATH = re.compile(r"\$CLAUDE_PROJECT_DIR/(\.claude/hooks/[\w.-]+)")


def _declared_hooks(root: Path) -> set[str] | None:
    """None — объявление не читается: это отдельный исход, а не «объявлено ноль хуков»."""
    settings = _at(root, HOOK_SETTINGS)
    if not settings.exists():
        return set()
    try:
        declared = json.loads(settings.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return set(HOOK_PATH.findall(json.dumps(declared.get("hooks", {}))))


def hooks_off_declaration(root: Path = ROOT) -> list[str]:
    """Хук объявлен путём, которого нет, — или лежит файлом, которого никто не зовёт.

    Обе половины молчат по-своему: мёртвое объявление не падает, а незаявленный файл просто не
    запускается — поэтому сверяются обе, и «хука нет» никогда не выглядит как «хук промолчал».
    """
    directory = _at(root, HOOKS)
    settings = _at(root, HOOK_SETTINGS)
    if not directory.is_dir() and not settings.exists():
        return []
    declared = _declared_hooks(root)
    if declared is None:
        return [f"{'/'.join(HOOK_SETTINGS)} не разбирается как JSON — не загрузится НИ ОДИН хук, "
                "и это выглядит как их молчание"]
    present = {f"{'/'.join(HOOKS)}/{p.name}" for p in (directory.iterdir() if directory.is_dir() else ())
               if p.is_file() and not p.name.startswith(".")}
    notes = [f"{rel} объявлен в {'/'.join(HOOK_SETTINGS)}, а файла нет — событие приходит, "
             "запускать нечего, и отказ выглядит как молчание"
             for rel in sorted(declared - present)]
    notes += [f"{rel} лежит в дереве, а объявления в {'/'.join(HOOK_SETTINGS)} у него нет — "
              "код есть, срабатывать ему не на чем"
              for rel in sorted(present - declared)]
    return notes


def _zones_of(path: Path) -> set[str]:
    """Зоны, объявленные строками таблицы в этом каталоге."""
    if not path.exists():
        return set()
    zones = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("|"):
            cell = line.split("|")[1].strip().strip("*").strip("`").strip()
            if cell:
                zones.add(cell)
    return zones


def guards_off_catalog(root: Path = ROOT) -> list[str]:
    """Сторож без объявленной зоны — и правило «не плодить» перестаёт действовать на сторожей.

    Тот же механизм, что у наборов: у скриптов он нужен ровно потому, что слепую зону тянет
    закрыть НОВЫМ скриптом, хотя почти всегда она ложится в запас существующего. Обратная сторона
    так же красная: строка про сторожа, которого нет, отправляет расширять пустоту.
    """
    directory = _at(root, GUARDS)
    if not directory.is_dir():
        return []
    scripts = sorted(p for p in directory.glob("*.py") if not p.name.startswith("_"))
    zones = _zones_of(_at(root, GUARDS_CATALOG))
    if not scripts and not zones:
        return []                      # ни сторожей, ни зон — обвинять некого и не в чем
    if not zones:
        return [f"{'/'.join(GUARDS_CATALOG)} — каталога зон нет, а сторожа есть: "
                "правило «не плодить» держится дисциплиной, то есть не держится"]
    notes = []
    have = {p.name for p in scripts}
    for name in sorted(have - zones):
        notes.append(f"scripts/guards/{name} — сторож без строки-зоны в scripts/guards/CATALOG.md: "
                     "сначала расширь хозяина по его запасу; заводишь свой — объяви зону и улику")
    for zone in sorted(zones - have):
        if zone.endswith(".py"):
            notes.append(f"scripts/guards/CATALOG.md: зона `{zone}` объявлена, а сторожа нет — "
                         "расширять предлагается несуществующее")
    return notes

HARD = (("пропуск набора без покрытия в CI", skips_without_ci),
        ("имя используется до объявления", used_before_declared),
        ("код отказа мимо реестра", codes_outside_registry),
        ("объявление ресурсов мимо инвентаря", resources_off_inventory),
        ("статус находки мимо реестра", status_off_registry),
        ("набор мимо каталога зон", suites_off_catalog),
        ("сторож мимо каталога зон", guards_off_catalog),
        ("модуль без единого читателя", module_without_reader),
        ("число джоб гейта мимо ci.yml", ci_jobs_off_docs),
        ("сценарий зовёт инструмент мимо описи", scenario_calls_unknown_tool),
        ("факт мимо реестра типов", facts_outside_registry),
        ("хук мимо объявления", hooks_off_declaration),
        ("объявление наблюдения неполно", observation_incomplete),
        ("факт эмитится, а решения о наблюдении нет", facts_without_observer))

# Храповик: вниз можно, вверх нет. Потолок — в файле рядом, совет — как долг закрывается.
RATCHETS = (
    ("enum без значений", enum_without_values, BASELINE,
     "Долг вырос. Почини столбцы выше или объясни в ревью: --bless"),
    ("ветвление по значению вместо таблицы", dispatch_by_value, DISPATCH_BASELINE,
     "Новая цепочка ветвления: объяви таблицу (config/*.yaml → генерик в core/) "
     "или опусти потолок осознанно — --bless"),
    ("объявлено клиенту, но бросить некому", codes_without_emitter, ORPHAN_BASELINE,
     "Реестр обещает клиенту отказ, которого не бывает: либо путь, который его бросает, "
     "либо снять строку из server_reactions.yaml и KNOWN_ERROR_CODES"),
    ("рецепт движка, который клиент не увидит", dead_recovery_in_engine, DEAD_RECOVERY_BASELINE,
     "Совет из кода перекрывается реестром и до клиента не доезжает: либо снять аргумент "
     "`suggested_tool`, либо поправить рецепт в config/server_reactions.yaml"),
    ("копия объявления в коде", mirrored_declaration, MIRROR_BASELINE,
     "Значение объявлено в config/*.yaml и продублировано в коде под тем же именем: читай "
     "декларацию вместо копии — либо опусти потолок осознанно, --bless"),
    ("объявлено сервером, но сценарием не покрыто", declared_but_unscripted, UNSCRIPTED_BASELINE,
     "Новое объявление без сценария. Покрытие пишется ОБЪЯВЛЕНИЕМ в tests/scenarios/*.yaml "
     "(`call` + `expect.code`), новый python-скрипт для этого не нужен — либо --bless с объяснением"),
    ("объявлено ненаблюдаемым", facts_exempt_from_observation, FACT_EXEMPT_BASELINE,
     "Спросить реальность про этот факт нечем — и таких стало больше. Либо наблюдатель, либо "
     "инструмент, которого не хватает, чтобы наблюдатель стал возможен"),
    ("объявлено фактом, а слать некому", facts_without_emitter, FACT_EMITTER_BASELINE,
     "Тип обещан контрактом (или наблюдателем), а сервер его не шлёт: либо путь, который шлёт, "
     "либо снять строку из KNOWN_FACT_TYPES / tests/harness/observations.yaml"),
)


def _named_tree(argv: list[str]) -> Path | None:
    """`--root <путь>` — судить НАЗВАННОЕ дерево. Корень проверки и так принимают параметром;
    без флага он недостижим из командной строки, а значит и из объявления сценария."""
    for i, arg in enumerate(argv):
        if arg == "--root" and i + 1 < len(argv):
            return Path(argv[i + 1]).resolve()
        if arg.startswith("--root="):
            return Path(arg.split("=", 1)[1]).resolve()
    return None


def main() -> int:
    # Режим хука: молчим, когда чисто. Сторож, печатающий «всё хорошо» после каждой правки,
    # превращается в шум, и его перестают читать.
    quiet = "--hook" in sys.argv
    named = _named_tree(sys.argv)
    root = named or ROOT
    if named is not None and "--bless" in sys.argv:
        print("invariants: --bless по чужому дереву запрещён — потолок принадлежит СВОЕМУ дереву, "
              "и запись чужого числа сюда была бы тихой ложью", file=sys.stderr)
        return 2
    failed = False
    for title, check in HARD:
        notes = check(root)
        if not quiet:
            print(f"── {title}: {'чисто' if not notes else str(len(notes)) + ' шт.'}")
        elif notes:
            print(f"── {title}: {len(notes)} шт.")
        for note in notes:
            print(f"   ✗ {note}")
        failed = failed or bool(notes)

    bless = "--bless" in sys.argv
    for title, check, baseline, advice in RATCHETS:
        notes = check(root)
        if named is not None:
            # Потолок долга принадлежит своему дереву. Судить им чужое значит мерить чужой меркой;
            # число печатаем, вердикт по нему не выносим.
            print(f"── {title}: {len(notes)} (потолок не судим — дерево чужое)")
            continue
        limit = int(baseline.read_text(encoding="utf-8").strip()) if baseline.exists() else len(notes)
        if not quiet or len(notes) > limit:
            print(f"── {title}: {len(notes)} при потолке {limit}")
        if bless:
            baseline.write_text(f"{len(notes)}\n", encoding="utf-8")
            print(f"   потолок записан: {len(notes)}")
        elif len(notes) > limit:
            for note in notes:
                print(f"   ✗ {note}")
            print(f"   {advice}")
            failed = True
    return 0 if bless else (1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
