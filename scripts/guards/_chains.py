#!/usr/bin/env python3
"""scripts/guards/_chains.py — цепочка инструкции: кого зовёт раздел скила и чем он кончается.

Зовёт: invariants.
Зовут: blast_radius, comment_guard.

Источник улики, не судья: разбирает скилы на узлы, находит адресата каждого (скил · сторож · файл ·
команда) и говорит, чем узел кончается — прогоном или прозой. Тем же разбором отвечает про код: кого
зовёт файл и кто зовёт его.

Узлом взят РАЗДЕЛ, а не строка: раздел переживает правку соседнего абзаца, строка — нет. Адресат
выводится разбором, а не объявляется: реестр «раздел → кого зовёт» рассохся бы на первой правке
текста, а таблица связей нужна ровно тогда, когда объявления ещё нет.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
СКИЛЫ = (".claude", "skills")
ЗАГОЛОВОК = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
ВСТАВКА = re.compile(r"`([^`\n]+)`")
БЛОК = re.compile(r"```(?:bash|sh|console|python)?\n(.*?)```", re.S)
# Команда — то, что ЗАПУСКАЮТ: имени файла мало, иначе каждая ссылка на сторожа считалась бы
# прогоном, и дыра «раздел зовёт, но поднять его нечем» исчезла бы из счёта.
ЗАПУСК = re.compile(r"^(?:[A-Z_][A-Z0-9_]*=\S+\s+)*"
                    r"(?:\.venv/bin/python|python3?|pytest|bash|ruff|mypy|npm|git|bandit)\b")
ТЕРМИНАЛЫ = ("прогон", "скил", "файл", "проза")


def скилы(root: Path = ROOT) -> list[str]:
    """Имена скилов на диске. Пусто — дерева скилов нет, и цепочку строить не из чего."""
    дерево = root.joinpath(*СКИЛЫ)
    return sorted(p.name for p in дерево.iterdir() if (p / "SKILL.md").exists()) \
        if дерево.is_dir() else []


def _цели() -> re.Pattern:
    """Регулярка живых целей живёт у сторожа осей — второй копии здесь быть не должно."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import invariants  # импорт по запросу: разбор скилов гоняют и там, где осей нет
    return invariants.ЖИВЫЕ_ЦЕЛИ


def разделы(текст: str) -> list[tuple[str, int, str]]:
    """[(заголовок, строка, тело)] — плюс «вступление» до первого заголовка: правило часто там."""
    строки = текст.splitlines()
    найдено: list[tuple[str, int, list[str]]] = [("вступление", 1, [])]
    внутри_блока = False
    for n, строка in enumerate(строки, 1):
        if строка.startswith("```"):
            внутри_блока = not внутри_блока
        совпало = None if внутри_блока else ЗАГОЛОВОК.match(строка)
        if совпало:
            найдено.append((совпало.group(2), n, []))
        else:
            найдено[-1][2].append(строка)
    return [(имя, n, "\n".join(тело)) for имя, n, тело in найдено if "".join(тело).strip()]


def адресаты(тело: str, свои: list[str], имя_скила: str = "") -> dict[str, list[str]]:
    """Кого зовёт этот кусок текста: {род: [адресат]}. Судится только код — вставки и блоки.

    Проза не судится намеренно: имя скила в предложении бывает уроком про снесённое, а обвинять
    урок значит запрещать записывать прошлое (тот же разбор, что у сторожа мёртвых команд).
    """
    цели = _цели()
    куски = ВСТАВКА.findall(тело) + [_без_пояснения(с) for блок in БЛОК.findall(тело)
                                     for с in блок.splitlines() if с.strip()]
    зовёт: dict[str, set[str]] = {род: set() for род in ("скил", "команда", "файл", "ось")}
    for кусок in куски:
        к = кусок.strip()
        if к in свои and к != имя_скила:
            зовёт["скил"].add(к)
        for цель in цели.findall(к):
            зовёт["файл"].add(цель)
        if ЗАПУСК.match(к) and цели.search(к):
            зовёт["команда"].add(к)
    for заголовок in re.findall(r"ось «([^»]+)»", тело):
        зовёт["ось"].add(заголовок)
    return {род: sorted(имена) for род, имена in зовёт.items() if имена}


def _без_пояснения(строка: str) -> str:
    """Хвост `# пояснение` в блоке — часть текста, а не команды: с ним команда не запускается."""
    return строка.split("  #")[0].split("\t#")[0].rstrip(" \\").strip()


def _терминал(зовёт: dict[str, list[str]]) -> str:
    """Чем кончается узел. Порядок важен: прогон сильнее ссылки, ссылка сильнее прозы."""
    if зовёт.get("команда"):
        return "прогон"
    if зовёт.get("скил") or зовёт.get("ось"):
        return "скил"
    if зовёт.get("файл"):
        return "файл"
    return "проза"


