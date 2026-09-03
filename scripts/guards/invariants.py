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
LESSON_BASELINE = Path(__file__).with_name("lesson_debt_baseline.txt")
EVIDENCE_BASELINE = Path(__file__).with_name("lesson_evidence_baseline.txt")
DISPATCH_BASELINE = Path(__file__).with_name("dispatch_baseline.txt")
UNSCRIPTED_BASELINE = Path(__file__).with_name("unscripted_baseline.txt")
DEAD_RECOVERY_BASELINE = Path(__file__).with_name("dead_recovery_baseline.txt")
ORPHAN_BASELINE = Path(__file__).with_name("orphan_codes_baseline.txt")
MIRROR_BASELINE = Path(__file__).with_name("mirror_baseline.txt")
PRIVATE_LOADER_BASELINE = Path(__file__).with_name("private_loader_baseline.txt")
KNOB_BASELINE = Path(__file__).with_name("knob_reader_baseline.txt")
RECORD_FIELD_BASELINE = Path(__file__).with_name("record_field_baseline.txt")
ABSENT_KNOB_BASELINE = Path(__file__).with_name("absent_knob_baseline.txt")
STUB_BASELINE = Path(__file__).with_name("stub_baseline.txt")
MUTED_BASELINE = Path(__file__).with_name("muted_refusal_baseline.txt")
FACT_EMITTER_BASELINE = Path(__file__).with_name("fact_emitters_baseline.txt")
FACT_EXEMPT_BASELINE = Path(__file__).with_name("fact_exempt_baseline.txt")
SKILL_BOUNDARY_BASELINE = Path(__file__).with_name("skill_boundary_baseline.txt")
# Две ветки — это выбор, три и больше по одному значению — уже таблица.
DISPATCH_LIMIT = 3
# Диспетчеризацию не отменяет ни тип значения, ни имя вместо литерала: `if code == 404` и
# `if kind == SET` добавляют случай правкой КОДА ровно так же, как сравнение со строкой.
DISPATCH_VALUE = (ast.Constant, ast.Name, ast.Attribute)

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
                and isinstance(current.test.comparators[0], DISPATCH_VALUE)):
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
    for source in _python_sources(root):
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
            values = [ast.unparse(c.comparators[0]) for c in chain]
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


def _python_sources(root: Path) -> list[Path]:
    """Код СЕРВЕРА. Сторожа и тесты сюда не входят: они называют чужие имена, разбирая их."""
    files = list((root / "core").rglob("*.py")) + list((root / "tools").rglob("*.py")) + [root / "server.py"]
    return [p for p in sorted(files) if "__pycache__" not in str(p) and p.exists()]


def _named_literals(tree: ast.AST) -> list[tuple[str, ast.AST, int]]:
    """Что код связывает с ИМЕНЕМ: присваивание, дефолт параметра, именованный аргумент, `.get`.

    У `cfg.get("ключ", дефолт)` имя приходит СТРОКОЙ, а не идентификатором, поэтому три первые
    формы её не видят; при этом она опаснее прочих — пропадёт ключ в декларации, и код молча
    подставит свой дефолт вместо отказа.
    """
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
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "get" and len(node.args) == 2
              and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            out.append((node.args[0].value, node.args[1], node.lineno))
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


# Путь чтения точнее имени: `spec.get("scene_column", …)` называет ключ, который в декларациях
# не один, — по имени такое судить нельзя, а по пути `compensation.scene_column` можно.
CONFIG_ROOT_NAMES = ("config", "cfg", "data", "declaration")
_NOT_LITERAL = object()


def _declared_paths(root: Path) -> dict[tuple[str, ...], dict[str, object]]:
    """Каждый объявленный скаляр под ПОЛНЫМ путём: {(ключ,…): {файл:путь: значение}}."""
    out: dict[tuple[str, ...], dict[str, object]] = {}

    def walk(node: object, chain: tuple[str, ...], origin: str, text: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, chain + (str(key),), origin, f"{text}.{key}")
        elif isinstance(node, list):
            if chain and all(not isinstance(v, (dict, list)) for v in node):
                out.setdefault(chain, {})[f"{origin}:{text}"] = list(node)
            else:
                for i, value in enumerate(node):
                    walk(value, chain, origin, f"{text}[{i}]")
        elif node is not None and not isinstance(node, bool):
            out.setdefault(chain, {})[f"{origin}:{text}"] = node

    for declaration in sorted((root / "config").glob("*.yaml")):
        walk(yaml.safe_load(declaration.read_text(encoding="utf-8")) or {}, (), declaration.name, "")
    return out


def _receiver_path(node: ast.AST, assigns: dict[str, ast.AST], depth: int = 0) -> tuple[str, ...] | None:
    """Каким путём добыт приёмник `.get`: `()` — корень декларации, `None` — не декларация."""
    while isinstance(node, ast.BoolOp) and node.values:      # `cfg.get("раздел") or {}`
        node = node.values[0]
    if depth > 6:
        return None
    if isinstance(node, ast.Attribute) and node.attr in CONFIG_ROOT_NAMES:
        return ()
    if isinstance(node, ast.Name):
        if node.id in CONFIG_ROOT_NAMES:
            return ()
        origin = assigns.get(node.id)
        return _receiver_path(origin, assigns, depth + 1) if origin is not None else None
    key = _addressed_by(node)
    if key is None:
        return None
    outer = _receiver_path(node.func.value if isinstance(node, ast.Call) else node.value, assigns, depth + 1)
    return None if outer is None else outer + (key,)


def _config_reads(tree: ast.AST) -> list[tuple[tuple[str, ...], ast.AST, int]]:
    """`X.get("ключ", запасное)`, у которых путь приёмника разрешился до декларации."""
    assigns: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assigns.setdefault(node.targets[0].id, node.value)
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and len(node.args) == 2 and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            chain = _receiver_path(node.func.value, assigns)
            if chain is not None:
                out.append((chain + (node.args[0].value,), node.args[1], node.lineno))
    return out


