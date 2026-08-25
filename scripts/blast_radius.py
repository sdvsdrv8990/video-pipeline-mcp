#!/usr/bin/env python3
"""scripts/blast_radius.py — что висит на этом месте кода: карта «строка → сценарии».

Отвечает ДО правки: «собираюсь тронуть вот здесь — что это заденет и чем это проверено». Покрытие
снимается по одному сценарию на сервер, поэтому строка знает своих сценариев поимённо, а не
«покрыта вообще». Сборка медленная и в гейт не входит — инструмент запускается руками.

    python3 scripts/blast_radius.py --build                 # собрать карту
    python3 scripts/blast_radius.py --query core/paths.py:42
    python3 scripts/blast_radius.py --changed               # по незакоммиченной правке
    python3 scripts/blast_radius.py --affected              # какие сценарии гнать ИМЕННО сейчас
    python3 scripts/blast_radius.py --check-routes          # рубеж маршрута реально ИСПОЛНЯЛСЯ
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Импорты ниже корня: харнесс живёт в `tests/`, и путь к нему добавляется выше.
import yaml  # noqa: E402
from coverage.parser import PythonParser  # noqa: E402

from tests.harness import live_server  # noqa: E402
from tests.harness.scenario import Runner, Vocabulary, load, scenario_files  # noqa: E402
from tests.harness.scenario_map import MapRunner, load_map, plan  # noqa: E402
from tests.scenarios.steps import STEPS  # noqa: E402

SCENARIOS = ROOT / "tests" / "scenarios"
RADIUS = ROOT / "tests" / ".blast" / "radius.json"
BLIND_BASELINE = Path(__file__).with_name("blast_blind_baseline.txt")
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
    print(f"\nКарта радиуса: {RADIUS} · файлов {len(radius)}")
    return radius


def _load() -> dict:
    if not RADIUS.exists():
        sys.exit(f"Карты нет: {RADIUS}. Собери её — `python3 scripts/blast_radius.py --build`.")
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
    return 1 if blind else 0


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
    """Строки сервера, которых не исполняет НИ ОДИН сценарий, — размер молчания карты.

    Храповик: вниз можно, вверх нет. Новый непокрытый путь растит число, и гейт краснеет — иначе
    карта молчит именно о том, чего в неё не положили.
    """
    radius = _load()
    worst: list[tuple[int, str]] = []
    total = 0
    for rel in _measured_files():
        covered: set[int] = set()
        for executed in (radius.get(rel) or {}).values():
            covered.update(executed)
        missed = len(_statements(rel) - covered)
        total += missed
        if missed:
            worst.append((missed, rel))
    limit = int(BLIND_BASELINE.read_text(encoding="utf-8").strip()) if BLIND_BASELINE.exists() else total
    print(f"── строк вне всех сценариев: {total} при потолке {limit}")
    for missed, rel in sorted(worst, reverse=True)[:8]:
        print(f"   {missed:5d}  {rel}")
    if "--bless" in sys.argv:
        BLIND_BASELINE.write_text(f"{total}\n", encoding="utf-8")
        print(f"   потолок опущен до {total}")
        return 0
    if total > limit:
        print(f"   ✗ молчание выросло на {total - limit}: правка добавила код, который не исполняет "
              "ни один сценарий — объяви сценарий или сузь правку")
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