def цепочка_скила(имя: str, root: Path = ROOT) -> list[dict]:
    """Узлы цепочки одного скила: раздел → кого зовёт → чем кончается → чем поднять.

    Отказ, а не пустая таблица: скила нет — «инструкций не нужно» сказал бы сам инструмент.
    """
    свои = скилы(root)
    if имя not in свои:
        raise SystemExit(f"скила `{имя}` нет в {'/'.join(СКИЛЫ)}; есть: {', '.join(свои) or '—'}")
    файл = root.joinpath(*СКИЛЫ, имя, "SKILL.md")
    узлы = []
    for заголовок, строка, тело in разделы(файл.read_text(encoding="utf-8", errors="replace")):
        зовёт = адресаты(тело, свои, имя)
        узлы.append({"узел": заголовок, "строка": строка, "зовёт": зовёт,
                     "терминал": _терминал(зовёт), "команды": зовёт.get("команда", [])})
    return узлы


def связи(root: Path = ROOT) -> dict[str, dict[str, list[str]]]:
    """Таблица связей цепочек: {скил: {кого зовёт: [разделы, которые зовут]}}."""
    таблица: dict[str, dict[str, list[str]]] = {}
    for имя in скилы(root):
        рёбра: dict[str, list[str]] = {}
        for узел in цепочка_скила(имя, root):
            for сосед in узел["зовёт"].get("скил", []):
                рёбра.setdefault(сосед, []).append(узел["узел"])
        таблица[имя] = рёбра
    return таблица


def радиус(имя: str, root: Path = ROOT, таблица: dict | None = None) -> dict[str, int]:
    """{скил: глубина} — весь охват цепочки, а не один скил. Сам скил на глубине 0."""
    таблица = таблица if таблица is not None else связи(root)
    if имя not in таблица:
        raise SystemExit(f"скила `{имя}` нет в таблице связей; есть: {', '.join(таблица) or '—'}")
    охват = {имя: 0}
    волна = [имя]
    глубина = 0
    while волна:
        глубина += 1
        следующая = []
        for узел in волна:
            for сосед in таблица.get(узел, {}):
                if сосед not in охват:
                    охват[сосед] = глубина
                    следующая.append(сосед)
        волна = следующая
    return охват


def набор(имя: str, root: Path = ROOT, таблица: dict | None = None) -> dict[str, list[str]]:
    """Набор команд ВСЕЙ цепочки: {скил: команды}. Скил без команд остаётся в наборе пустым —
    иначе «цепочка поднимается вот этим» читалось бы там, где половина звеньев непроверяема."""
    охват = радиус(имя, root, таблица)
    return {сосед: sorted({к for узел in цепочка_скила(сосед, root) for к in узел["команды"]})
            for сосед in sorted(охват, key=lambda с: (охват[с], с))}


СВОИ_ДЕРЕВЬЯ = ("core", "tools", "scripts", "tests", "server")


def зовёт_код(rel: str, root: Path = ROOT) -> set[str]:
    """Кого зовёт файл КОДА: имена своих модулей из его импортов.

    Импорт, а не граф вызовов: граф живёт во внешнем индексе (`_symbol_graph`), и в CI его нет —
    правило, опирающееся на сеть, молчало бы там, где обязано судить.
    """
    import ast  # разбор нужен только этому вопросу, а модуль зовут и без него
    try:
        дерево = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return set()
    цели: set[str] = set()
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.Import):
            цели |= {a.name for a in узел.names}
        elif isinstance(узел, ast.ImportFrom) and узел.module == "__future__":
            continue                         # `annotations` — переключатель разбора, а не адресат
        elif isinstance(узел, ast.ImportFrom) and узел.module:
            # `from core import paths` адресует paths, а не core: считать головой дерева значит
            # засчитать любую шапку, где написан путь файла, — ложная зелень на весь замер.
            цели |= {f"{узел.module}.{a.name}" for a in узел.names}
    имена = {ц.split(".")[-1] for ц in цели
             if ц.split(".")[0] in СВОИ_ДЕРЕВЬЯ or ц.startswith("_")}
    return имена - set(СВОИ_ДЕРЕВЬЯ)


def модуль_файла(rel: str) -> str:
    """Полное имя модуля по пути: у пакета — путь каталога. Короткое имя не различает тёзок
    разных деревьев (`tools/excel` и `core/excel`)."""
    путь = Path(rel).with_suffix("")
    return ".".join(путь.parent.parts if путь.name == "__init__" else путь.parts)


