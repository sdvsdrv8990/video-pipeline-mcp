"""scripts/guards/invariants.py — межфайловые инварианты контракта сервера: то, что живёт в двух местах
и обязано совпадать (код отказа и реестр реакций, объявление и его читатель, факт и его наблюдение).

Зовёт: _evidence (реестр осей).
Зовут: гейт CI.

Долг не чинится задним числом: где он есть, ось держит ХРАПОВИК — вниз можно, вверх нет; где долга
нет, проверка жёсткая.
"""
from __future__ import annotations
import ast
import json
import re
import sys
from collections.abc import Set as AbstractSet
from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))


class НетУлики(Exception):
    """Оси нечего судить: источника улики нет. Третье состояние против пары «чисто / нарушения».

    Пустой список значит и «проверено, чисто», и «не проверялось», а читатель отчёта их не
    различает — и снятый потолок читается как чистота.
    """


def строка_оси(title: str, notes: list[str], не_судит: str = "", потолок: int | None = None) -> str:
    """Одна строка отчёта. Дом у формы один: два места печатали бы третье состояние по-разному."""
    if не_судит:
        return f"── {title}: не судится — {не_судит}"
    if потолок is None:
        return f"── {title}: {'чисто' if not notes else str(len(notes)) + ' шт.'}"
    return f"── {title}: {len(notes)} при потолке {потолок}"


# Две ветки — это выбор, три и больше по одному значению — уже таблица.
DISPATCH_LIMIT = 3


# Диспетчеризацию не отменяет ни тип значения, ни имя вместо литерала: `if code == 404` и
# `if kind == SET` добавляют случай правкой КОДА ровно так же, как сравнение со строкой.
DISPATCH_VALUE = (ast.Constant, ast.Name, ast.Attribute)


# Пути НЕ зашиты: корень приходит параметром, иначе сторожа нельзя проверить, не насорив в
# репозитории, — а проверка, требующая мусора, делается редко.
TABLES = ("config", "templates", "tables")


REGISTRY = ("config", "server_reactions.yaml")


CONTRACT = ("core", "contracts", "error_detail.py")


RESOURCES = ("config", "resources.yaml")


INVENTORY = ("tests", "quick", "tools_inventory.golden.json")


SCENARIOS = ("tests", "scenarios")


ROUTES = ("tests", "routes")


FACT_TYPES = ("core", "contracts", "fact.py")


OBSERVATIONS = ("tests", "harness", "observations.yaml")


def _at(root: Path, parts: tuple[str, ...]) -> Path:
    return root.joinpath(*parts)


# Код отказа бросают либо исключением зоны, либо обёрткой контекста.
RAISED_CODE = re.compile(r'(?:Error|\berr|_err)\(\s*\n?\s*"([A-Z][A-Z_0-9]{3,})"')


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


def codes_outside_registry(root: Path = ROOT,
                           known: AbstractSet[str] | None = None) -> list[str]:
    """Брошенный код отказа обязан быть и в реестре реакций, и в `KNOWN_ERROR_CODES`.

    Реестра нет — сверять не с чем, и это «улики нет», а не находка. Соседи по семейству молчат
    так же; здесь проверки не было, и сторож умирал трейсом вместо вердикта.
    """
    registry_file = _at(root, REGISTRY)
    if not registry_file.exists():
        return []
    if known is None:
        sys.path.insert(0, str(root))
        from core.contracts.error_detail import KNOWN_ERROR_CODES
        known = KNOWN_ERROR_CODES

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


def resources_off_inventory(root: Path = ROOT) -> list[str]:
    """Инструмент объявлен тяжёлым, а такого инструмента нет — снятие с цикла молча не действует.

    Переименовали инструмент — строка в `resources.yaml` осталась указывать в пустоту, вызов снова
    исполняется в цикле событий и морозит сервер, и НИ ОДНА проверка об этом не скажет: сценарий
    живучести бьёт единственным именем. Связи внутри файла (класс объявлен, у снятого с цикла есть
    предел) судит схема у двери деклараций (`core/declaration_schemas.py`) при загрузке; здесь — только
    связь между файлами: декларация против инвентаря инструментов.
    """
    declaration, inventory = _at(root, RESOURCES), _at(root, INVENTORY)
    if not declaration.exists() or not inventory.exists():
        return []
    data = yaml.safe_load(declaration.read_text(encoding="utf-8")) or {}
    known_tools = set(json.loads(inventory.read_text(encoding="utf-8")))
    return [f"config/resources.yaml: инструмента `{tool}` нет в инвентаре — строка указывает в пустоту"
            for tool in (data.get("tools") or {}) if tool not in known_tools]


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
            for arg, умолчание in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if умолчание is not None:
                    out.append((arg.arg, умолчание, умолчание.lineno))
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
    приёмник = node.func if isinstance(node, ast.Call) else node
    внутри = приёмник.value if isinstance(приёмник, (ast.Attribute, ast.Subscript)) else None
    outer = _receiver_path(внутри, assigns, depth + 1) if внутри is not None else None
    return None if outer is None else outer + (key,)