def _literal(node: ast.AST) -> object:
    """Значение литерала. `frozenset({…})` — перечень, а не вызов: literal_eval его сам не берёт,
    и запасной список молча уходил бы от сторожа именно в той форме, в какой его чаще всего пишут."""
    inner = node.args[0] if (isinstance(node, ast.Call) and len(node.args) == 1
                             and getattr(node.func, "id", "") in ("frozenset", "set", "tuple", "list")) else node
    try:
        return ast.literal_eval(inner)
    except (ValueError, SyntaxError, TypeError):
        return _NOT_LITERAL


def mirrored_declaration(root: Path = ROOT) -> list[str]:
    """Код держит ВТОРУЮ копию объявленного факта: то же имя, то же значение.

    Литерал сам по себе не улика: поиск по одному лишь равенству значений тонет в шуме. Уликой его
    делает второй ИМЕНОВАННЫЙ источник, и называют его двумя способами. ПУТЬ ЧТЕНИЯ точнее:
    `lim.get("limit_column", "daily_limit")` под разделом `limits` обвиняется, даже когда имя ключа
    в декларациях не одно; по одному имени такое приходилось отпускать. Где путь приёмника не
    выводится (раздел приехал параметром), судит ИМЯ — ключ, называющий одно-единственное значение
    во всех декларациях. Расхождение имён при равном значении — совпадение (`status` = 403 против
    столбца таблицы), поэтому обвиняется только полное совпадение.
    """
    singular = _singular_declarations(root)
    paths = _declared_paths(root)
    if not singular and not paths:
        return []
    notes = []
    sources = _python_sources(root)
    for source in sources:
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        judged: set[tuple[int, str]] = set()
        for chain, node, line in _config_reads(tree):
            if len(chain) < 2:
                continue                      # ключ без раздела — это имя, а не путь: судит правило имени
            places = {where: value for c, decl in paths.items() if c[-len(chain):] == chain
                      for where, value in decl.items()}
            if len(places) != 1:
                continue                      # суффикс попал в несколько объявлений — улика неоднозначна
            (where, declared), = places.items()
            value = _literal(node)
            if value is not _NOT_LITERAL and _same_fact(value, declared):
                judged.add((line, chain[-1]))
                notes.append(f"{source.relative_to(root)}:{line} — `{'.'.join(chain)}` = {value!r:.60} "
                             f"повторяет объявление {where}: правка декларации молча разойдётся с кодом")
        for name, node, line in _named_literals(tree):
            if (line, name) in judged:
                continue
            bare = name.lower().strip("_")
            # Приставка снимается ТОЛЬКО когда точного имени нет: `default_unit` бывает и своим
            # ключом декларации, и запасным значением для ключа `unit` — сперва читаем буквально.
            fallback = False
            if bare not in singular:
                for prefix in ("default_", "fallback_"):
                    if bare.startswith(prefix):
                        bare, fallback = bare[len(prefix):], True
                        break
            if bare not in singular:
                continue
            value = _literal(node)
            if value is _NOT_LITERAL:
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




# Имя короче совпадает с чужим по случайности — тот же порог, что у копий объявления.
KNOB_NAME = 6
# Ключи схемы адресуются библиотекой целиком: `properties.color.pattern` никто по имени не грузит.
SCHEMA_SECTION = ("properties", "params")
# Ключ ЗАПИСИ — имя ручки: несколько слов через `_`. Ключ КАРТЫ — величина предметной области
# (`h264`, `float16`, `Encoder not found`), и по имени её не грузят по устройству.
KNOB_WORDS = re.compile(r"^[a-z][a-z0-9_]*$")


def _code_names(root: Path) -> set[str]:
    """Имена, которые код НАЗЫВАЕТ: строкой-значением либо обращением к полю.

    Считается по ДЕРЕВУ, а не по тексту: регулярка засчитывала читателем упоминание в комментарии,
    в докстринге и путь импорта (`from core.excel.excel_core import` — «обращение к полю»), а на
    `"couldn't start tunnel"` апостроф работал закрывающей кавычкой. Комментариев в дереве нет.
    """
    names: set[str] = set()
    for source in _python_sources(root):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
                names.add(node.value)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
    return names


def _knobs(root: Path) -> list[tuple[str, str, str, list[str], list[str]]]:
    """Ручки деклараций: (файл, путь, имя, соседи по секции, секции над ней).

    Ручка — ключ со скаляром или перечнем скаляров: именно его правят, чтобы изменить поведение.
    Перечень скаляров — ОДНА ручка под своим именем, спуск по элементам сделал бы ручками данные.
    """
    out: list[tuple[str, str, str, list[str], list[str]]] = []

    def leaf(node: object) -> bool:
        return not isinstance(node, dict) and not (isinstance(node, list)
                                                   and any(isinstance(v, (dict, list)) for v in node))

    def walk(node: object, origin: str, path: str, chain: list[str]) -> None:
        if isinstance(node, dict):
            siblings = [str(k) for k in node]
            for key, value in node.items():
                where = f"{path}.{key}"
                if leaf(value):
                    out.append((origin, where, str(key), siblings, chain))
                else:
                    walk(value, origin, where, chain + [str(key)])
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, origin, f"{path}[{i}]", chain)

    for declaration in sorted((root / "config").glob("*.yaml")):
        walk(yaml.safe_load(declaration.read_text(encoding="utf-8")) or {}, declaration.name, "", [])
    return out


def _declaration_values(root: Path) -> set[str]:
    """Строки, стоящие в декларациях ЗНАЧЕНИЕМ: такое имя — величина предметной области."""
    values: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            values.add(node)

    for declaration in sorted((root / "config").rglob("*.yaml")):
        walk(yaml.safe_load(declaration.read_text(encoding="utf-8")) or {})
    return values


