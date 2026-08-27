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
            def run(smap=smap, path=path, number=number):
                with live_server() as srv:
                    srv.rpc.timeout = 180.0
                    MapRunner().walk_path(smap, path, number, Runner(srv, steps=STEPS))
            remember(f"{smap.id}#путь{number}", _coverage_of(run))

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
        measured = path.startswith(("core/", "tools/")) or path == "server.py"
        print(f"  {target}: " + ("файл не исполняется НИ ОДНИМ сценарием — правка здесь сценариям невидима"
                                 if measured else "вне области замера (мерятся core/, tools/, server.py)"))
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


def affected() -> int:
    """Сценарии, задетые ТЕКУЩЕЙ правкой, и строки, за которыми не стоит ни один сценарий.

    Печатает готовую команду точечного прогона: полная матрица идёт минуты, задетая часть — секунды.
    """
    diff = subprocess.run(["git", "diff", "-U0", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout
    radius, current = _load(), ""
    hit: set[str] = set()
    blind: list[str] = []
    touched_lines: dict[str, set[int]] = {}
    for row in diff.splitlines():
        if row.startswith("+++ b/"):
            current = row[len("+++ b/"):]
        head = HUNK.match(row)
        if not (head and current.endswith(".py")):
            continue
        measured = current.startswith(("core/", "tools/")) or current == "server.py"
        if not measured:
            continue
        covered = radius.get(str(Path(current))) or {}
        start, count = int(head.group(1)), int(head.group(2) or 1)
        for line in range(start, start + max(count, 1)):
            touched_lines.setdefault(current, set()).add(line)
            names = {name for name, lines in covered.items() if line in lines}
            hit |= names
            if not names:
                blind.append(f"{current}:{line}")
    if blind:
        print(f"  ⚠ строк без единого сценария: {len(blind)} — {', '.join(blind[:5])}"
              + (" …" if len(blind) > 5 else ""))
        print("    правка в зоне, которую тесты не исполняют: сначала сценарий, потом код")
    if hit:
        print(f"  задетые сценарии ({len(hit)}): {', '.join(sorted(hit))}")
        print(f"    точечный прогон: VPM_SCENARIO='{','.join(sorted(hit))}' python3 tests/scenarios/test_scenarios.py")
    if not hit and not blind:
        print("  Правка не касается измеряемой зоны (core/, tools/, server.py) — прогонять нечего.")
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
    sites = (ask or _symbol_graph.callers)(name.rpartition(".")[2] for _, name in touched)
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
    else:
        print("  по графу вызовов молчащих вызывающих у тронутых функций нет")
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


def _measured_files() -> list[str]:
    files = [str(p.relative_to(ROOT)) for p in (ROOT / "core").rglob("*.py")]
    files += [str(p.relative_to(ROOT)) for p in (ROOT / "tools").rglob("*.py")]
    return sorted(f for f in files + ["server.py"] if "__pycache__" not in f)


def blind() -> int:
    """Функции сервера, которых не исполняет НИ ОДИН сценарий, — размер молчания карты.

    Считаем ИМЕНА, а не строки: номера сдвигаются от любой правки, и построчное число живёт ровно до
    следующего касания файла — такой улике верить нельзя. Имя переживает правку, а новая непокрытая
    функция всё так же растит счёт. Код 2 — улики нет (карта не собрана), это не то же самое, что
    «молчание выросло»: ложное обвинение выключает сторожа быстрее, чем его отсутствие.
    """
    if not COVERED.exists():
        print(f"── улики нет: {COVERED} не собран. Пересобери карту — `--build`.")
        return 2
    covered = json.loads(COVERED.read_text(encoding="utf-8"))
    worst: list[tuple[int, str]] = []
    total = 0
    for rel in _measured_files():
        silent = set(_functions(rel)) - set(covered.get(rel) or [])
        total += len(silent)
        if silent:
            worst.append((len(silent), rel))
    limit = int(BLIND_BASELINE.read_text(encoding="utf-8").strip()) if BLIND_BASELINE.exists() else total
    print(f"── функций вне всех сценариев: {total} при потолке {limit}")
    for missed, rel in sorted(worst, reverse=True)[:8]:
        print(f"   {missed:4d}  {rel}")
    if "--bless" in sys.argv:
        BLIND_BASELINE.write_text(f"{total}\n", encoding="utf-8")
        print(f"   потолок опущен до {total}")
        return 0
    if total > limit:
        print(f"   ✗ молчание выросло на {total - limit}: появились функции, которых не исполняет "
              "ни один сценарий. Объяви сценарий, потом пересобери карту (`--build`) и опусти "
              "потолок (`--blind --bless`) — до пересборки новая функция и должна числиться молчащей")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--query", metavar="ФАЙЛ[:СТРОКА]")
    parser.add_argument("--changed", action="store_true")
    parser.add_argument("--check-routes", action="store_true")
    parser.add_argument("--affected", action="store_true")
    parser.add_argument("--blind", action="store_true")
    parser.add_argument("--bless", action="store_true")
    args = parser.parse_args()
    if args.build:
        build()
    if args.query:
        query(args.query)
    if args.changed:
        changed()
    if args.affected:
        return affected()
    if args.check_routes:
        return check_routes()
    if args.blind:
        return blind()
    if not any([args.build, args.query, args.changed, args.check_routes, args.affected, args.blind]):
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
