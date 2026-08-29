#!/usr/bin/env python3
"""scripts/guards/what_if.py — реальность против ожиданий: что даст правка, ДО того как её принять.

Намерение (`intent.yaml`) объявляет, что меняем, где, зачем и что обязано сменить цвет — сценарий,
маршрут потока данных или проверка сторожа. Кандидатный патч раскатывается в ОТДЕЛЬНОМ worktree,
ВСЕ ТРИ карты гоняются дважды — на `HEAD` и на патче, — и отчёт кладётся в три колонки:

    заявленное сбылось · ИЗМЕНИЛОСЬ НЕЗАЯВЛЕННОЕ (скрытый риск) · заявленное не сбылось

Третья колонка ловит «фикс не работает», вторая — «фикс задел соседнее». Предсказать поведение
ненаписанного кода нельзя ничем; здесь оно ИЗМЕРЯЕТСЯ на кандидате, пока он не в основной ветке.

    python3 scripts/guards/what_if.py --intent intent.yaml            # патч = незакоммиченная правка
    python3 scripts/guards/what_if.py --intent intent.yaml --patch fix.diff
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BEHAVIOUR = ("tests/scenarios/test_scenarios.py", "tests/routes/test_routes.py")
TESTS_CATALOG = ("tests", "CATALOG.md")
GUARD_IN_ZONE = re.compile(r"scripts/guards/[\w.]+\.py|\.claude/hooks/")
LINE = re.compile(r"^\s{2}([✓✗]) (.+?)(?:\s{2}→ .*)?$")


def suites(root: Path) -> list[str]:
    """Карты цикла: поведение сервера, маршруты потоков и НАБОРЫ, судящие машинерию.

    Машинерия — это сторожа И хуки: сервер не импортирует ни тех, ни других, поэтому правка хука
    без этой строки читалась бы как «ничего не изменилось» ровно так же, как когда-то правка
    сторожа.

    Третья карта берётся из объявления (`tests/CATALOG.md`: строка набора, зона которого называет
    `scripts/guards/*.py`), а не выводится из дерева. Вывод пробовали: `test_findings_count.py`
    грузит сторожа компиляцией из исходника, поэтому ни импорта, ни строки-пути в нём нет, и
    производная теряла его молча. Роспись, которая тихо недосчитывает, хуже объявленной.
    """
    homes = []
    for line in root.joinpath(*TESTS_CATALOG).read_text(encoding="utf-8").splitlines():
        cells = line.split("|")
        if not line.startswith("|") or len(cells) < 3 or not GUARD_IN_ZONE.search(cells[2]):
            continue
        name = cells[1].strip().strip("*").strip("`").strip()
        path = next(root.glob(f"tests/**/{name}"), None)
        if path is None:
            sys.exit(f"tests/CATALOG.md объявляет `{name}` домом сторожа, а файла нет: карта "
                     f"сторожей неполна, и сравнение молча пропустит их правки")
        homes.append(str(path.relative_to(root)))
    return list(BEHAVIOUR) + sorted(homes)
INTENT_KEYS = {"intent", "where", "why", "expect"}
EXPECT_KEYS = {"scenario", "becomes", "why"}


def verdicts(cwd: Path) -> dict[str, bool]:
    """Прогон обеих карт в дереве `cwd` → {метка проверки: прошла}.

    Карта сценариев отвечает «что делает система», карта маршрутов — «куда течёт то, что она
    сказала клиенту». Правка умеет сломать поток, не тронув ни одного сценария, поэтому сравнение
    до/после без маршрутов молчало бы ровно там, где дороже всего.
    """
    out: dict[str, bool] = {}
    seen: dict[str, int] = {}
    for suite in suites(cwd):
        if not (cwd / suite).exists():
            continue
        done = subprocess.run([sys.executable, suite], cwd=cwd, capture_output=True,
                              text=True, timeout=3600)
        rows = [found for row in done.stdout.splitlines() if (found := LINE.match(row))]
        if not rows:
            sys.exit(f"{suite} в {cwd} не дал ни одной проверки:\n"
                     f"{done.stdout[-2000:]}\n{done.stderr[-1000:]}")
        for found in rows:
            label = found.group(2).strip()
            # Решётка печатает одну метку на несколько прогонов (`#путь1 · … → успех` × 4). Без
            # порядкового номера словарь оставил бы последнюю, и смена цвета остальных пропала бы.
            seen[label] = seen.get(label, 0) + 1
            out[label if seen[label] == 1 else f"{label} ×{seen[label]}"] = found.group(1) == "✓"
    if not out:
        sys.exit(f"Прогон в {cwd} не дал ни одной проверки — сравнивать нечего")
    return out


def load_intent(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    unknown = set(data) - INTENT_KEYS
    if unknown:
        sys.exit(f"{path.name}: неизвестные ключи {sorted(unknown)} (разрешены {sorted(INTENT_KEYS)})")
    for field in ("intent", "where", "why"):
        if not data.get(field):
            sys.exit(f"{path.name}: нет `{field}` — намерение без него не проверяемо")
    # Пустой `expect` — не пропуск, а УТВЕРЖДЕНИЕ «цвет не меняет ничего»: у сноса мёртвого и у
    # переноса это и есть весь заявленный итог, и проверяет его вторая колонка. Отличать надо от
    # ОТСУТСТВИЯ ключа: там автор про поведение не сказал ничего, и сравнивать не с чем.
    if "expect" not in data:
        sys.exit(f"{path.name}: нет `expect` — объяви, что сменит цвет, либо `expect: []`, "
                 f"если утверждаешь, что не сменит ничего")
    if not isinstance(data["expect"], list):
        sys.exit(f"{path.name}: `expect` — список ожиданий либо пустой список")
    for item in data["expect"]:
        unknown = set(item) - EXPECT_KEYS
        if unknown:
            sys.exit(f"{path.name}.expect: неизвестные ключи {sorted(unknown)}")
        if item.get("becomes") not in ("red", "green"):
            sys.exit(f"{path.name}.expect: `becomes` — только `red` или `green` (у {item.get('scenario')!r})")
    return data


def candidate_tree(patch: str) -> Path:
    """Копия HEAD в отдельном worktree с наложенным патчем. Рабочее дерево не трогаем вовсе."""
    tmp = Path(tempfile.mkdtemp(prefix="vpm_whatif_"))
    tree = tmp / "tree"
    subprocess.run(["git", "worktree", "add", "--detach", str(tree), "HEAD"],
                   cwd=ROOT, capture_output=True, text=True, check=True)
    if patch.strip():
        applied = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=tree,
                                 input=patch, capture_output=True, text=True)
        if applied.returncode != 0:
            drop_tree(tree)
            sys.exit(f"Патч не накладывается на HEAD:\n{applied.stderr[-1500:]}")
    return tree


def drop_tree(tree: Path) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(tree)],
                   cwd=ROOT, capture_output=True, text=True)
    shutil.rmtree(tree.parent, ignore_errors=True)


def _named(label: str) -> str:
    """Имя, которым намерение зовёт проверку: у сценария оно до шага (` · `), у маршрута — до
    рубежа (`: `). Одним разделителем не обойтись, а промах по имени немой."""
    return label.split(" · ")[0].split(": ")[0]


def report(intent: dict, before: dict[str, bool], after: dict[str, bool]) -> int:
    common = set(before) & set(after)
    turned_red = {label for label in common if before[label] and not after[label]}
    turned_green = {label for label in common if not before[label] and after[label]}
    appeared, vanished = set(after) - set(before), set(before) - set(after)

    declared = {str(item["scenario"]): str(item["becomes"]) for item in intent["expect"]}
    got: dict[str, str] = {}
    for label in turned_red:
        got.setdefault(_named(label), "red")
    for label in turned_green:
        got.setdefault(_named(label), "green")

    print(f"\n═══ НАМЕРЕНИЕ: {intent['intent']} ═══")
    print(f"  где: {intent['where']}\n  зачем: {intent['why']}")
    print(f"  проверок сравнено {len(common)}; сменили цвет {len(turned_red | turned_green)}")

    print("\n── ЗАЯВЛЕННОЕ СБЫЛОСЬ ──")
    fulfilled = [name for name, color in declared.items() if got.get(name) == color]
    for name in fulfilled:
        print(f"  ✓ {name} → {declared[name]}")
    if not declared:
        print("  заявлено, что цвет не меняет НИЧЕГО — весь вердикт во второй колонке")
    elif not fulfilled:
        print("  (ничего)")

    print("\n── ИЗМЕНИЛОСЬ НЕЗАЯВЛЕННОЕ (скрытый риск) ──")
    surprise = {name: color for name, color in got.items() if name not in declared}
    for name, color in sorted(surprise.items()):
        print(f"  ⚠ {name} → {color} — намерение об этом не говорило")
    for label in sorted(appeared | vanished):
        print(f"  ⚠ проверка {'появилась' if label in appeared else 'исчезла'}: {label}")
    if not surprise and not (appeared | vanished):
        print("  (ничего — правка задела ровно то, что заявлено)")

    print("\n── ЗАЯВЛЕННОЕ НЕ СБЫЛОСЬ ──")
    gone = {_named(label) for label in vanished}
    missed = [(name, color) for name, color in declared.items() if got.get(name) != color]
    for name, color in missed:
        if name in gone:
            # Исчезнувшая проверка — не «цвет не сменился»: наблюдения не стало вовсе, и принять
            # это за «ничего не поменялось» значит принять отсутствие улики за чистый результат.
            print(f"  ✗ {name}: ждали {color}, а проверки ИСЧЕЗЛИ — улики нет, это не «без изменений»")
        else:
            print(f"  ✗ {name}: ждали {color}, получили {got.get(name) or 'без изменений'}")
    if not missed:
        print("  (ничего)")

    return 0 if not (missed or surprise or appeared or vanished) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", required=True, type=Path)
    parser.add_argument("--patch", type=Path, help="файл диффа; по умолчанию — незакоммиченная правка")
    args = parser.parse_args()

    intent = load_intent(args.intent)
    patch = args.patch.read_text(encoding="utf-8") if args.patch else subprocess.run(
        ["git", "diff", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout
    if not patch.strip():
        sys.exit("Патча нет: незакоммиченных правок не найдено, а --patch не задан.")

    print("Прогон на HEAD (базовая линия)…")
    base = candidate_tree("")
    try:
        before = verdicts(base)
    finally:
        drop_tree(base)

    print("Прогон на кандидате…")
    tree = candidate_tree(patch)
    try:
        after = verdicts(tree)
    finally:
        drop_tree(tree)

    return report(intent, before, after)


if __name__ == "__main__":
    sys.exit(main())