def knob_without_reader(root: Path = ROOT) -> list[str]:
    """Ручка объявлена, а грузить её некому: правка строки не меняет ПОВЕДЕНИЯ.

    Обвиняется не всякий неупомянутый ключ — так набирается шум. Уликой ключ делает ЗАПИСЬ вокруг:
    её ключи — имена ручек (`log_blocked`, `scene_column`), а у карты ключами приходят величины
    предметной области (`h264`, `float16`, `Encoder not found`), и в коде их не бывает по
    устройству. Плюс хотя бы один сосед, названный кодом: секция, которой не читают вовсе, не
    судится — улики нет. Ключ, стоящий в декларациях ещё и значением, — тоже величина, не ручка.

    Честный предел: читателем считается ЛЮБОЙ модуль, назвавший имя, даже если он грузит другую
    декларацию — `log_file` в `firewall.yaml` «читает» `core/runner/supervisor.py`. Поэтому улику
    даёт форма ключа, а не доля читаемых соседей: от чужого однофамильца она не зависит.
    """
    if not (root / "config").is_dir():
        return []
    names = _code_names(root)
    values = _declaration_values(root)
    notes = []
    for origin, where, key, siblings, chain in _knobs(root):
        if len(key) < KNOB_NAME or key in names or key in values:
            continue
        if any(section in SCHEMA_SECTION for section in chain):
            continue
        if "_" not in key or not all(KNOB_WORDS.match(s) for s in siblings):
            continue
        read = [s for s in siblings if s != key and s in names]
        if not read:
            continue
        notes.append(f"config/{origin}{where} — ручку никто не грузит, а соседей по секции "
                     f"({', '.join(read[:3])}) код читает: правка этой строки не меняет ничего")
    return notes


# Голос обработчика: он поднимает своё, называет пойманное или оставляет след. Ничего из этого —
# отказ погашен, и наверх уходит пустота, неотличимая от честного «данных нет».
SPEAKS = ("err", "log", "print", "warn", "trail", "refus")
BROAD = {"Exception", "BaseException"}


def muted_refusal(root: Path = ROOT) -> list[str]:
    """Широкий `except`, гасящий отказ молча: сбой уезжает наверх пустым результатом.

    Узкий перехват (`OSError` на чтении `/proc`, `ImportError` у необязательной библиотеки) —
    решение о конкретной причине, и он сюда не попадает. Улику даёт именно ШИРИНА: `Exception`
    ловит и опечатку в своём коде, и обрыв диска, а клиент получает «пусто» и считает её ответом.
    """
    notes = []
    for source in _python_sources(root):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            caught = {ast.unparse(part) for part in
                      (node.type.elts if isinstance(node.type, ast.Tuple) else [node.type])}
            if not caught & BROAD:
                continue
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            calls = {ast.unparse(n.func).lower() for n in ast.walk(node) if isinstance(n, ast.Call)}
            if (any(isinstance(n, ast.Raise) for n in ast.walk(node))
                    or (node.name and node.name in names)
                    or any(word in call for call in calls for word in SPEAKS)):
                continue
            notes.append(f"{source.relative_to(root)}:{node.lineno} — широкий `except` гасит отказ "
                         "молча: наверх уходит пустота, и клиент считает её ответом")
    return notes


def _declaration_keys(root: Path) -> set[str]:
    """Все имена ключей деклараций — и разделов, и ручек."""
    keys: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                keys.add(str(key))
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for declaration in sorted((root / "config").rglob("*.yaml")):
        walk(yaml.safe_load(declaration.read_text(encoding="utf-8")) or {})
    return keys


