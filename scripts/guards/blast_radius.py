#!/usr/bin/env python3
"""scripts/guards/blast_radius.py — что висит на этом месте кода: карта «строка → сценарии».

Отвечает ДО правки: «собираюсь тронуть вот здесь — что это заденет и чем это проверено». Покрытие
снимается по одному сценарию на сервер, поэтому строка знает своих сценариев поимённо, а не
«покрыта вообще». Сборка медленная и в гейт не входит — инструмент запускается руками.

У `--affected` улик две: прогон (что исполнялось) и статический граф вызовов (`_symbol_graph.py`) —
кто зовёт тронутое место, даже если его не гоняет ни один сценарий; вторая вердикта не меняет.

    python3 scripts/guards/blast_radius.py --build                 # собрать карту
    python3 scripts/guards/blast_radius.py --query core/paths.py:42
    python3 scripts/guards/blast_radius.py --changed               # по незакоммиченной правке
    python3 scripts/guards/blast_radius.py --affected              # какие сценарии гнать ИМЕННО сейчас
    python3 scripts/guards/blast_radius.py --check-routes          # рубеж маршрута реально ИСПОЛНЯЛСЯ
"""
import ast
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _evidence                                                           # noqa: E402
import _verdict                                                            # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Импорты ниже корня: харнесс живёт в `tests/`, и путь к нему добавляется выше.
import yaml  # noqa: E402
from coverage.parser import PythonParser  # noqa: E402

import _symbol_graph  # noqa: E402

from tests.harness import live_server  # noqa: E402
from tests.harness.scenario import Runner, Vocabulary, load, scenario_files  # noqa: E402
from tests.harness.scenario_map import MapRunner, load_map, plan  # noqa: E402
from tests.scenarios.steps import STEPS  # noqa: E402

SCENARIOS = ROOT / "tests" / "scenarios"
RADIUS = ROOT / "tests" / ".blast" / "radius.json"
BLIND_BASELINE = Path(__file__).with_name("blast_blind_baseline.txt")
COVERED = ROOT / "tests" / ".blast" / "covered_functions.json"
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


# ═══ Сбор ═══

def _coverage_of(run) -> dict[str, list[int]]:
    """Строки, исполненные СЕРВЕРОМ за один сценарий (харнесс поднимает его под покрытием)."""
    outdir = Path(tempfile.mkdtemp(prefix="vpm_blast_"))
    os.environ["VPM_SERVER_COVERAGE"] = str(outdir)
    try:
        run()
    finally:
        os.environ.pop("VPM_SERVER_COVERAGE", None)
    rc = outdir / "coveragerc"
    report = outdir / "coverage.json"
    subprocess.run([sys.executable, "-m", "coverage", "combine", f"--rcfile={rc}"],
                   cwd=ROOT, capture_output=True, text=True)
    subprocess.run([sys.executable, "-m", "coverage", "json", f"--rcfile={rc}", "-o", str(report)],
                   cwd=ROOT, capture_output=True, text=True)
    executed = {}
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        executed = {name: info.get("executed_lines") or [] for name, info in (data.get("files") or {}).items()}
    shutil.rmtree(outdir, ignore_errors=True)
    return executed


def build() -> dict:
    """Карта: файл → {сценарий: исполненные строки}. Один сценарий = один сервер, иначе строки смешаются."""
    vocab = Vocabulary()
    radius: dict[str, dict[str, list[int]]] = {}

    def remember(name: str, executed: dict[str, list[int]]):
        for path, lines in executed.items():
            radius.setdefault(path, {})[name] = lines
        print(f"  {name}: файлов {len(executed)}, строк {sum(len(v) for v in executed.values())}")

    for source in scenario_files(SCENARIOS):
        for scenario in load(source, vocab):
            def run(scenario=scenario):
                with live_server() as srv:
                    srv.rpc.timeout = 180.0
                    Runner(srv, steps=STEPS).run(scenario)
            remember(scenario.id, _coverage_of(run))

    for source in sorted(SCENARIOS.glob("*.map.yaml")):
        smap = load_map(source, vocab)
        for number, path in enumerate(plan(smap), 1):
            def пройти(smap=smap, path=path, number=number):
                with live_server() as srv:
                    srv.rpc.timeout = 180.0
                    MapRunner().walk_path(smap, path, number, Runner(srv, steps=STEPS))
            remember(f"{smap.id}#путь{number}", _coverage_of(пройти))

    RADIUS.parent.mkdir(parents=True, exist_ok=True)
    RADIUS.write_text(json.dumps(radius, ensure_ascii=False), encoding="utf-8")
    # Строки живут ровно до следующей правки: сдвинулись номера — число стало мусором. Поэтому
    # исполненное переводится в ИМЕНА функций здесь, где код и строки ещё согласованы.
    covered = {rel: sorted(_functions_hit(rel, {n for lines in by.values() for n in lines}))
               for rel, by in radius.items() if rel in set(_measured_files())}
    COVERED.write_text(json.dumps(covered, ensure_ascii=False), encoding="utf-8")
    print(f"\nКарта радиуса: {RADIUS} · файлов {len(radius)}; исполненных функций: "
          f"{sum(len(v) for v in covered.values())}")
    return radius