def личность(rel: str) -> str:
    """Имя, которым файл называют в шапке: пакет — именем каталога, а не `__init__`."""
    путь = Path(rel)
    return путь.parent.name if путь.stem == "__init__" else путь.stem


ИНТЕРПРЕТАТОР = re.compile(r"(?:^|/)python[\d.]*$")


def _путь_выражения(узел, среда: dict[str, str]) -> str | None:
    """Значение выражения пути без исполнения: литерал, имя, `/`, `str()`, `.resolve()`, `.parent`,
    `.parents[N]`. У `среда or запасной` берётся запасной: переменной среды разбору не видно."""
    import ast
    if isinstance(узел, ast.Constant) and isinstance(узел.value, str):
        return узел.value
    if isinstance(узел, ast.Name):
        return среда.get(узел.id)
    if isinstance(узел, ast.BoolOp):
        return next(filter(None, (_путь_выражения(в, среда) for в in reversed(узел.values))), None)
    if isinstance(узел, ast.BinOp) and isinstance(узел.op, ast.Div):
        лево, право = _путь_выражения(узел.left, среда), _путь_выражения(узел.right, среда)
        return str(Path(лево) / право) if лево and право else None
    if isinstance(узел, ast.Call) and isinstance(узел.func, ast.Name) and узел.func.id in ("str", "Path") \
            and len(узел.args) == 1:
        return _путь_выражения(узел.args[0], среда)
    if isinstance(узел, ast.Call) and isinstance(узел.func, ast.Attribute) and узел.func.attr == "resolve":
        return _путь_выражения(узел.func.value, среда)
    if isinstance(узел, ast.Attribute) and узел.attr == "parent":
        база = _путь_выражения(узел.value, среда)
        return str(Path(база).parent) if база else None
    if isinstance(узел, ast.Subscript) and isinstance(узел.value, ast.Attribute) \
            and узел.value.attr == "parents" and isinstance(узел.slice, ast.Constant) \
            and isinstance(номер := узел.slice.value, int):
        база = _путь_выражения(узел.value.value, среда)
        return str(Path(база).parents[номер]) if база else None
    return None


def _исполняемые_пути(дерево, файл: Path) -> set[Path]:
    """Файлы, которые код исполняет без импорта: сосед через `sys.path`, исходник, загруженный по
    пути, argv интерпретатора — в том числе через свой помощник, строящий их из параметра.

    Упоминание пути строкой вызовом не считается: совет «запусти scripts/…» ничего не исполняет.
    """
    import ast
    среда = {"__file__": str(файл)}
    for _ in range(2):                               # второй проход — имя через имя, объявленное ниже
        for у in ast.walk(дерево):
            if isinstance(у, ast.Assign) and len(у.targets) == 1 and isinstance(у.targets[0], ast.Name) \
                    and (з := _путь_выражения(у.value, среда)):
                среда[у.targets[0].id] = з
            elif isinstance(у, (ast.FunctionDef, ast.AsyncFunctionDef)):
                умолчания = zip(у.args.args[len(у.args.args) - len(у.args.defaults):], у.args.defaults)
                среда |= {а.arg: з for а, д in умолчания if (з := _путь_выражения(д, среда))}

    def интерпретатор(у) -> bool:
        if isinstance(у, ast.IfExp):
            return интерпретатор(у.body) or интерпретатор(у.orelse)
        if isinstance(у, ast.Attribute) and у.attr == "executable":
            return True
        return bool(ИНТЕРПРЕТАТОР.search(_путь_выражения(у, среда) or ""))

    def цели(у) -> list:
        if isinstance(у, (ast.List, ast.Tuple)) and len(у.elts) > 1 and интерпретатор(у.elts[0]):
            return [у.elts[1]]
        if not isinstance(у, ast.Call) or not у.args:
            return []
        имя = у.func.attr if isinstance(у.func, ast.Attribute) else getattr(у.func, "id", "")
        if имя == "compile" and isinstance(у.args[0], ast.Call) and isinstance(у.args[0].func, ast.Attribute):
            return [у.args[0].func.value]                                  # compile(путь.read_text(), …)
        return [у.args[-1]] if имя in ("spec_from_file_location", "run_path") else []

    помощники = {ф.name: [(i, п.arg, ц) for у in ast.walk(ф) for ц in цели(у)
                          for i, п in enumerate(ф.args.args)
                          if any(isinstance(н, ast.Name) and н.id == п.arg for н in ast.walk(ц))]
                 for ф in ast.walk(дерево) if isinstance(ф, (ast.FunctionDef, ast.AsyncFunctionDef))}
    каталоги = [Path(к) for у in ast.walk(дерево)
                if isinstance(у, ast.Call) and isinstance(у.func, ast.Attribute) and у.args
                and у.func.attr in ("insert", "append") and getattr(у.func.value, "attr", "") == "path"
                and (к := _путь_выражения(у.args[-1], среда))]
    найдено: set[Path] = set()
    for у in ast.walk(дерево):
        выражения = [(ц, среда) for ц in цели(у)]
        if isinstance(у, ast.Call) and isinstance(у.func, ast.Name):
            выражения += [(ц, {**среда, п: з}) for i, п, ц in помощники.get(у.func.id, [])
                          if i < len(у.args) and (з := _путь_выражения(у.args[i], среда))]
        найдено |= {Path(з) for ц, ср in выражения if (з := _путь_выражения(ц, ср))}
        имена = [а.name for а in у.names] if isinstance(у, ast.Import) else \
            [у.module] if isinstance(у, ast.ImportFrom) and у.module and not у.level else []
        найдено |= {к / имя.replace(".", "/") / хвост for к in каталоги for имя in имена
                    for хвост in ("__init__.py",)} | \
            {(к / имя.replace(".", "/")).with_suffix(".py") for к in каталоги for имя in имена}
    return найдено