def _addressed_by(node: ast.AST) -> str | None:
    """Ключ, которым добыт САМ приёмник: `cfg.get("раздел", {})` либо `cfg["раздел"]`."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            and node.args and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)):
        return node.args[0].value
    if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)):
        return str(node.slice.value)
    return None


def default_instead_of_declaration(root: Path = ROOT) -> list[str]:
    """Ключ читают с дефолтом, а в декларации его нет: значение живёт в коде, конфиг бессилен.

    Зеркало «ручки без читателя»: там объявление без читателя, здесь читатель без объявления.
    Обвиняется не всякий `.get(имя, дефолт)` — таких на дереве 64, и это заголовки HTTP, поля
    JSON-RPC, параметры инструментов и разбор `/proc`, то есть данные запроса, а не конфигурация.
    Уликой чтение делают два условия разом: модуль называет файл декларации СТРОКОЙ (в комментарии
    имя ничего не грузит), а приёмник добыт ОБЪЯВЛЕННЫМ ключом — читают раздел декларации и
    спрашивают в нём строку, которой там нет. Дефолт обязан быть литералом: `.get("раздел", {})` —
    это спуск по декларации, а не ручка.
    """
    declarations = [f.name for f in sorted((root / "config").glob("*.yaml"))]
    if not declarations:
        return []
    keys = _declaration_keys(root)
    notes = []
    for source in _python_sources(root):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        strings = [n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        if not any(name in text for text in strings for name in declarations):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and len(node.args) == 2):
                continue
            key, default = node.args
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            if not isinstance(default, ast.Constant) or key.value in keys:
                continue
            section = _addressed_by(node.func.value)
            if section is None or section not in keys:
                continue
            notes.append(f"{source.relative_to(root)}:{node.lineno} — `{key.value}` спрашивают "
                         f"у раздела `{section}` с дефолтом {default.value!r}, а строки такой в "
                         f"декларациях нет: значение живёт в коде, правка конфига бессильна")
    return notes


def declaration_loaded_privately(root: Path = ROOT) -> list[str]:
    """Своя загрузка YAML в модуле, который НАЗЫВАЕТ декларацию: у отказа заводится своя политика.

    Замерено прогоном: шесть загрузчиков давали шесть разных ответов на одно и то же — «файла нет»
    и «файл битый», — и два из них молча ослабляли защиту, а три отдавали сырой `ParserError` мимо
    контракта. Дверь одна (`core/declaration.py`): отсутствие — `TEMPLATE_NOT_FOUND` либо решение
    вызывающего (`optional`), битость — всегда `SCHEMA_INVALID`. Улику даёт ПАРА: модуль называет
    файл декларации строкой И разбирает YAML сам.
    """
    declarations = [f.name for f in sorted((root / "config").glob("*.yaml"))]
    if not declarations:
        return []
    notes = []
    for source in _python_sources(root):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        strings = [n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        named = [d for d in declarations if any(d in text for text in strings)]
        if not named:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "safe_load"):
                notes.append(f"{source.relative_to(root)}:{node.lineno} — своя загрузка YAML в "
                             f"модуле, который называет {named[0]}: у отказа появится своя политика")
    return notes


def _raises_name(node: ast.Raise) -> str:
    """Имя исключения у `raise X` и `raise X(...)` — иначе форма с аргументом уходит незамеченной."""
    exc = node.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Attribute):
        return exc.attr
    return ""


def unfinished_in_server(root: Path = ROOT) -> list[str]:
    """Незавершённое, ОБЪЯВЛЕННОЕ в серверном коде: `raise NotImplementedError`.

    Кричать о незавершённом — правило проекта, и храповик его не отменяет: он требует, чтобы
    прибавление кричащего было РЕШЕНИЕМ (`--bless`), а не строкой, которую никто не заметил.
    Тихая заглушка сюда не попадает по устройству — на то она и тихая; поэтому ноль на этой оси
    означает «незавершённого не объявлено», а не «незавершённого нет».
    """
    notes = []
    for source in _python_sources(root):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and _raises_name(node) == "NotImplementedError":
                notes.append(f"{source.relative_to(root)}:{node.lineno} — объявлено незавершённым: "
                             "стаб остаётся решением, а не строкой между делом")
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
    # Имя с подчёркивания — модуль-помощник, а не хук: его никто не зовёт событием, и требовать
    # ему объявления значит требовать объявить `_stamp.py` сторожем. Та же договорённость по дереву.
    present = {f"{'/'.join(HOOKS)}/{p.name}" for p in (directory.iterdir() if directory.is_dir() else ())
               if p.is_file() and not p.name.startswith((".", "_"))}
    notes = [f"{rel} объявлен в {'/'.join(HOOK_SETTINGS)}, а файла нет — событие приходит, "
             "запускать нечего, и отказ выглядит как молчание"
             for rel in sorted(declared - present)]
    notes += [f"{rel} лежит в дереве, а объявления в {'/'.join(HOOK_SETTINGS)} у него нет — "
              "код есть, срабатывать ему не на чем"
              for rel in sorted(present - declared)]
    return notes


LESSONS = "hard-won-lessons.md"
LESSON = re.compile(r"^- \*\*(.+?)\*\*", re.M)
EXECUTOR = re.compile(r"⟨исполняет:\s*(.+?)⟩")
EVIDENCE = re.compile(r"⟨улика:\s*⟦vpm\s+([0-9a-f]{4})[^⟧]*⟧⟩")
SCENARIO_NAME = re.compile(r"^- scenario:\s*(\S+)", re.M)
# Механизм ловит повтор ошибки сам; скилл и «НЕТ» — это слова, и они считаются ДОЛГОМ.
MECHANISM = ("ось ", "проверка ", "сценарий ")


def _lesson_blocks(memory: Path) -> list[tuple[str, str]]:
    """(заголовок урока, весь его кусок текста). Разбор один на все три оси — иначе они разойдутся."""
    path = memory / LESSONS
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    границы = [m.start() for m in LESSON.finditer(text)] + [len(text)]
    return [(LESSON.match(text[начало:границы[i + 1]]).group(1), text[начало:границы[i + 1]])
            for i, начало in enumerate(границы[:-1])]


def _lessons(memory: Path) -> list[tuple[str, str]]:
    """(заголовок урока, объявленный исполнитель). Урок без метки отдаёт пустого исполнителя."""
    уроки = []
    for имя, кусок in _lesson_blocks(memory):
        метка = EXECUTOR.search(кусок)
        уроки.append((имя, метка.group(1).strip() if метка else ""))
    return уроки


def _suite_text(root: Path) -> str:
    """Свод исходников наборов: метки проверок живут строками в них, а не отдельным реестром."""
    куски = []
    for путь in sorted((root / "tests").rglob("*.py")):
        try:
            куски.append(путь.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(куски)


def _mechanisms(root: Path) -> set[str]:
    """Что вообще существует как исполнитель: имена осей, сценариев и скилов."""
    имена = {имя for имя, _ in HARD} | {строка[0] for строка in RATCHETS}
    сценарии = _at(root, ("tests", "scenarios"))
    if сценарии.is_dir():
        for path in сценарии.glob("*.yaml"):
            имена |= set(SCENARIO_NAME.findall(path.read_text(encoding="utf-8", errors="replace")))
    skills = _at(root, (".claude", "skills"))
    if skills.is_dir():
        имена |= {p.name for p in skills.iterdir() if (p / "SKILL.md").exists()}
    return имена


def lesson_without_executor(root: Path = ROOT, memory: Path | None = None) -> list[str]:
    """Урок обязан называть ИСПОЛНИТЕЛЯ, и тот обязан существовать.

    Урок, за которым не стоит механизм, повтор ошибки не ловит: он лежит прозой и пылится. Здесь
    судится только пара «названо ↔ существует»; долг «механизма нет вовсе» считает храповик рядом.
    """
    home = memory or memory_dir(root)
    if not (home / LESSONS).exists():
        return []
    известные = _mechanisms(root)
    notes = []
    for имя, исполнитель in _lessons(home):
        if not исполнитель:
            notes.append(f"{LESSONS}: урок «{имя[:60]}» не называет исполнителя — повтор ошибки "
                         f"он не ловит, а пылится")
            continue
        if исполнитель.startswith("проверка "):
            # Метка проверки — не имя из реестра, а строка, которую печатает набор: ищем её ТАМ,
            # где она живёт, иначе пришлось бы держать второй список из девятисот меток.
            метка = исполнитель.split("«", 1)[-1].rstrip("»")
            if метка not in _suite_text(root):
                notes.append(f"{LESSONS}: урок «{имя[:50]}» зовёт проверку «{метка[:40]}», которой "
                             f"нет ни в одном наборе")
            continue
        названо = исполнитель.split("`")[1] if "`" in исполнитель else исполнитель.split(" ", 1)[-1]
        if исполнитель != "НЕТ" and названо not in известные:
            notes.append(f"{LESSONS}: урок «{имя[:50]}» зовёт исполнителя `{названо}`, которого "
                         f"нет ни среди осей, ни среди сценариев, ни среди скилов")
    return notes


def lesson_without_evidence(root: Path = ROOT, memory: Path | None = None) -> list[str]:
    """Долг: урок без ключа улики — по нему не поднять прогон, в котором он родился.

    Ключ ведёт в запись подписи: намерение, ожидание, факт, HEAD и команда повтора. Журнал подписей
    не под git, поэтому нет журнала вовсе — «улики нет» и обвинять некого; есть журнал, но ключа в
    нём нет — это находка: метка ссылается в пустоту, что хуже её отсутствия.
    """
    home = memory or memory_dir(root)
    if not (home / LESSONS).exists():
        return []
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _stamp
    журнал = list(root.joinpath(*_stamp.JOURNAL).glob("stamps-*.jsonl"))
    notes = []
    for имя, кусок in _lesson_blocks(home):
        ключ = EVIDENCE.search(кусок)
        if not ключ:
            notes.append(f"{LESSONS}: «{имя[:70]}» — ключа улики нет, прогон не поднять")
        elif журнал and not _stamp.find(ключ.group(1), root):
            notes.append(f"{LESSONS}: «{имя[:50]}» ссылается на ключ {ключ.group(1)}, которого нет "
                         f"в журнале подписей — метка ведёт в пустоту")
    return notes


def lesson_without_mechanism(root: Path = ROOT, memory: Path | None = None) -> list[str]:
    """Долг: урок, за которым стоят только слова (скилл или «НЕТ»), а не ловящий механизм."""
    home = memory or memory_dir(root)
    if not (home / LESSONS).exists():
        return []
    return [f"{LESSONS}: «{имя[:70]}» — исполнитель {исполнитель or 'не назван'}"
            for имя, исполнитель in _lessons(home)
            if not исполнитель.startswith(MECHANISM)]


MEMORY_INDEX = "MEMORY.md"
# Указатель индекса: `- [Заголовок](файл.md) — крючок`. Берём только адрес.
MEMORY_LINK = re.compile(r"\]\(([^)]+\.md)\)")
MEMORY_NAME = re.compile(r"^name:\s*(\S+)\s*$", re.M)


def memory_dir(root: Path = ROOT) -> Path:
    """Слуг памяти из корня. Дефисом становится ЛЮБОЙ не-буквенно-цифровой знак, а не только `/`:
    правило `/`→`-` промахивалось мимо каталога на любом пути с подчёркиванием, и оба механизма
    молча судили пустоту (проверено на всех каталогах `~/.claude/projects`)."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root)) / "memory"