def _load() -> dict:
    if not RADIUS.exists():
        sys.exit(f"Карты нет: {RADIUS}. Собери её — `python3 scripts/guards/blast_radius.py --build`.")
    return json.loads(RADIUS.read_text(encoding="utf-8"))


# ═══ Запрос ═══

def query(target: str, radius: dict | None = None) -> list[str]:
    """`файл` или `файл:строка` → сценарии, которые это исполняют."""
    radius = radius if radius is not None else _load()
    path, _, line = target.partition(":")
    covered = radius.get(str(Path(path)))
    if covered is None:
        # Область замера — код сервера. Тест или скрипт «не покрыт» не потому, что заброшен,
        # а потому, что не измеряется: путать эти два случая значит кричать на каждую правку.
        дерево = дерево_файла(path)
        if дерево is None:
            причина = ("вне объявленных деревьев замера — объявление в scripts/guards/evidence.yaml")
        elif "слепая-зона" in дерево[1]:
            причина = "файл не исполняется НИ ОДНИМ сценарием — правка здесь сценариям невидима"
        else:
            причина = (f"дерево «{дерево[0]}» судится только радиусом: сценарий зовёт его "
                       "ПОДПРОЦЕССОМ, а покрытие в подпроцесс не ходит")
        print(f"  {target}: {причина}")
        return []
    if not line:
        hits = sorted(covered)
    else:
        hits = sorted(name for name, lines in covered.items() if int(line) in lines)
        if not hits:
            print(f"  {target}: строка не исполняется ни одним сценарием "
                  f"(файл трогают {len(covered)}: {', '.join(sorted(covered)[:3])}…)")
            return []
    print(f"  {target}: заденет {len(hits)} — {', '.join(hits)}")
    return hits


