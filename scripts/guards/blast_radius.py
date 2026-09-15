#!/usr/bin/env python3
"""scripts/guards/blast_radius.py — что висит на этом месте кода: карта «строка → сценарии».

Паспорт: будит редактирование, наряд, рука · читает tests/.blast/radius.json,
  tests/routes/routes.yaml, scripts/guards/evidence.yaml · расписывает «поднять правку → сверить
  возраст карты → назвать задетые сценарии → назвать слепые строки → предупредить о горячей
  точке» · подписывает через _verdict.
Зовёт: _chains, _evidence, _stamp, _symbol_graph, _verdict.
Зовут: наряд и рука, из кода никто.

Отвечает ДО правки: «собираюсь тронуть вот здесь — что это заденет и чем это проверено». Покрытие
снимается по одному сценарию на сервер, поэтому строка знает своих сценариев поимённо, а не
«покрыта вообще». Сборка медленная и в гейт не входит.

У `--affected` улик две: прогон (что исполнялось) и статический граф вызовов — кто зовёт тронутое
место, даже если его не гоняет ни один сценарий; вторая вердикта не меняет.

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
import _stamp  # noqa: E402 — тот же порядок: роспись шагов лежит рядом со сторожами
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



ПОВТОР_РАДИУСА = ".venv/bin/python scripts/guards/blast_radius.py --affected"


def _расписаться(ряды: list[dict]) -> None:
    """Роспись прогона ОДНОЙ подписью: поднимается `lesson_producer.py --шаги <ключ>`.

    Без неё «прогонять нечего» звучит одинаково и когда правка не задела измеряемой зоны, и когда
    правки нет вовсе, и когда карта старше кода, — три разных утверждения под одним молчанием.
    """
    _stamp.роспись(ряды, механизм="blast_radius.py", cmd=ПОВТОР_РАДИУСА, зона="сторожа")


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
    _расписаться([
        _stamp.строка_шага(
            "поднять правку", "git diff -U0 HEAD",
            [_stamp.условие("в дереве есть незакоммиченная правка", True, bool(diff.strip()))],
            исход="сработал" if diff.strip() else "молчал",
            следствие=f"тронутых файлов дерева: {len(тронутые)}" if diff.strip()
            else "дерево чистое — радиус считать не от чего, и это не «ничего не задето»"),
        _stamp.строка_шага(
            "сверить возраст карты", "mtime карты против файла",
            [_stamp.условие("файл правлен позже сборки карты", True, bool(stale))],
            исход="сработал" if stale else "молчал",
            следствие=f"файлов новее карты: {len(stale)}" if stale
            else "карта не старше кода — координаты строк годны"),
        _stamp.строка_шага(
            "назвать задетые сценарии", "карта радиуса",
            [_stamp.условие("хоть одна строка покрыта сценарием", True, bool(hit))],
            исход="сработал" if hit else "молчал",
            следствие=f"задетых сценариев: {len(hit)}" if hit
            else "ни одна тронутая строка не покрыта — гнать нечего"),
        _stamp.строка_шага(
            "назвать слепые строки", "карта радиуса",
            [_stamp.условие("есть строка без единого сценария", True, bool(blind))],
            исход="сработал" if blind else "молчал",
            следствие=f"строк без сценария: {len(blind)}" if blind
            else "слепых строк в правке нет"),
        _stamp.строка_шага(
            "предупредить о горячей точке", "горячие",
            [_stamp.условие("правка попала в горячую точку", True, bool(задеты))],
            исход="сработал" if задеты else "молчал",
            следствие=f"горячих файлов в правке: {len(задеты)}" if задеты
            else "правка идёт по спокойным местам"),
    ])
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


def _цепь_деления(узел: ast.BinOp) -> list[str]:
    """Куски пути из `X / "docs" / "roadmap"`. Нестроковый кусок ОБРЫВАЕТ цепь, а не пропускается.

    Вычисленное имя (`дом / файл`) путём не является: продолжив за ним, мы склеили бы куски,
    которые в дереве рядом не стоят, и получили бы источник, которого никто не читает.
    """
    части: list[ast.AST] = []
    н: ast.AST = узел
    while isinstance(н, ast.BinOp) and isinstance(н.op, ast.Div):
        части.insert(0, н.right)
        н = н.left
    куски = []
    for часть in части:
        if not (isinstance(часть, ast.Constant) and isinstance(часть.value, str)):
            break
        куски.append(часть.value)
    return куски


def _источники(узел: ast.AST, конст: dict[str, str]) -> set[str]:
    """Пути, которые читает этот кусок кода: кортежи сегментов, имена констант и прямые строки.

    Разбор, а не объявление: реестр «механизм → источник» рассохся бы на первой же правке, а карта
    класса нужна ровно в тот момент, когда заводят нового судью, — то есть когда объявление ещё не
    написано.
    """
    найдено = set()
    # Цепь деления разбирается ОДИН раз — от самого внешнего узла: `ast.walk` встречает и левые
    # операнды, и каждый префикс пути стал бы отдельным источником («.claude» рядом с
    # «.claude/state/vpm-permit»), раздув людность места, которого нет.
    вложенные = {id(п.left) for п in ast.walk(узел)
                 if isinstance(п, ast.BinOp) and isinstance(п.op, ast.Div)}
    for под in ast.walk(узел):
        if isinstance(под, ast.BinOp) and isinstance(под.op, ast.Div) and id(под) not in вложенные:
            куски = _цепь_деления(под)
            if куски and куски[0] in ВЕРХНИЕ:
                найдено.add("/".join(куски))
        elif isinstance(под, ast.Call) and isinstance(под.func, ast.Attribute) \
                and под.func.attr == "joinpath":
            куски = [а.value for а in под.args
                     if isinstance(а, ast.Constant) and isinstance(а.value, str)]
            if len(куски) == len(под.args) and куски and куски[0] in ВЕРХНИЕ:
                найдено.add("/".join(куски))
        elif isinstance(под, ast.Tuple):
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


def _роды_зон(зона: Path) -> dict[str, dict[str, str]]:
    """{файл: {сценарий: род}} — один читатель объявлений на весь сторож радиуса."""
    from tests.harness.scenario import роды as читатель  # noqa: PLC0415 — род живёт в объявлении
    return читатель(зона)


def _роды_сценариев(root: Path = ROOT) -> dict[str, str]:
    """{сценарий: род} по всем объявлениям. Пусто — объявлений нет, и род спрашивать не у кого."""
    sys.path.insert(0, str(root))
    from tests.harness.scenario import роды as роды_зон  # noqa: PLC0415 — род живёт в объявлении,
    # и разбор нужен только тем режимам, которые о нём спрашивают
    return {имя: род for зона in роды_зон(root / "tests" / "scenarios").values()
            for имя, род in зона.items()}


def _тела(rel: str, root: Path = ROOT) -> dict[str, tuple[int, int]]:
    """Имя функции → диапазон ТЕЛА. Строка `def` исполняется при импорте, поэтому по ней покрытой
    выглядит любая функция — «покрыт» тогда значит «модуль загрузили», а не «сюда заходили»."""
    дерево = ast.parse((root / rel).read_text(encoding="utf-8"))
    свод: dict[str, tuple[int, int]] = {}

    def обойти(узел, префикс: str):
        for ребёнок in ast.iter_child_nodes(узел):
            if isinstance(ребёнок, (ast.FunctionDef, ast.AsyncFunctionDef)):
                имя = f"{префикс}{ребёнок.name}"
                тело = ребёнок.body
                if (len(тело) > 1 and isinstance(тело[0], ast.Expr)
                        and isinstance(getattr(тело[0], "value", None), ast.Constant)):
                    тело = тело[1:]              # докстринг тоже исполняется при определении
                свод[имя] = (тело[0].lineno, getattr(ребёнок, "end_lineno", ребёнок.lineno))
                обойти(ребёнок, f"{имя}.")
            elif isinstance(ребёнок, ast.ClassDef):
                обойти(ребёнок, f"{префикс}{ребёнок.name}.")

    обойти(дерево, "")
    return свод


def цепочка(rel: str, root: Path = ROOT) -> list[dict]:
    """Узлы цепочки одного файла: функция → сценарии, их РОДЫ и команда прогона именно их.

    Карта покрытия знает, какие строки исполнил каждый сценарий; имена функций переживают правку,
    номера строк — нет, поэтому узлом берётся функция. Род сценария приходит из объявления: узел,
    покрытый одними положительными, проверен на удаче, и это видно числом, а не на глаз.
    """
    карта_пути = root / "tests" / ".blast" / "radius.json"
    if not карта_пути.exists():
        raise SystemExit(f"карты радиуса нет ({карта_пути}) — собрать: --build. Пустая таблица "
                         f"здесь читалась бы как «сценариев не нужно»")
    radius = json.loads(карта_пути.read_text(encoding="utf-8"))
    исполнено = radius.get(str(Path(rel)))
    if исполнено is None:
        raise SystemExit(f"{rel}: файла нет в карте радиуса — он вне деревьев замера либо карта "
                         f"собрана до его появления. Пересобрать: --build")
    роды = _роды_сценариев(root)
    узлы = []
    for имя, (начало, конец) in sorted(_тела(rel, root).items(), key=lambda п: п[1][0]):
        свои = sorted(с for с, строки in исполнено.items()
                      if any(начало <= n <= конец for n in строки))
        счёт = {род: sum(1 for с in свои if роды.get(с) == род)
                for род in ("положительный", "отрицательный", "разрушительный")}
        команда = (f"VPM_SCENARIO={','.join(свои)} .venv/bin/python tests/scenarios/test_scenarios.py"
                   if свои else "")
        узлы.append({"узел": имя, "строки": (начало, конец), "сценарии": свои,
                     "роды": счёт, "команда": команда})
    return узлы


def показ_цепочки(rel: str, root: Path = ROOT) -> int:
    """Таблица радиуса по файлу: узел · роды покрытия · команда. Дыры названы и посчитаны."""
    узлы = цепочка(rel, root)
    if not узлы:
        print(f"{rel}: ни одной функции — цепочки здесь нет")
        return 1
    без_покрытия, без_разрушения = [], []
    for узел in узлы:
        счёт = узел["роды"]
        метка = f"+ {счёт['положительный']:3} − {счёт['отрицательный']:3} ✗ {счёт['разрушительный']:3}"
        дыра = "" if счёт["разрушительный"] else "   ← разрушение здесь не пробовал никто"
        print(f"{узел['узел']:38} {метка}{дыра}")
        if not узел["сценарии"]:
            без_покрытия.append(узел["узел"])
            print("     дыра: не исполняет ни один сценарий — правка здесь сценариям невидима")
            continue
        if not счёт["разрушительный"]:
            без_разрушения.append(узел["узел"])
    print(f"\nузлов {len(узлы)} · без покрытия {len(без_покрытия)} · "
          f"без разрушительного {len(без_разрушения)}")
    if без_покрытия:
        print(f"   вне сценариев: {', '.join(без_покрытия[:8])}")
    print(f"   команды набора по узлу: --цепочка {rel} --узел <имя>")
    return 0


def показ_узла(rel: str, имя: str, root: Path = ROOT) -> int:
    """Три набора одного узла и КОМАНДА на каждый: положительный · отрицательный · разрушительный.

    Команда лежит рядом со счётом намеренно: набор, который не поднять одной строкой, проверяют
    «когда-нибудь», а разрушительный — не проверяют вовсе.
    """
    узлы = {у["узел"]: у for у in цепочка(rel, root)}
    if имя not in узлы:
        print(f"{rel}: узла `{имя}` нет; есть {', '.join(sorted(узлы))[:120]}")
        return 1
    узел = узлы[имя]
    роды = _роды_сценариев(root)
    пусто = True
    for род in ("положительный", "отрицательный", "разрушительный"):
        свои = [с for с in узел["сценарии"] if роды.get(с) == род]
        if not свои:
            print(f"{род}: НАБОРА НЕТ — эта сторона узла не проверялась ни разу")
            continue
        пусто = False
        print(f"{род}: {len(свои)}")
        print(f"   VPM_SCENARIO={','.join(свои)} "
              f".venv/bin/python tests/scenarios/test_scenarios.py")
    if пусто:
        print("узел вне сценариев целиком — команду поднять нечем")
        return 1
    return 0


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


def показ_скила(имя: str, раздел: str = "", root: Path = ROOT) -> int:
    """Цепочка скила: раздел → кого зовёт → чем кончается. Дыры названы и посчитаны."""
    import _chains  # noqa: PLC0415 — по запросу: сторож радиуса гоняется и там, где скилов нет
    узлы = _chains.цепочка_скила(имя, root)
    if раздел:
        свои = [у for у in узлы if у["узел"] == раздел]
        if not свои:
            print(f"{имя}: раздела `{раздел}` нет; есть {', '.join(у['узел'] for у in узлы)[:160]}")
            return 1
        узел = свои[0]
        print(f"{имя} · {узел['узел']} (строка {узел['строка']}) · кончается: {узел['терминал']}")
        for род, адресаты in узел["зовёт"].items():
            print(f"   {род}: {', '.join(адресаты)}")
        if not узел["зовёт"]:
            print("   не зовёт никого: исполнение этой инструкции проверить нечем")
            return 1
        for команда in узел["команды"]:
            print(f"   поднять: {команда}")
        return 0
    счёт = {род: sum(1 for у in узлы if у["терминал"] == род) for род in _chains.ТЕРМИНАЛЫ}
    for узел in узлы:
        зовёт = узел["зовёт"]
        метка = " ".join(f"{род} {len(адресаты)}" for род, адресаты in зовёт.items()) or "—"
        print(f"{узел['узел'][:44]:46} {узел['терминал']:7} {метка}")
    охват = _chains.радиус(имя, root)
    print(f"\nузлов {len(узлы)} · прогон {счёт['прогон']} · скил {счёт['скил']} · "
          f"файл {счёт['файл']} · проза {счёт['проза']}")
    print(f"   радиус цепочки: {len(охват)} скилов, глубина {max(охват.values())}")
    print(f"   дыра: {счёт['проза']} разделов ведут в прозу — их исполнение не проверяет никто")
    print(f"   набор команд цепочки: --набор скил:{имя}")
    return 0


def показ_связей(root: Path = ROOT) -> int:
    """Таблица связей цепочек: кто кого зовёт, кого не зовёт никто, чья цепочка кончается на себе."""
    import _chains  # noqa: PLC0415 — по запросу: сторож радиуса гоняется и там, где скилов нет
    таблица = _chains.связи(root)
    if not таблица:
        print("дерева скилов нет — таблицу связей строить не из чего")
        return 1
    зовут = {сосед for рёбра in таблица.values() for сосед in рёбра}
    for имя, рёбра in sorted(таблица.items()):
        охват = _chains.радиус(имя, root, таблица)
        print(f"{имя:24} → {len(рёбра):2} · охват {len(охват):2} · "
              f"{', '.join(sorted(рёбра))[:90] or 'никого'}")
    сироты = sorted(set(таблица) - зовут)
    тупики = sorted(имя for имя, рёбра in таблица.items() if not рёбра)
    print(f"\nскилов {len(таблица)} · рёбер {sum(len(р) for р in таблица.values())} · "
          f"сирот {len(сироты)} · тупиков {len(тупики)}")
    if сироты:
        print(f"   никто не зовёт: {', '.join(сироты)} — в цепочку попадают только прямым вызовом")
    if тупики:
        print(f"   не зовут никого: {', '.join(тупики)} — цепочка кончается на себе")
    return 0


АДРЕСА = ("скил", "задача", "сторож", "ось", "файл")
# Команда приходит из ТЕКСТА скила, а текст правят. Знаки оболочки в ней — уже не команда, а чужая
# логика в нашем прогоне, поэтому маршрут их не исполняет, а отказывается вслух.
ОБОЛОЧКА = re.compile(r"[|;&><`$]")
# Команда с подстановкой `<путь>` — заготовка, а не команда: запустить её значит спросить сторожа
# про файл с именем «<путь>» и получить зелёное там, где не проверено ничего.
ПОДСТАНОВКА = re.compile(r"<[^>]+>")


def _объявленные_исходы(root: Path = ROOT) -> dict[tuple[str, str], str]:
    """{(сторож, флаг): судит|справка} из объявления типов работы: `сверх` судит, `спросить` спрашивает."""
    задачи = (_evidence.словарь(root / "scripts" / "guards" / "evidence.yaml")
              or {}).get("задачи") or {}
    свод: dict[tuple[str, str], str] = {}
    for тело in задачи.values():
        for файл, флаги in ((тело or {}).get("сверх") or {}).items():
            for флаг in (флаги if isinstance(флаги, list) else [флаги]):
                свод[(файл, str(флаг).split()[0])] = "судит"
        for файл, что in ((тело or {}).get("спросить") or {}).items():
            свод[(файл, str(что).split()[0])] = "справка"
    return свод


def род_исхода(команда: str, объявлено: dict) -> str:
    """Что значит код возврата этой команды: судит · справка · не объявлен.

    «Не объявлен» — не пропуск: такую команду маршрут не ветвит и не запускает, потому что
    угаданный смысл кода возврата даёт зелёный маршрут на красной улике.
    """
    # Прогон объявленного сценария судит по коду возврата — это и есть его смысл, и второго
    # объявления ему не нужно: род сценария уже объявлен в самом файле сценариев, а команда без
    # выбранного имени гоняла бы всё подряд и вердикта об ЭТОМ сценарии не давала.
    if "VPM_SCENARIO=" in команда and "test_scenarios.py" in команда:
        return "судит"
    файлы = re.findall(r"[\w./-]+\.py", команда)
    флаги = re.findall(r"(?<!\S)--[\w-]+", команда) or [""]
    for файл in файлы:
        for флаг in флаги:
            род = объявлено.get((Path(файл).name, флаг))
            if род:
                return род
    return "не объявлен"


def сценарии_сторожа(имя: str, root: Path = ROOT) -> list[tuple[str, str]]:
    """[(сценарий, род)] — объявления, которые зовут ЭТОГО сторожа шагом `run:`.

    Набор команд сторожа — это не только его собственный суд: дыру воспроизводит цепочка, и живые
    сценарии всех трёх родов входят в неё наравне (заказ владельца 2026-09-14: «в радиус пишем не
    улику, а набор команд, который через цепочку воспроизводит дыру… и положительные, и
    отрицательные, и разрушительные»).
    """
    зона = root / "tests" / "scenarios"
    объявленные = {с: р for з in _роды_зон(зона).values() for с, р in з.items()}
    найдено: list[tuple[str, str]] = []
    for файл in sorted(зона.glob("*.yaml")):
        текущий = ""
        for строка in файл.read_text(encoding="utf-8", errors="replace").splitlines():
            if строка.startswith("- scenario:"):
                текущий = строка.split(":", 1)[1].strip()
            if текущий and re.search(rf"run:\s*(?:scripts/guards|\.claude/hooks)/{re.escape(имя)}\b",
                                     строка):
                пара = (текущий, объявленные.get(текущий, "род не объявлен"))
                if пара not in найдено:
                    найдено.append(пара)
    return найдено


def шаги(адрес: str, root: Path = ROOT) -> list[dict]:
    """Набор команд одного адреса: [{звено, условие, команда, исход}]. Род пишется до двоеточия."""
    import _chains  # noqa: PLC0415 — по запросу: сторож радиуса гоняется и там, где скилов нет
    род, _, имя = адрес.partition(":")
    if род not in АДРЕСА or not имя:
        raise SystemExit(f"адрес `{адрес}`: род пишется до двоеточия, роды — {', '.join(АДРЕСА)}")
    объявлено = _объявленные_исходы(root)
    собранные: list[dict] = []
    if род == "скил":
        for звено, команды in _chains.набор(имя, root).items():
            for команда in команды:
                собранные.append({"звено": f"скил:{звено}",
                                  "условие": f"цепочка `{имя}` дошла до звена `{звено}`",
                                  "команда": команда})
    elif род == "задача":
        задачи = (_evidence.словарь(root / "scripts" / "guards" / "evidence.yaml")
                  or {}).get("задачи") or {}
        тело = задачи.get(имя)
        if тело is None:
            raise SystemExit(f"типа работы `{имя}` нет; есть: {', '.join(задачи) or '—'}")
        for файл, флаги in (тело.get("сверх") or {}).items():
            for флаг in (флаги if isinstance(флаги, list) else [флаги]):
                собранные.append({"звено": f"сторож:{файл}", "условие": f"тип работы = {имя}",
                                  "команда": f".venv/bin/python scripts/guards/{файл} {флаг}"})
        if тело.get("оси"):
            собранные.append({"звено": "оси", "условие":
                              f"оси типа работы: {', '.join(тело['оси'])[:90]}",
                              "команда": ".venv/bin/python scripts/guards/invariants.py --check"})
        if тело.get("скил"):
            собранные += шаги(f"скил:{тело['скил']}", root)
    elif род == "сторож":
        пары = sorted(флаг for (файл, флаг) in объявлено if файл == имя)
        if not пары:
            raise SystemExit(f"сторож `{имя}` не объявлен ни в одном типе работы — роутить его "
                             f"нечем: объявить в evidence.yaml, раздел `задачи`")
        собранные += [{"звено": f"сторож:{имя}", "условие": f"поднят рукой: {имя} {флаг}",
                       "команда": f".venv/bin/python scripts/guards/{имя} {флаг}"}
                      for флаг in пары]
        собранные += [{"звено": f"сценарий:{сценарий}", "условие": f"род сценария: {род}",
                       "команда": f"VPM_SCENARIO={сценарий} .venv/bin/python "
                                  f"tests/scenarios/test_scenarios.py"}
                      for сценарий, род in сценарии_сторожа(имя, root)]
    elif род == "ось":
        sys.path.insert(0, str(root / "scripts" / "guards"))
        import invariants  # noqa: PLC0415 — заголовки осей живут у сторожа осей, а не здесь
        заголовки = {и for и, _ in invariants.HARD} | {с[0] for с in invariants.RATCHETS}
        if имя not in заголовки:
            raise SystemExit(f"оси «{имя}» нет среди {len(заголовки)} объявленных")
        собранные.append({"звено": f"ось:{имя}", "условие": f"ось «{имя}» в наборе задачи",
                          "команда": ".venv/bin/python scripts/guards/invariants.py --check"})
    else:
        путь, _, узел_имя = имя.partition("#")
        узлы = {у["узел"]: у for у in цепочка(путь, root)}
        if узел_имя and узел_имя not in узлы:
            raise SystemExit(f"{путь}: узла `{узел_имя}` нет; есть {', '.join(узлы)[:120]}")
        for узел in ([узлы[узел_имя]] if узел_имя else list(узлы.values())):
            if узел["команда"]:
                роды = " ".join(f"{р[:3]} {n}" for р, n in узел["роды"].items())
                собранные.append({"звено": f"файл:{путь}#{узел['узел']}",
                                  "условие": f"узел покрыт сценариями ({роды})",
                                  "команда": узел["команда"], "исход": "судит"})
    for шаг in собранные:
        шаг.setdefault("исход", род_исхода(шаг["команда"], объявлено))
    return собранные


def маршрут(адреса: list[str], root: Path = ROOT) -> list[dict]:
    """Комбинация цепочек: шаги слиты по КОМАНДЕ, и общая несёт ВСЕ свои условия — иначе вторая
    цепочка исчезает при дедупе, и «две цепочки держатся на одном прогоне» никто не увидит."""
    сводка: dict[str, dict] = {}
    for адрес in адреса:
        for шаг in шаги(адрес, root):
            уже = сводка.get(шаг["команда"])
            if уже:
                уже["условия"].append(шаг["условие"])
                уже["звенья"].append(шаг["звено"])
            else:
                сводка[шаг["команда"]] = {**шаг, "условия": [шаг["условие"]],
                                          "звенья": [шаг["звено"]]}
    return list(сводка.values())


def _выполнить(команда: str, root: Path = ROOT) -> tuple[int, str]:
    """Прогон без оболочки: присваивания уходят в окружение, остальное — argv."""
    if ПОДСТАНОВКА.search(команда):
        return -1, "подстановка `<…>` — это заготовка: подставить и запустить руками"
    if ОБОЛОЧКА.search(команда):
        return -1, "знаки оболочки в команде из текста — не запускаю"
    куски = команда.split()
    env = dict(os.environ)
    while куски and "=" in куски[0] and not куски[0].startswith("-"):
        ключ, _, значение = куски.pop(0).partition("=")
        env[ключ] = значение
    if not куски:
        return -1, "в команде не осталось исполняемого"
    try:
        r = subprocess.run(куски, cwd=str(root), env=env, capture_output=True,
                           text=True, timeout=900, check=False)
    except (OSError, subprocess.SubprocessError) as e:  # noqa: BLE001 — упавший запуск чужой
        # команды не имеет права ронять маршрут: он обязан назвать сломавшееся звено сам
        return -1, f"не запустилась: {e}"
    хвост = (r.stdout or r.stderr or "").strip().splitlines()
    return r.returncode, (хвост[-1][:120] if хвост else "")


def показ_маршрута(адреса: list[str], root: Path = ROOT, прогнать: bool = False) -> int:
    """Набор команд комбинации как МАРШРУТ: условие → команда → что делать по исходу."""
    шаги_маршрута = маршрут(адреса, root)
    if not шаги_маршрута:
        print(f"{' + '.join(адреса)}: ни одной команды — поднять эту комбинацию нечем")
        return 1
    общих = sum(1 for ш in шаги_маршрута if len(set(ш["условия"])) > 1)
    без_исхода = [ш for ш in шаги_маршрута if ш["исход"] == "не объявлен"]
    судящих = [ш for ш in шаги_маршрута if ш["исход"] == "судит"]
    print(f"маршрут: {' + '.join(адреса)}")
    print(f"шагов {len(шаги_маршрута)} · общих {общих} · судят {len(судящих)} · "
          f"справка {len(шаги_маршрута) - len(судящих) - len(без_исхода)} · "
          f"исход не объявлен {len(без_исхода)}\n")
    сломался = ""
    for n, шаг in enumerate(шаги_маршрута, 1):
        print(f"{n:2} [{шаг['исход']}] {' · '.join(sorted(set(шаг['звенья'])))}")
        for условие in sorted(set(шаг["условия"])):
            print(f"     если: {условие}")
        print(f"     {шаг['команда']}")
        if шаг["исход"] == "судит":
            print("     код 0 → дальше · код ≠ 0 → СТОП, звено названо")
        elif шаг["исход"] == "справка":
            print("     код любой → дальше: это справка, вердикта не несёт")
        else:
            print("     не ветвится и не запускается: объявить в evidence.yaml `сверх`/`спросить`")
        if прогнать and not сломался:
            if шаг["исход"] == "не объявлен":
                print("     ⊘ пропущен: смысл кода возврата не объявлен")
                continue
            код, хвост = _выполнить(шаг["команда"], root)
            print(f"     → код {код} · {хвост}")
            if код != 0 and шаг["исход"] == "судит":
                сломался = f"{' · '.join(sorted(set(шаг['звенья'])))} — {шаг['команда']}"
    if без_исхода:
        print(f"\nдыра: {len(без_исхода)} команд без объявленного исхода — маршрут их не ветвит")
    if прогнать:
        print(f"\nсломалось звено: {сломался}" if сломался else "\nмаршрут пройден целиком")
        return 1 if сломался else 0
    return 0


def показ_файла(rel: str, root: Path = ROOT) -> int:
    """Смыкание цепочек на ОДНОМ файле: кто про него знает, кто его судит, чем он поднимается.

    Четыре рода связи в одном ответе — скилы, судьи с их состоянием, узлы сценариев и событие,
    которое этот файл рождает. Порознь они были и раньше; вопрос «что вокруг ЭТОГО файла» не
    отвечался ничем, и набор цепочек висел над деревом целиком.
    """
    import _chains  # noqa: PLC0415 — по запросу: сторож радиуса гоняется и там, где скилов нет
    if not (root / rel).is_file():
        print(f"{rel}: файла нет — карту вокруг него строить не из чего")
        return 1
    print(f"── карта файла: {rel}")

    скилы = _chains.скилы_о_файле(rel, root)
    print(f"\nскилы, знающие о нём: {len(скилы)}")
    for имя, разделы in sorted(скилы.items()):
        print(f"   {имя}: {', '.join(разделы)[:90]}")
    if not скилы:
        print("   никто: правку здесь ни один скил не сопровождает — инструкции о нём нет")

    sys.path.insert(0, str(root / "scripts" / "guards"))
    import patrol  # noqa: PLC0415 — наряд владеет состояниями; вторая копия расходилась бы с ним
    print("\nсудьи и их состояние по этому файлу:")
    код_состояний = patrol.состояния(rel, root)

    print("\nузлы сценариев:")
    try:
        узлы = цепочка(rel, root)
        покрыты = [у for у in узлы if у["сценарии"]]
        print(f"   узлов {len(узлы)} · покрыто {len(покрыты)} · "
              f"набор: --набор файл:{rel}")
    except SystemExit as отказ:
        print(f"   {отказ}")

    улики = _evidence.rods_of([rel])
    print(f"\nсобытие, которое он рождает: {', '.join(улики) or 'не выводится из объявления'}")
    if улики:
        print(f"   наряд на него: .venv/bin/python scripts/guards/patrol.py --улика {улики[0]}")
    return код_состояний


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
    parser.add_argument("--цепочка", metavar="ФАЙЛ",
                        help="таблица радиуса по файлу: узел · роды покрытия · дыры")
    parser.add_argument("--узел", metavar="ИМЯ",
                        help="три набора одного узла и команда на каждый")
    parser.add_argument("--скил", metavar="ИМЯ",
                        help="цепочка инструкций скила: раздел · кого зовёт · чем кончается")
    parser.add_argument("--файл", metavar="ПУТЬ",
                        help="карта вокруг файла: скилы · судьи и их состояние · узлы · событие")
    parser.add_argument("--связи", action="store_true",
                        help="таблица связей цепочек: кто кого зовёт, сироты и тупики")
    parser.add_argument("--набор", nargs="+", metavar="АДРЕС",
                        help="набор команд адреса или КОМБИНАЦИИ: скил: задача: сторож: ось: файл:")
    parser.add_argument("--прогнать", action="store_true",
                        help="исполнить маршрут набора: судящий красный останавливает и называет звено")
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
    if getattr(args, "файл"):
        return показ_файла(getattr(args, "файл"), args.root)
    if getattr(args, "набор"):
        return показ_маршрута(getattr(args, "набор"), args.root, getattr(args, "прогнать"))
    if getattr(args, "скил"):
        return показ_скила(getattr(args, "скил"), getattr(args, "узел") or "", args.root)
    if getattr(args, "связи"):
        return показ_связей(args.root)
    if getattr(args, "цепочка") and getattr(args, "узел"):
        return показ_узла(getattr(args, "цепочка"), getattr(args, "узел"), args.root)
    if getattr(args, "цепочка"):
        return показ_цепочки(getattr(args, "цепочка"), args.root)
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
                getattr(args, "класс") is not None, getattr(args, "цепочка"),
                getattr(args, "скил"), getattr(args, "связи"), getattr(args, "набор"),
                getattr(args, "файл")]):
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