def memory_off_index(root: Path = ROOT, memory: Path | None = None) -> list[str]:
    """Память живёт в двух местах — файлы на диске и указатели в `MEMORY.md`, — и они обязаны сойтись.

    Каталога нет (CI, чужая машина) — «улики нет», а не «памяти ноль»: чужое отсутствие не наша
    находка. Здесь же судится версионирование: память без git не имеет ни истории, ни отката, и
    «обновил память» остаётся словом.
    """
    home = memory or memory_dir(root)
    if not home.is_dir():
        return []
    notes = []
    if not (home / ".git").exists():
        notes.append(f"{home.name}: память вне версий — ни истории, ни отката, ни гейта; "
                     f"«обновил память» проверяется только словом (`git init` в каталоге памяти)")
    index = home / MEMORY_INDEX
    if not index.exists():
        return notes + [f"{MEMORY_INDEX} отсутствует — указателя на память нет, и каждый файл "
                        f"придётся открывать наугад"]
    listed = set(MEMORY_LINK.findall(index.read_text(encoding="utf-8")))
    # Снятое лежит в `_archive/` намеренно и в указателе не значится — это не расхождение.
    on_disk = {p.name for p in home.glob("*.md") if p.name != MEMORY_INDEX}
    notes += [f"{name} лежит в памяти, а указателя в {MEMORY_INDEX} у него нет — файл найдут "
              f"только перебором" for name in sorted(on_disk - listed)]
    notes += [f"{MEMORY_INDEX} указывает на `{name}`, которого нет — указатель ведёт в пустоту"
              for name in sorted(listed - on_disk)]
    for path in sorted(home.glob("*.md")):
        if path.name == MEMORY_INDEX:
            continue
        found = MEMORY_NAME.search(path.read_text(encoding="utf-8"))
        if not found:
            notes.append(f"{path.name}: нет `name:` в шапке — связи `[[имя]]` в него не ведут")
        elif found.group(1) != path.stem:
            notes.append(f"{path.name}: шапка зовётся `{found.group(1)}`, а файл — `{path.stem}`; "
                         f"связь `[[{found.group(1)}]]` ведёт мимо файла")
    return notes


JOURNAL = ("docs", "roadmap", "_sessions.md")
ARCHIVE = ("docs", "roadmap", "sessions")
# Строка указателя: `- \`s25.md:30\` · Сессия 25 (доп. 101) — …`
POINTER = re.compile(r"^- `([\w.]+):(\d+)` · (.+)$", re.M)