def changed() -> None:
    """Радиус НЕЗАКОММИЧЕННОЙ правки: какие сценарии стоят на тронутых строках."""
    diff = subprocess.run(["git", "diff", "-U0", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout
    radius, current, touched = _load(), "", 0
    for row in diff.splitlines():
        if row.startswith("+++ b/"):
            current = row[len("+++ b/"):]
        head = HUNK.match(row)
        if head and current.endswith(".py"):
            start, count = int(head.group(1)), int(head.group(2) or 1)
            for line in range(start, start + max(count, 1)):
                touched += 1
                query(f"{current}:{line}", radius)
    if not touched:
        print("  В незакоммиченной правке нет строк Python — радиус считать не по чему.")


def _churn() -> dict[str, int]:
    """Сколько коммитов трогали файл. История — единственная улика о цене правки, которой нет в коде."""
    вывод = subprocess.run(["git", "log", "--format=%H", "--name-only"],
                           cwd=ROOT, capture_output=True, text=True).stdout
    итог: dict[str, int] = {}
    for строка in вывод.splitlines():
        if строка.endswith(".py"):
            итог[строка] = итог.get(строка, 0) + 1
    return итог


def горячие() -> list[tuple[str, int, int]]:
    """(файл, правок, строк) для горячих точек: часто правится И крупнее медианы.

    Пороги берутся ДОЛЯМИ из объявления и пересчитываются каждый раз: абсолютное число правок
    протухает с каждым коммитом, доля — нет. Нет объявления — список пуст, а не выдуманные пороги.
    """
    декл = _evidence.горячие()
    доля = декл.get("доля-правок")
    if not доля:
        return []
    churn = _churn()
    размер = {}
    for rel in _measured_files("радиус"):
        try:
            размер[rel] = len([s for s in (ROOT / rel).read_text(encoding="utf-8").splitlines()
                               if s.strip()])
        except OSError:
            continue
    if not размер:
        return []
    медиана = sorted(размер.values())[len(размер) // 2]
    правки = sorted((churn.get(rel, 0) for rel in размер), reverse=True)
    порог = правки[max(int(len(правки) * доля) - 1, 0)]
    итог = [(rel, churn.get(rel, 0), n) for rel, n in размер.items()
            if churn.get(rel, 0) >= порог and n > медиана]
    return sorted(итог, key=lambda с: -с[1])


def показ_горячих() -> int:
    """ПОКАЗ приоритета, не отказ. Метка замера частично говорит «где мы смотрели», поэтому
    краснеть здесь было бы обвинением по улике, которой не хватает на обвинение."""
    ряд = горячие()
    if not ряд:
        print("── горячие точки: объявления нет либо замер пуст")
        return 0
    print(f"── горячих точек: {len(ряд)} (часто правятся И крупнее медианы)")
    for rel, правок, строк in ряд:
        дерево = дерево_файла(rel)
        print(f"   {правок:3} правок · {строк:5} строк · {(дерево or ('вне деревьев',))[0]:9}  {rel}")
    print("   Это не отказ: правка здесь дороже средней, и сюда идут сценарий и разбор в первую очередь.")
    return 0



def affected() -> int:
    """Сценарии, задетые ТЕКУЩЕЙ правкой, и строки, за которыми не стоит ни один сценарий.

    Печатает готовую команду точечного прогона: полная матрица идёт минуты, задетая часть — секунды.
    """
    diff = subprocess.run(["git", "diff", "-U0", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout
    radius, current = _load(), ""
    hit: set[str] = set()
    вне_покрытия: dict[str, set[str]] = {}
    blind: list[str] = []
    touched_lines: dict[str, set[int]] = {}
    тронутые: set[str] = set()
    for row in diff.splitlines():
        if row.startswith("+++ b/"):
            current = row[len("+++ b/"):]
        head = HUNK.match(row)
        if not (head and current.endswith(".py")):
            continue
        дерево = дерево_файла(current)
        if дерево is None:
            continue
        тронутые.add(current)
        if "слепая-зона" not in дерево[1]:
            # Дерево объявлено, но покрытие его не видит: сценарий зовёт его подпроцессом.
            # Молча пропустить — значит ответить «правка ничего не задела», а это неправда.
            вне_покрытия.setdefault(дерево[0], set()).add(current)
            continue
        covered = radius.get(str(Path(current))) or {}
        start, count = int(head.group(1)), int(head.group(2) or 1)
        for line in range(start, start + max(count, 1)):
            touched_lines.setdefault(current, set()).add(line)
            names = {name for name, lines in covered.items() if line in lines}
            hit |= names
            if not names:
                blind.append(f"{current}:{line}")
    for имя, файлы in sorted(вне_покрытия.items()):
        print(f"  дерево «{имя}» ({len(файлы)}): {', '.join(sorted(файлы)[:3])}"
              + (" …" if len(файлы) > 3 else ""))
        print("    судится радиусом, но не покрытием: сценарий зовёт его ПОДПРОЦЕССОМ, "
              "и что именно исполнилось — не измерено")
    stale = sorted(rel for rel in touched_lines
                   if RADIUS.exists() and (ROOT / rel).stat().st_mtime > RADIUS.stat().st_mtime)
    if stale:
        # Карта хранит НОМЕРА строк: файл, тронутый после сборки, сдвинул их, и сценарии тут
        # называются по устаревшим координатам. Это не «чисто» и не «хуже» — это негодная улика.
        print(f"  ⚠ карта старше кода: {len(stale)} файл(ов) правились после сборки "
              f"({', '.join(stale[:3])}) — пересобери `--build`, иначе сценарии названы по сдвинутым строкам")
    if blind:
        print(f"  ⚠ строк без единого сценария: {len(blind)} — {', '.join(blind[:5])}"
              + (" …" if len(blind) > 5 else ""))
        print("    правка в зоне, которую тесты не исполняют: сначала сценарий, потом код")
    if hit:
        print(f"  задетые сценарии ({len(hit)}): {', '.join(sorted(hit))}")
        print(f"    точечный прогон: VPM_SCENARIO='{','.join(sorted(hit))}' python3 tests/scenarios/test_scenarios.py")
    if not hit and not blind and not вне_покрытия:
        корни = sorted(к for д in (_evidence.деревья() or {}).values() for к in д.get("корни") or [])
        print(f"  Правка не касается измеряемой зоны (мерятся {', '.join(корни)}) — прогонять нечего.")
    задеты = {rel: (правок, строк) for rel, правок, строк in горячие() if rel in тронутые}
    if задеты:
        print(f"  ● горячая точка в правке ({len(задеты)}): правка здесь дороже средней")
        for rel, (правок, строк) in sorted(задеты.items(), key=lambda кв: -кв[1][0]):
            print(f"    {правок} правок · {строк} строк  {rel}")
        print("    Это показ, а не отказ: сценарий и разбор сюда идут в первую очередь.")
    static_blind({(rel, name) for rel, lines in touched_lines.items()
                  for name, (first, last) in _functions(rel, classes=True).items()
                  if any(first <= n <= last for n in lines)})
    return 1 if blind else 0


def static_blind(touched: set[tuple[str, str]], ask=None) -> None:
    """Вызывающие тронутых функций, которых не исполняет НИ ОДИН сценарий.

    Прогон знает только исполненное: вызывающий, которого не гоняет ни один сценарий, карте не
    виден — а правка достаётся ему первой. Граф отвечает статикой и разрешает не все вызовы,
    поэтому улика РАСШИРЯЕТ подозрение и не трогает вердикт: заверить безопасность она не может.
    """
    if not touched:
        return
    if not COVERED.exists():
        print(f"  ── улики нет: {COVERED} не собран (`--build`) — статику сверять не с чем")
        return
    # Источник знает только КОРОТКИЕ имена, а одно такое имя в дереве носят до одиннадцати разных
    # символов. Поэтому имя спрашивается вместе с файлом, где его тронули, и ответ про
    # другой файл уликой не считается. Два тронутых файла с одним коротким именем — двусмысленность
    # по построению, и улику по такому имени не берём вовсе.
    дома: dict[str, set[str]] = {}
    for rel, name in touched:
        дома.setdefault(name.rpartition(".")[2], set()).add(rel)
    спрошены = sorted(дома)
    где = {имя: next(iter(места)) for имя, места in дома.items() if len(места) == 1}
    sites = (ask or _symbol_graph.callers)(спрошены, где=где)
    if sites is None:
        print("  ── улики нет: символьный граф не отвечает (сервер не поставлен или граф не собран);"
              " вердикт остался на прогоне")
        return
    covered = json.loads(COVERED.read_text(encoding="utf-8"))
    known: dict[str, dict[str, tuple[int, int]]] = {}
    silent: dict[str, set[str]] = {}
    outside = 0
    for symbol, places in sites.items():
        for place in places:
            rel, _, line = place.rpartition(":")
            if not line.isdigit() or not (rel.startswith(("core/", "tools/")) or rel == "server.py"):
                continue                      # вне области замера: тесты и скрипты не мерятся
            if rel not in known:
                known[rel] = _functions(rel)
            inside = [n for n, (first, last) in known[rel].items() if first <= int(line) <= last]
            if not inside:
                outside += 1                  # вызов на уровне модуля: покрытие по ИМЕНАМ его не видит
            for name in inside:
                if name not in (covered.get(rel) or []):
                    silent.setdefault(symbol, set()).add(f"{rel}::{name}")
    if silent:
        print(f"  ⚠ по графу вызовов правку получат {sum(len(v) for v in silent.values())} вызывающих, "
              "которых не исполняет ни один сценарий:")
        for symbol, places in sorted(silent.items()):
            print(f"    {symbol} ← {', '.join(sorted(places))}")
        print("    улика статическая и неполная: расширяет подозрение, обратного не доказывает")
    if len(sites) < len(спрошены):
        # Имя, которого граф не знает, в ответе отсутствует. Печатать по нему «молчащих нет»
        # значило бы выдать нулевую улику за проверенную тишину.
        нет = [имя for имя in спрошены if имя not in sites]
        print(f"  ── улики нет по {len(нет)} именам из {len(спрошены)}: граф их не объявил либо "
              f"объявил однофамильца из другого файла ({', '.join(нет[:5])}"
              f"{'…' if len(нет) > 5 else ''})")
    if not silent:
        # Слова разные не ради красоты: «у тронутых функций» законно лишь когда улика есть по ВСЕМ
        # именам. Когда часть без улики, тишина относится только к остальным — иначе это снова
        # успокоение на нулевой улике, только помягче.
        целиком = len(sites) == len(спрошены)
        if целиком:
            print("  по графу вызовов молчащих вызывающих у тронутых функций нет")
        elif sites:
            print("  по остальным именам молчащих вызывающих нет")
    if outside:
        print(f"  ○ точек вызова вне функций: {outside} — покрытие меряется по именам, их оно не видит")


def check_routes() -> int:
    """Рубеж маршрута обязан ИСПОЛНЯТЬСЯ его опорой, а не просто существовать на диске."""
    radius = _load()
    routes = yaml.safe_load((ROOT / "tests" / "routes" / "routes.yaml").read_text(encoding="utf-8"))
    bad = 0
    for route in routes:
        scenario = str(route["proof"]["scenario"]).partition("#")[2]
        for hop in dict.fromkeys([h["at"] for h in route["hops"]] + [route["decided_by"]]):
            if not (ROOT / hop).exists():
                continue                          # рубеж-описание: «конверт MCP», «консоль сервера»
            if not hop.endswith(".py"):
                # Декларация исполняемых строк не имеет: покрытие про неё молчит, и делать вид,
                # что «рубеж пройден», нельзя — это отдельный вопрос («конфиг реально прочитан»).
                print(f"  ○ {route['route']}: {hop} — декларация, покрытием не измеряется")
                continue
            executed = scenario in (radius.get(str(Path(hop))) or {})
            print(f"  {'✓' if executed else '✗'} {route['route']}: {hop} "
                  f"{'исполняется' if executed else 'НЕ исполняется'} опорой {scenario}")
            bad += 0 if executed else 1
    print(f"\nРубежей мимо потока: {bad}")
    return 1 if bad else 0


def _functions(rel: str, classes: bool = False) -> dict[str, tuple[int, int]]:
    """Имя функции → диапазон строк. Имя переживает правки, номер строки — нет.

    `classes=True` добавляет и классы: покрытие меряется функциями, поэтому в счёт молчания они не
    идут, но у графа вызовов спрашивать про тронутую модель нужно именно по имени класса.
    """
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    out: dict[str, tuple[int, int]] = {}

    def walk(node, prefix: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                out[name] = (child.lineno, getattr(child, "end_lineno", child.lineno))
                walk(child, f"{name}.")
            elif isinstance(child, ast.ClassDef):
                name = f"{prefix}{child.name}"
                if classes:
                    out[name] = (child.lineno, getattr(child, "end_lineno", child.lineno))
                walk(child, f"{name}.")
    walk(tree, "")
    return out


def _functions_hit(rel: str, lines: set[int]) -> set[str]:
    """Функции, которых коснулось исполнение: хотя бы одна строка тела внутри диапазона."""
    return {name for name, (start, end) in _functions(rel).items()
            if any(start <= n <= end for n in lines)}


def _statements(rel: str) -> set[int]:
    """Исполнимые строки файла СТАТИЧЕСКИ: опись из сборки не годится — новый код в неё не попал бы,
    и сторож молчал бы ровно там, где обязан кричать."""
    parser = PythonParser(text=(ROOT / rel).read_text(encoding="utf-8"), filename=rel)
    parser.parse_source()
    return set(parser.statements)


def _measured_files(судится: str = "радиус") -> list[str]:
    """Файлы деревьев, объявленных в `evidence.yaml` для этого вопроса.

    Список деревьев — объявление, а не код: зашитый видел одно дерево из четырёх, и правка
    сторожа или хука отвечала «ничего не изменилось». Скрытые каталоги пропускаются целиком —
    под `tests/` лежат журналы прогонов (`.journal`, `.blast`), а это артефакты, не код.
    """
    объявление = _evidence.деревья()
    files: list[str] = []
    for дерево in объявление.values():
        if судится not in (дерево.get("судится") or []):
            continue
        for корень in дерево.get("корни") or []:
            путь = ROOT / корень
            найдено = [путь] if путь.is_file() else list(путь.rglob("*.py"))
            # Скрытое считается ВНУТРИ корня, а не по всему пути: сам корень бывает скрытым
            # (`.claude/hooks`), и общий фильтр вырезал бы дерево целиком — молча.
            files += [str(f.relative_to(ROOT)) for f in найдено
                      if not any(ч.startswith(".")
                                 for ч in (f.relative_to(путь).parts[:-1] if путь.is_dir() else ()))]
    return sorted({f for f in files if "__pycache__" not in f})


def дерево_файла(rel: str) -> tuple[str, list[str]] | None:
    """Имя дерева и вопросы, которыми оно судится. `None` — файл вне объявленных деревьев."""
    for имя, дерево in (_evidence.деревья() or {}).items():
        корни = дерево.get("корни") or []
        if any(rel == к or rel.startswith(к.rstrip("/") + "/") for к in корни):
            return имя, list(дерево.get("судится") or [])
    return None


def _молчащие() -> dict[str, list[str]]:
    """Молчащие функции по файлам: имя переживает правку, номер строки — нет."""
    covered = json.loads(COVERED.read_text(encoding="utf-8"))
    молчат = {}
    for rel in _measured_files("слепая-зона"):
        silent = sorted(set(_functions(rel)) - set(covered.get(rel) or []))
        if silent:
            молчат[rel] = silent
    return молчат


def _потолок_именами() -> set[str] | None:
    """Потолок читается ИМЕНАМИ. Прежний формат — одно число; оно не переживает пересборку
    карты: счёт 92 сходится и когда молчат те же функции, и когда пять новых заменили пять
    покрытых. `None` — потолка в этом формате нет, и отличить прирост от давнего долга нечем."""
    if not BLIND_BASELINE.exists():
        return None
    текст = BLIND_BASELINE.read_text(encoding="utf-8").strip()
    if not текст or текст.isdigit():
        return None
    return {s.strip() for s in текст.splitlines() if s.strip()}


def blind() -> int:
    """Функции сервера, которых не исполняет НИ ОДИН сценарий, — размер молчания карты.

    Отчёт называет ПРИРОСТ поимённо, а не восьмёрку худших: большое и растущее — разные вопросы,
    и читатель, которому показали худший файл, идёт чинить давний долг вместо новой дыры.
    Код 2 — улики нет (карта не собрана), это не то же самое, что «молчание выросло»: ложное
    обвинение выключает сторожа быстрее, чем его отсутствие.
    """
    if not COVERED.exists():
        print(f"── улики нет: {COVERED} не собран. Пересобери карту — `--build`.")
        return 2
    молчат = _молчащие()
    сейчас = {f"{rel}::{fn}" for rel, fns in молчат.items() for fn in fns}
    потолок = _потолок_именами()
    print(f"── функций вне всех сценариев: {len(сейчас)}")

    if "--bless" in sys.argv:
        import _stamp
        причина = _stamp.почему(sys.argv)
        if not причина:
            print("blast_radius: --bless без --почему запрещён. Потолок — это долг, и его сдвиг "
                  "решение: назови причину одной строкой, она уедет в подпись журнала.",
                  file=sys.stderr)
            return 2
        # Прежний потолок числом — это ТО ЖЕ измерение, а не ноль: иначе переезд формата
        # выглядит ростом долга с нуля и заводит остаток о долге, которого не появлялось.
        прежнее = BLIND_BASELINE.read_text(encoding="utf-8").strip() if BLIND_BASELINE.exists() else ""
        было = len(потолок) if потолок is not None else (int(прежнее) if прежнее.isdigit() else 0)
        BLIND_BASELINE.write_text("\n".join(sorted(сейчас)) + "\n", encoding="utf-8")
        print(f"   потолок записан именами: {len(сейчас)}")
        return _stamp.сдвиг_потолка(
            [("слепая зона", было, len(сейчас), BLIND_BASELINE.name)] if было != len(сейчас) else [],
            причина)

    if потолок is None:
        print("   ✗ потолок не в именах: старое число не отличает прирост от давнего долга. "
              "Перепиши потолок именами — `--blind --bless --почему \"<причина>\"`")
        return 1
    прирост = sorted(сейчас - потолок)
    ушли = потолок - сейчас
    if ушли:
        print(f"── ушли из молчания: {len(ушли)} (потолок опустится следующим --bless)")
    if прирост:
        print(f"── ПРИРОСТ, которого нет в потолке: {len(прирост)}")
        for имя in прирост:
            print(f"   ✗ {имя}")
        print("   Объяви сценарий, который их исполняет, пересобери карту (`--build`) и опусти "
              "потолок (`--blind --bless --почему \"<причина>\"`) — до пересборки новая функция "
              "и должна числиться молчащей")
        return 1
    print("── сводка, восьмёрка самых молчаливых файлов (это ДОЛГ, а не прирост):")
    for rel, fns in sorted(молчат.items(), key=lambda кв: -len(кв[1]))[:8]:
        print(f"   {len(fns):4d}  {rel}")
    return 0


ВЕРХНИЕ = ("core", "tools", "scripts", "tests", "docs", "config", ".claude", "studio", "spec")


def _константы(дерево: ast.Module) -> dict[str, str]:
    """{ИМЯ: путь} по присваиваниям вида `GUARDS = ("scripts", "guards")` — источник зовут именем."""
    свод = {}
    for узел in дерево.body:
        if not isinstance(узел, ast.Assign) or not isinstance(узел.value, ast.Tuple):
            continue
        куски = [э.value for э in узел.value.elts
                 if isinstance(э, ast.Constant) and isinstance(э.value, str)]
        if len(куски) == len(узел.value.elts) and куски and куски[0] in ВЕРХНИЕ:
            for цель in узел.targets:
                if isinstance(цель, ast.Name):
                    свод[цель.id] = "/".join(куски)
    return свод


def _источники(узел: ast.AST, конст: dict[str, str]) -> set[str]:
    """Пути, которые читает этот кусок кода: кортежи сегментов, имена констант и прямые строки.

    Разбор, а не объявление: реестр «механизм → источник» рассохся бы на первой же правке, а карта
    класса нужна ровно в тот момент, когда заводят нового судью, — то есть когда объявление ещё не
    написано.
    """
    найдено = set()
    for под in ast.walk(узел):
        if isinstance(под, ast.Tuple):
            куски = [э.value for э in под.elts
                     if isinstance(э, ast.Constant) and isinstance(э.value, str)]
            if len(куски) == len(под.elts) and куски and куски[0] in ВЕРХНИЕ:
                найдено.add("/".join(куски))
        elif isinstance(под, ast.Name) and под.id in конст:
            найдено.add(конст[под.id])
        elif isinstance(под, ast.Constant) and isinstance(под.value, str):
            з = под.value
            if "/" in з and з.split("/")[0] in ВЕРХНИЕ and " " not in з:
                найдено.add(з)
    return найдено


def карта_классов(root: Path = ROOT) -> tuple[dict[str, set[str]], list[str]]:
    """(источник → механизмы, механизмы вне карты). Оси называются поимённо, остальные — файлом."""
    карта: dict[str, set[str]] = {}
    вне = []
    sys.path.insert(0, str(root / "scripts" / "guards"))
    import invariants  # noqa: PLC0415 — импорт внутри: карта строится по запросу, а сторож
    # радиуса гоняется и там, где осей нет вовсе
    заголовки = {**{ч.__name__: и for и, ч in invariants.HARD},
                 **{с[1].__name__: с[0] for с in invariants.RATCHETS}}
    файлы = sorted((root / "scripts" / "guards").glob("*.py")) + \
        sorted((root / ".claude" / "hooks").glob("*.py"))
    for файл in файлы:
        try:
            дерево = ast.parse(файл.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        конст = _константы(дерево)
        общие = _источники(дерево, конст)
        оси_файла = set()
        for узел in дерево.body:
            if isinstance(узел, ast.FunctionDef) and узел.name in заголовки:
                имя = f"ось «{заголовки[узел.name]}»"
                оси_файла |= {имя}
                свои = _источники(узел, конст)
                for и in свои:
                    карта.setdefault(и, set()).add(имя)
                if not свои:
                    вне.append(имя)
        свои_файла = общие - {и for ось in оси_файла for и in карта.get(ось, set())}
        for и in свои_файла:
            карта.setdefault(и, set()).add(файл.name)
        if not общие:
            вне.append(файл.name)
    return карта, sorted(вне)


def показ_класса(источник: str = "", root: Path = ROOT) -> int:
    """Кто уже стоит на этом источнике. Без имени — вся карта классов.

    Вопрос задаётся ДО заведения нового судьи: кандидат почти всегда уже есть, и расширить его
    дешевле, чем завести соседа, который будет судить тот же класс вполсилы.
    """
    карта, вне = карта_классов(root)
    if not карта:
        print("карта классов пуста — разбирать нечего")
        return 1
    if источник:
        совпало = {и: кто for и, кто in карта.items() if источник in и}
        if not совпало:
            print(f"на источник «{источник}» не стоит НИКТО — это первый судья здесь, "
                  f"и его радиус придётся объявить самому")
            return 0
        for и, кто in sorted(совпало.items()):
            print(f"{и}: уже стоят {len(кто)}")
            for механизм in sorted(кто):
                print(f"   · {механизм}")
        print("\nкандидат уже есть: расширяй существующего, а не заводи соседа на тот же класс")
        return 0
    for и, кто in sorted(карта.items(), key=lambda пара: (-len(пара[1]), пара[0]))[:20]:
        print(f"{len(кто):3}  {и}")
    print(f"\nисточников в карте: {len(карта)}; механизмов вне карты: {len(вне)}")
    for имя in вне:
        print(f"   ⚠ {имя} — не читает ни одного объявленного источника, его радиус неизвестен")
    return 0


def показ_родов(root: Path = ROOT) -> int:
    """Покрытие зоны РОДАМИ сценариев: положительный · отрицательный · разрушительный.

    Радиус покрытия — это не только «сколько сценариев стоит на строке», но и «какого они рода».
    Зона, где все сценарии положительные, проверяет удачу: разрушение состояния в ней не пробовал
    никто, и первым его пробует владелец на своих данных.
    """
    sys.path.insert(0, str(root))
    from tests.harness.scenario import РОДЫ, роды
    свод = роды(root / "tests" / "scenarios")
    if not свод:
        print("объявленных сценариев нет — покрытие родами считать не по чему")
        return 1
    знак = {"положительный": "+", "отрицательный": "−", "разрушительный": "✗"}
    пусто = []
    for файл, сценарии in свод.items():
        счёт = {род: sum(1 for р in сценарии.values() if р == род) for род in РОДЫ}
        строка = "  ".join(f"{знак[род]} {счёт[род]:2}" for род in РОДЫ)
        print(f"{файл:28} {строка}")
        if сценарии and not счёт["разрушительный"]:
            пусто.append(файл)
    всего = {род: sum(1 for с in свод.values() for р in с.values() if р == род) for род in РОДЫ}
    print(f"\nвсего: положительных {всего['положительный']} · отрицательных "
          f"{всего['отрицательный']} · разрушительных {всего['разрушительный']}")
    print(f"зон без единого разрушительного: {len(пусто)} из {len(свод)}"
          + (f" — {', '.join(пусто)}" if пусто else ""))
    return 0


def main() -> int:
    try:                                       # след: без отметки механизм выглядит пылящимся
        sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
        import _trace
        _trace.mark("blast_radius.py")
    except Exception:                          # noqa: BLE001 — след не важнее самой работы
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--query", metavar="ФАЙЛ[:СТРОКА]")
    parser.add_argument("--changed", action="store_true")
    parser.add_argument("--check-routes", action="store_true")
    parser.add_argument("--affected", action="store_true")
    parser.add_argument("--blind", action="store_true")
    parser.add_argument("--роды", action="store_true",
                        help="покрытие зоны родами сценариев (не отказ)")
    parser.add_argument("--класс", nargs="?", const="", metavar="ИСТОЧНИК",
                        help="кто уже стоит на этом источнике; без имени — вся карта классов")
    parser.add_argument("--root", type=Path, default=ROOT, metavar="ПУТЬ",
                        help="судить НАЗВАННОЕ дерево (карта классов и покрытие родами)")
    parser.add_argument("--горячие", action="store_true",
                        help="показ: где правка дороже средней (не отказ)")
    parser.add_argument("--bless", action="store_true")
    parser.add_argument("--почему", metavar="ПРИЧИНА", default="",
                        help="причина сдвига потолка — уедет в подпись журнала")
    args = parser.parse_args()
    if args.build:
        build()
    if args.query:
        query(args.query)
    if args.changed:
        changed()
    # Судящие режимы закрываются общим хвостом; `--build`/`--query`/`--changed` вердикта не выносят,
    # и подпись под справкой означала бы прогон, которого не было.
    if args.affected:
        return _verdict.close("радиус правки", affected(),
                              ".venv/bin/python scripts/guards/blast_radius.py --affected",
                              точный=False)
    if args.check_routes:
        return _verdict.close("рубежи потоков", check_routes(),
                              ".venv/bin/python scripts/guards/blast_radius.py --check-routes",
                              точный=False)
    if getattr(args, "класс") is not None:
        return показ_класса(getattr(args, "класс") or "", args.root)
    if getattr(args, "роды"):
        return показ_родов(args.root)
    if args.горячие:
        return показ_горячих()
    if args.blind:
        return _verdict.close("слепая зона", blind(),
                              ".venv/bin/python scripts/guards/blast_radius.py --blind",
                              точный=False)
    if not any([args.build, args.query, args.changed, args.check_routes, args.affected,
                args.blind, args.горячие, getattr(args, "роды"),
                getattr(args, "класс") is not None]):
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