def модули_кода(rel: str, root: Path = ROOT) -> set[str]:
    """Свои модули, которые файл импортирует или исполняет по пути, — полными именами по дереву.

    Разрешается и относительный импорт, и `from пакет.модуль import Символ`. Имя без файла
    остаётся как написано, только если это сосед через `sys.path` (`import _stamp` из хука).
    """
    import ast
    def найти(база: Path, точками: str) -> str | None:
        путь = база.joinpath(*точками.split("."))
        for кандидат in (путь.with_suffix(".py"), путь / "__init__.py"):
            if кандидат.is_file():
                return модуль_файла(str(кандидат.relative_to(root)))
        return None
    try:
        дерево = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return set()
    свой = (root / rel).parent
    модули: set[str] = set()
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.Import):
            for а in узел.names:
                if найдено := найти(root, а.name) or найти(свой, а.name):
                    модули.add(найдено)
                elif а.name.startswith("_"):
                    модули.add(а.name)
        elif isinstance(узел, ast.ImportFrom) and узел.module != "__future__":
            базы = [свой.parents[узел.level - 2] if узел.level > 1 else свой] if узел.level \
                else [root, свой]
            for база in базы:
                for а in узел.names:
                    полное = f"{узел.module}.{а.name}" if узел.module else а.name
                    if найдено := найти(база, полное) or (узел.module and найти(база, узел.module)):
                        модули.add(найдено)
            if not узел.level and узел.module and узел.module.startswith("_") \
                    and not any(найти(б, узел.module) for б in базы):
                модули.add(узел.module)
    for путь in _исполняемые_пути(дерево, root / rel):
        полный = (путь if путь.is_absolute() else root / путь).resolve()
        if полный.suffix == ".py" and полный.is_file() and полный.is_relative_to(root.resolve()) \
                and полный != (root / rel).resolve():
            модули.add(модуль_файла(str(полный.relative_to(root.resolve()))))
    return модули


def карта_вызовов(файлы: list[str], root: Path = ROOT) -> dict[str, set[str]]:
    """{полное имя модуля: личности тех, кто его импортирует}. Считается по всему списку разом:
    поштучный обход стоил бы повторного разбора дерева на каждый файл, и сторож перестали бы гонять."""
    карта: dict[str, set[str]] = {}
    for rel in файлы:
        свой = личность(rel)
        for цель in модули_кода(rel, root):
            карта.setdefault(цель, set()).add(свой)
    return карта

def скилы_о_файле(rel: str, root: Path = ROOT) -> dict[str, list[str]]:
    """{скил: разделы, которые называют ЭТОТ файл или его модуль}. Ищется только в коде-вставках.

    Связь цепочки с КОНКРЕТНЫМ файлом — то, ради чего шапка снова имеет цену: иначе набор цепочек
    висит над деревом целиком и про отдельный файл не говорит ничего.
    """
    найдено: dict[str, list[str]] = {}
    for скил in скилы(root):
        разделы_скила = []
        for узел in цепочка_скила(скил, root):
            свои = узел["зовёт"].get("файл", []) + узел["зовёт"].get("команда", [])
            # Только полный путь: одно имя живёт в разных деревьях (`_trace.py` у хуков и у
            # сторожей), и связь по имени свела бы разные файлы в один — ложная связь хуже её
            # отсутствия, потому что она не проверяется.
            if any(rel in с for с in свои):
                разделы_скила.append(узел["узел"])
        if разделы_скила:
            найдено[скил] = разделы_скила
    return найдено