def journal_off_index(root: Path = ROOT) -> list[str]:
    """Закрытые записи журнала лежат отдельно, а указатель на них — в `_sessions.md`; они обязаны сойтись.

    Указатель адресует запись СТРОКОЙ (`файл:строка`), поэтому стареет он молча: дописал абзац в
    архив — и все адреса ниже поехали, а читатель попадёт в середину чужой записи и не заметит.
    Судится ровно это: файл существует, строка та самая, заголовок совпадает дословно. Архива нет
    (старое дерево, чужая машина) — «улики нет», а не «журнал пуст».
    """
    archive = _at(root, ARCHIVE)
    journal = _at(root, JOURNAL)
    if not archive.is_dir() or not journal.exists():
        return []
    pointers = POINTER.findall(journal.read_text(encoding="utf-8"))
    notes = []
    listed: dict[str, set[str]] = {}
    for name, line, head in pointers:
        listed.setdefault(name, set()).add(head)
        path = archive / name
        if not path.exists():
            notes.append(f"указатель зовёт `sessions/{name}`, которого нет — адрес ведёт в пустоту")
            continue
        rows = path.read_text(encoding="utf-8").splitlines()
        n = int(line)
        if not (1 <= n <= len(rows)) or rows[n - 1] != f"### {head}":
            было = rows[n - 1][:60] if 1 <= n <= len(rows) else "за концом файла"
            notes.append(f"указатель ведёт на `{name}:{n}` за записью «{head[:50]}», а там «{было}» "
                         f"— адрес поехал, и читатель попадёт в середину чужой записи")
    for path in sorted(archive.glob("*.md")):
        if path.name not in listed:
            notes.append(f"sessions/{path.name} лежит в архиве, а строк указателя у него нет — "
                         f"записи найдут только перебором")
            continue
        heads = {row[4:] for row in path.read_text(encoding="utf-8").splitlines()
                 if row.startswith("### ")}
        notes += [f"sessions/{path.name}: запись «{head[:60]}» в архиве есть, в указателе нет"
                  for head in sorted(heads - listed[path.name])]
    return notes

SKILLS_CATALOG = (".claude", "skills", "CATALOG.md")
SKILL_ROW = re.compile(r"^\| `([a-z][a-z-]+)` \|([^|]*)\|([^|]*)\|", re.M)


def skill_without_zone(root: Path = ROOT) -> list[str]:
    """Скил, чья зона не объявлена, — и граница, названная в одну сторону.

    Три находки, а не одна: скил без строки каталога (зона не объявлена вовсе), строка без скила
    (каталог зовёт снесённое) и односторонняя граница. Последняя опаснее прочих: сосед, о котором
    сказали, но который промолчал в ответ, считает зону своей — и оба развиваются в одну область,
    пока это не всплывёт выбором не того скила.
    """
    каталог = _at(root, SKILLS_CATALOG)
    дерево = _at(root, (".claude", "skills"))
    if not каталог.exists() or not дерево.is_dir():
        return []
    на_диске = {p.name for p in дерево.iterdir() if (p / "SKILL.md").exists()}
    строки = {имя: f"{зона} {границы}"
              for имя, зона, границы in SKILL_ROW.findall(каталог.read_text(encoding="utf-8"))}
    notes = [f"{имя}: зона не объявлена в {'/'.join(SKILLS_CATALOG)} — при выборе скила границы нет"
             for имя in sorted(на_диске - set(строки))]
    notes += [f"{'/'.join(SKILLS_CATALOG)} зовёт `{имя}`, которого на диске нет"
              for имя in sorted(set(строки) - на_диске)]
    for имя in sorted(на_диске & set(строки)):
        соседи = {n for n in на_диске if n != имя and re.search(rf"`{re.escape(n)}`", строки[имя])}
        notes += [f"{имя} → {сосед}: граница названа в ОДНУ сторону, обратной нет — сосед считает "
                  f"зону своей" for сосед in sorted(соседи)
                  if сосед in строки and not re.search(rf"`{re.escape(имя)}`", строки[сосед])]
    return notes

# Слово, встречающееся у половины библиотеки, признаком не является: метр, считающий `python`
# и `server` за сходство, назвал бы путаемыми ВСЕ пары и был бы выключен в первый день.
ФОН_ДОЛЯ = 0.5
# Ниже порога совпадают служебные слова, а не предмет: замер дал 22 пары при 6, 35 при 3.
БЛИЗОСТЬ = 6
СЛОВО = re.compile(r"[a-zа-яё_]{5,}")


def _описания(root: Path) -> dict[str, str]:
    """`description` каждого скила — единственное, что читается в МОМЕНТ выбора."""
    дерево = _at(root, (".claude", "skills"))
    из_диска = {}
    for каталог in sorted(дерево.iterdir()) if дерево.is_dir() else []:
        файл = каталог / "SKILL.md"
        if not файл.exists():
            continue
        m = re.search(r"^description:\s*(.+?)(?=^\w+:|^---)", файл.read_text(encoding="utf-8"),
                      re.S | re.M)
        из_диска[каталог.name] = " ".join((m.group(1) if m else "").split())
    return из_диска


def skill_boundary_invisible(root: Path = ROOT) -> list[str]:
    """Пара скилов путаема по существу, а граница между ними объявлена только в каталоге.

    Каталог в момент выбора скила не загружается — его читают ось и человек. Граница, живущая
    лишь там, при выборе не существует, и обе стороны считают зону своей.
    """
    описания = _описания(root)
    каталог = _at(root, SKILLS_CATALOG)
    if len(описания) < 2 or not каталог.exists():
        return []
    строки = {имя: f"{зона} {границы}"
              for имя, зона, границы in SKILL_ROW.findall(каталог.read_text(encoding="utf-8"))}
    частота: dict[str, int] = {}
    for текст in описания.values():
        for слово in set(СЛОВО.findall(текст.lower())):
            частота[слово] = частота.get(слово, 0) + 1
    фон = {с for с, n in частота.items() if n >= len(описания) * ФОН_ДОЛЯ}
    notes = []
    for имя in sorted(описания):
        for сосед in sorted(описания):
            if сосед <= имя:
                continue
            if not (re.search(rf"`{re.escape(сосед)}`", строки.get(имя, ""))
                    or re.search(rf"`{re.escape(имя)}`", строки.get(сосед, ""))):
                continue
            общее = ((set(СЛОВО.findall(описания[имя].lower()))
                      & set(СЛОВО.findall(описания[сосед].lower()))) - фон)
            взаимно = сосед in описания[имя] and имя in описания[сосед]
            if len(общее) >= БЛИЗОСТЬ and not взаимно:
                notes.append(f"{имя} ↔ {сосед}: общего в описаниях {len(общее)} слов, а граница "
                             f"названа только в каталоге — при выборе скила её нет")
    return notes


PRECOMMIT = (".pre-commit-config.yaml",)
INSTALL = ("install.sh",)