def _config_reads(tree: ast.AST) -> list[tuple[tuple[str, ...], ast.AST, int]]:
    """`X.get("ключ", запасное)`, у которых путь приёмника разрешился до декларации."""
    assigns: dict[str, ast.AST] = {}
    out: list[tuple[tuple[str, ...], ast.AST, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assigns.setdefault(node.targets[0].id, node.value)
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
            # `suppress` — тот же немой перехват, но узла ExceptHandler у него нет: пока ось
            # смотрела только на `except`, переписанный на него отказ уходил из-под потолка.
            if isinstance(node, ast.With):
                for item in node.items:
                    зов = item.context_expr
                    if (isinstance(зов, ast.Call)
                            and ast.unparse(зов.func).endswith("suppress")
                            and {ast.unparse(a) for a in зов.args} & BROAD):
                        notes.append(f"{source.relative_to(root)}:{node.lineno} — `suppress` "
                                     "гасит широкий отказ молча: сказать о нём здесь нечем "
                                     "по построению")
                continue
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


def axis_undeclared(root: Path = ROOT) -> list[str]:
    """Ось написана, но в реестр не внесена — судить её никто не будет, и молча.

    Промах в другую сторону (объявлен читатель, которого нет) ловит сам сбор реестра отказом.
    Форма оси — `(root: Path = ROOT) -> list[str]`; ею и опознаётся ненайденное объявление.
    """
    файл = _at(root, ("scripts", "guards", "invariants.py"))
    декл = _at(root, ("scripts", "guards", "evidence.yaml"))
    if not файл.exists() or not декл.exists():
        raise НетУлики("нет invariants.py или evidence.yaml — сверять объявление не с чем")
    try:
        объявление = (yaml.safe_load(декл.read_text(encoding="utf-8")) or {}).get("оси") or {}
    except yaml.YAMLError as сбой:
        raise НетУлики(f"evidence.yaml не разбирается ({сбой.__class__.__name__})") from сбой
    объявлены = {ось.get("читатель") for группа in объявление.values() for ось in группа or ()}
    notes = []
    for узел in ast.parse(файл.read_text(encoding="utf-8")).body:
        if not isinstance(узел, ast.FunctionDef) or узел.name.startswith("_"):
            continue
        args = [a.arg for a in узел.args.args]
        форма = (узел.returns is not None and ast.unparse(узел.returns) == "list[str]"
                 and args[:1] == ["root"])
        if форма and узел.name not in объявлены:
            notes.append(f"invariants.py:{узел.lineno} ось `{узел.name}` не объявлена в "
                         f"evidence.yaml (`оси`) — она не судит ничего")
    return notes


def _реестр_осей() -> dict:
    """Реестр осей из объявления. Пусто — это отказ, а не пустой набор: сторож без осей
    зелен по всему, и молчание было бы неотличимо от чистого дерева."""
    import _evidence
    декл = _evidence.оси()
    if not декл:
        raise SystemExit("invariants: реестр осей не объявлен — scripts/guards/evidence.yaml, "
                         "ключ `оси`. Судить нечем, а пустой набор осей дал бы зелёное по всему")
    return декл


def _читатель(ось: dict):
    """Условие оси остаётся Python; объявление зовёт его по имени, и промах имени — отказ."""
    fn = globals().get(ось["читатель"])
    if not callable(fn):
        raise SystemExit(f"invariants: у оси «{ось['заголовок']}» объявлен читатель "
                         f"`{ось['читатель']}`, которого в модуле нет")
    return fn


def _поле(ось: dict, имя: str) -> str:
    """Недостающее поле оси названо вслух: голый KeyError не говорит, КАКАЯ строка объявления
    неполна, а строк в реестре полсотни."""
    значение = ось.get(имя)
    if not значение:
        raise SystemExit(f"invariants: у оси «{ось.get('заголовок', '?')}» нет поля `{имя}` "
                         f"в scripts/guards/evidence.yaml — объявление неполно")
    return str(значение)


_ОСИ = _реестр_осей()


HARD = tuple((_поле(ось, "заголовок"), _читатель(ось)) for ось in _ОСИ.get("жёсткие") or ())


# Пятое поле необязательно: дверь объявляют единицам осей, и требовать её у всех значило бы
# писать «двери нет» полсотни раз.
RATCHETS = tuple((_поле(ось, "заголовок"), _читатель(ось),
                  Path(__file__).with_name(_поле(ось, "потолок")), _поле(ось, "совет"),
                  str(ось.get("дверь") or ""))
                 for ось in _ОСИ.get("храповики") or ())


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
    """Жёсткие оси — нарушение есть отказ; храповики — сверх потолка отказ. `--bless` пишет потолки по факту,
    `--root` судит названное дерево (без потолков: они принадлежат своему дереву)."""
    named = _named_tree(sys.argv)
    root = named or ROOT
    bless = "--bless" in sys.argv
    if named is not None and bless:
        print("invariants: --bless по чужому дереву запрещён — потолок принадлежит СВОЕМУ дереву, "
              "и запись чужого числа сюда была бы тихой ложью", file=sys.stderr)
        return 2
    failed = False
    for title, check in HARD:
        try:
            notes = check(root)
        except НетУлики as нет:  # отказ судить — своей строкой, а не «чисто» и не провал прогона
            print(строка_оси(title, [], str(нет)))
            continue
        print(строка_оси(title, notes))
        for note in notes:
            print(f"   ✗ {note}")
        failed = failed or bool(notes)
    for title, check, baseline, advice, _ in RATCHETS:
        try:
            notes = check(root)
        except НетУлики as нет:
            print(строка_оси(title, [], str(нет)))
            continue
        if named is not None:
            print(f"── {title}: {len(notes)} (потолок не судим — дерево чужое)")
            continue
        limit = int(baseline.read_text(encoding="utf-8").strip()) if baseline.exists() else len(notes)
        print(строка_оси(title, notes, потолок=limit))
        if bless:
            baseline.write_text(f"{len(notes)}\n", encoding="utf-8")
        elif len(notes) > limit:
            for note in notes:
                print(f"   ✗ {note}")
            print(f"   {advice}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