def door_not_installed(root: Path = ROOT) -> list[str]:
    """Дверь коммита объявлена, а установка её не ставит.

    `.git/hooks/` не под git, поэтому на свежем клоне двери нет вовсе — и её отсутствие выглядит
    как чистый проход: локально не судит никто, CI ловит уже после `push`.
    """
    config, install = _at(root, PRECOMMIT), _at(root, INSTALL)
    if not config.exists() or not install.exists():
        return []
    if "pre-commit install" in install.read_text(encoding="utf-8", errors="replace"):
        return []
    return [f"{'/'.join(PRECOMMIT)} объявляет дверь коммита, а {'/'.join(INSTALL)} её не ставит — "
            f"на свежем клоне двери нет, и это неотличимо от пройденных проверок"]


# Хвосты, гасящие вердикт: `|| true` обнуляет код возврата, перенаправление уводит поток.
HOOK_MUFFLE = ("2>/dev/null", "2>&1", "|| true", "; true")


def hook_declared_muted(root: Path = ROOT) -> list[str]:
    """Хук объявлен так, что сказать о находке он не может.

    К модели ведут ровно два канала — `exit 2` со `stderr` и JSON `additionalContext` на stdout.
    Хвост в команде рвёт первый и прячет трейс: упавший сторож становится неотличим от
    промолчавшего, а сторож без голоса — отсутствующий сторож, который выглядит работающим.
    """
    settings = _at(root, HOOK_SETTINGS)
    if not settings.exists():
        return []
    try:
        declared = json.loads(settings.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []                        # разбор объявления судит соседняя ось, здесь улики нет
    notes = []
    for event, blocks in (declared.get("hooks") or {}).items():
        for block in blocks if isinstance(blocks, list) else ():
            for hook in block.get("hooks", []):
                command = str(hook.get("command", ""))
                if "/".join(HOOKS) not in command:
                    continue
                muffle = [tail for tail in HOOK_MUFFLE if tail in command]
                if muffle:
                    name = Path(command.split()[1] if " " in command else command).name
                    notes.append(f"{event}: {name} объявлен с {muffle} — вердикт не доходит ни до "
                                 f"модели, ни до человека, и молчание выглядит чистым результатом")
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


GUARD_HOME = re.compile(r"scripts/guards/([\w.]+\.py)")


def zone_declared_twice(root: Path = ROOT) -> list[str]:
    """Одну зону объявили два хозяина — правило «не плодить» перестало действовать.

    Зона отвечает на «что покрывает ТОЛЬКО он», и второй претендент на ту же территорию и есть
    размножение, ради запрета которого каталог заведён. Личность зоны — назван ли в ней сторож
    (два дома у одного сторожа), а если не назван — сам текст зоны. Переписанный своими словами
    дубль так не ловится, и это честный предел: близость формулировок машина не судит.
    """
    notes = []
    for catalog in (_at(root, CATALOG), _at(root, GUARDS_CATALOG)):
        if not catalog.exists():
            continue
        owners: dict[str, list[str]] = {}
        for line in catalog.read_text(encoding="utf-8").splitlines():
            cells = line.split("|")
            if not line.startswith("|") or len(cells) < 3:
                continue
            name = cells[1].strip().strip("*").strip("`").strip()
            if not (name.endswith(".py") or name.endswith("/")):
                continue
            named = sorted(set(GUARD_HOME.findall(cells[2])))
            owners.setdefault(", ".join(named) if named else " ".join(cells[2].split()),
                              []).append(name)
        for zone, claimants in owners.items():
            if len(claimants) > 1:
                notes.append(f"{catalog.relative_to(root)}: зону `{zone[:60]}` объявили "
                             f"{', '.join(claimants)} — у одной правды два хозяина, "
                             f"и один из них всегда отстанет")
    return notes


def guard_without_home(root: Path = ROOT) -> list[str]:
    """Сторож, которого не судит ни один набор: его правку цикл сравнить не может.

    Дом объявляется зоной в `tests/CATALOG.md` — строкой набора, называющей `scripts/guards/*.py`;
    оттуда же `what_if.py` берёт третью карту сравнения. Обратная сторона так же красная:
    объявленный дом несуществующего сторожа отправляет цикл сравнивать пустоту.
    """
    directory, catalog = _at(root, GUARDS), _at(root, CATALOG)
    if not directory.is_dir() or not catalog.exists():
        return []
    on_disk = {p.name for p in directory.glob("*.py") if not p.name.startswith("_")}
    housed: set[str] = set()
    for line in catalog.read_text(encoding="utf-8").splitlines():
        cells = line.split("|")
        if line.startswith("|") and len(cells) >= 3:
            housed |= set(GUARD_HOME.findall(cells[2]))
    return ([f"scripts/guards/{name} — сторожа не судит ни один набор: правку такого сторожа "
             f"цикл `what_if.py` не с чем сравнить, третья карта его не видит"
             for name in sorted(on_disk - housed)]
            + [f"tests/CATALOG.md называет домом сторожа `scripts/guards/{name}`, которого нет — "
               f"карта цикла ведёт в пустоту" for name in sorted(housed - on_disk)])


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

HARD = (("одну зону объявили два хозяина", zone_declared_twice),
        ("сторож без набора-дома", guard_without_home),
        ("пропуск набора без покрытия в CI", skips_without_ci),
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
        ("объявление глушит голос хука", hook_declared_muted),
        ("дверь коммита объявлена, но не ставится", door_not_installed),
        ("память разошлась со своим указателем", memory_off_index),
        ("журнал разошёлся со своим указателем", journal_off_index),
        ("скил без объявленной зоны", skill_without_zone),
        ("урок зовёт несуществующего исполнителя", lesson_without_executor),
        ("объявление наблюдения неполно", observation_incomplete),
        ("факт эмитится, а решения о наблюдении нет", facts_without_observer))

# Храповик: вниз можно, вверх нет. Потолок — в файле рядом, совет — как долг закрывается.

# Кто читает запись следа/журнала и кто её СОБИРАЕТ. Списки поимённые, а не «все файлы»: `.get`
# по строке встречается всюду, и обвинять каждый значило бы выключить сторожа в первый же день.
RECORD_READERS = ("scripts/guards/reproduce.py",)
RECORD_WRITERS = ("tests/harness/scenario.py",)
# Имена, которыми в этих файлах зовут САМУ запись. Прочие `.get` читают объявления, а не запись.
RECORD_RECEIVERS = {"entry", "e", "rec", "record"}


def _record_fields() -> set[str]:
    """Объявленные поля записи — из модели, а не вторым списком рядом."""
    sys.path.insert(0, str(ROOT))
    from core.contracts.trail_record import RunSummary, TrailRecord
    return set(TrailRecord.model_fields) | set(RunSummary.model_fields)


def record_field_mismatch(root: Path = ROOT) -> list[str]:
    """У записи спрашивают или в неё кладут поле, которого в объявлении нет.

    Промах здесь НЕМОЙ по природе: `dict.get` вернёт `None`, и читатель примет отсутствие улики за
    отсутствие в реальности. Мутация «переименовать `reaction_class`» когда-то оставляла всё
    зелёным; писателя сервера теперь судит mypy по модели, а харнесс модель не импортирует
    намеренно — он судит сервер снаружи, — поэтому его сторону и сторону читателя судит эта ось.
    """
    fields = _record_fields()
    notes = []
    for rel in RECORD_READERS:
        source = root / rel
        if not source.exists():
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in RECORD_RECEIVERS and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value not in fields):
                notes.append(f"{rel}:{node.lineno} — у записи спрашивают поле "
                             f"`{node.args[0].value}`, которого нет в объявлении "
                             f"(core/contracts/trail_record.py)")
    for rel in RECORD_WRITERS:
        source = root / rel
        if not source.exists():
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            # Запись узнаётся по ПАРЕ ключей, а не по имени переменной: словарь ответа сервера
            # (`ok`/`code`/`facts`) записью не является, и обвинять его нельзя.
            if isinstance(node, ast.Dict):
                keys = {k.value for k in node.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if {"scenario", "ok"} <= keys:
                    notes += [f"{rel}:{node.lineno} — в запись кладут поле `{key}`, которого нет "
                              f"в объявлении (core/contracts/trail_record.py)"
                              for key in sorted(keys - fields)]
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "write"):
                named = {kw.arg for kw in node.keywords if kw.arg}
                if "scenario" in named:
                    notes += [f"{rel}:{node.lineno} — в запись кладут поле `{key}`, которого нет "
                              f"в объявлении (core/contracts/trail_record.py)"
                              for key in sorted(named - fields)]
    return notes


RATCHETS = (
    ("урок без ключа улики", lesson_without_evidence, EVIDENCE_BASELINE,
     "Урок не ведёт в прогон, где родился: подпиши наблюдение (`_stamp.py`) и поставь ключ "
     "меткой ⟨улика: ⟦vpm КЛЮЧ⟧⟩ — или объясни в ревью, почему улики быть не может (--bless)"),
    ("урок без механизма", lesson_without_mechanism, LESSON_BASELINE,
     "Урок держится словами: заведи ось/проверку/сценарий, который ловит повтор — "
     "или объясни в ревью, почему механизма быть не может (--bless)"),
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
    ("ручка объявлена, а читателя нет", knob_without_reader, KNOB_BASELINE,
     "Ключ в config/*.yaml не грузит НИКТО: правка строки не меняет поведения. Либо читатель, "
     "либо снять строку — украшение хуже пустого места, оно обещает управление, которого нет"),
    ("отказ погашен молча", muted_refusal, MUTED_BASELINE,
     "Широкий `except` превратил сбой в пустой результат: назови причину узким перехватом, "
     "либо отдай отказ кодом реестра, либо оставь след — молчание клиенту неотличимо от данных"),
    ("граница скила невидима при выборе", skill_boundary_invisible, SKILL_BOUNDARY_BASELINE,
     "Пара путаема по существу, а граница объявлена только в каталоге: назови соседа в ОБОИХ "
     "`description` — каталог в момент выбора не читается — либо опусти потолок осознанно, --bless"),
    ("объявлено незавершённым", unfinished_in_server, STUB_BASELINE,
     "Незавершённого стало больше. Кричать о нём правильно, но прибавление — "
     "решение: либо доделать, либо --bless с объяснением, почему стаб остаётся"),
    ("конфигурация, которой нет: ключ читают с дефолтом", default_instead_of_declaration,
     ABSENT_KNOB_BASELINE,
     "Читают раздел декларации и спрашивают строку, которой в нём нет: либо объяви её в "
     "config/*.yaml, либо не притворяйся конфигурацией — именованная константа честнее"),
    ("объявлено сервером, но сценарием не покрыто", declared_but_unscripted, UNSCRIPTED_BASELINE,
     "Новое объявление без сценария. Покрытие пишется ОБЪЯВЛЕНИЕМ в tests/scenarios/*.yaml "
     "(`call` + `expect.code`), новый python-скрипт для этого не нужен — либо --bless с объяснением"),
    ("объявлено ненаблюдаемым", facts_exempt_from_observation, FACT_EXEMPT_BASELINE,
     "Спросить реальность про этот факт нечем — и таких стало больше. Либо наблюдатель, либо "
     "инструмент, которого не хватает, чтобы наблюдатель стал возможен"),
    ("поле записи мимо объявления", record_field_mismatch, RECORD_FIELD_BASELINE,
     "У записи следа/журнала спрашивают или в неё кладут поле, которого нет в "
     "core/contracts/trail_record.py. Промах немой: `dict.get` вернёт None, и отсутствие улики "
     "сойдёт за отсутствие в реальности — объяви поле либо спрашивай объявленным именем"),
    ("своя загрузка декларации мимо общей двери", declaration_loaded_privately, PRIVATE_LOADER_BASELINE,
     "Модуль разбирает YAML сам, и у отказа заводится своя политика: шесть загрузчиков давали "
     "шесть ответов на «нет файла» и «битый файл». Читай через core/declaration.py"),
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
