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
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import yaml

import _stamp

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
# Исходы сравнения. Смена цвета — не единственный род: правка, ВЕСЬ смысл которой в новой проверке,
# цвет не меняет ни у кого, и без `appeared` она обречена падать в «скрытый риск», а третья колонка
# врать «заявленное не сбылось». Исчезновение объявляется так же — иначе снятую проверку не отличить
# от переименованной.
OUTCOMES = {"red", "green", "appeared", "vanished"}
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
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as beda:
        # Метки проверок часто несут `": "` — законно с тех пор, как имя стало точным. В YAML это
        # начало отображения, поэтому незакавыченная метка роняет разбор чужим стеком на 20 строк.
        место = getattr(beda, "problem_mark", None)
        где = f"строка {место.line + 1}, колонка {место.column + 1}" if место else "место не названо"
        sys.exit(f"{path.name}: намерение не разбирается ({где}) — {getattr(beda, 'problem', beda)}.\n"
                 f"Чаще всего это двоеточие с пробелом внутри незакавыченной строки: закавычь "
                 f"значение целиком — `scenario: \"метка: с рубежом\"`.")
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
        if item.get("becomes") not in OUTCOMES:
            sys.exit(f"{path.name}.expect: `becomes` — одно из {sorted(OUTCOMES)} "
                     f"(у {item.get('scenario')!r})")
    return data


def unspoken(intent: dict, root: Path = ROOT) -> list[str]:
    """Метки намерения, которых НЕТ ни в одном исходнике набора: почти всегда опечатка.

    Предупреждение, а не отказ: метка бывает собрана из переменных, и запрещать такую значило бы
    запрещать целый род проверок. Смысл в том, чтобы опечатка стоила секунды, а не двух прогонов.
    """
    тексты = []
    for путь in sorted((root / "tests").rglob("*.py")) + sorted((root / "tests").rglob("*.yaml")):
        try:
            тексты.append(путь.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    свод = "\n".join(тексты)
    return [str(item["scenario"]) for item in intent["expect"]
            if str(item["scenario"]) not in свод]


def untracked(root: Path = ROOT) -> list[str]:
    """НОВЫЕ файлы рабочего дерева. `git diff` их не содержит вовсе, поэтому правка, добавляющая
    файл, доезжала в дерево кандидата половиной: ссылки на новый модуль есть, самого модуля нет.
    Игнорируемое не берём — это артефакты прогонов, а не правка."""
    done = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                          cwd=root, capture_output=True, text=True)
    return sorted(item for item in done.stdout.split("\0") if item)


def carry_untracked(tree: Path, root: Path = ROOT) -> list[str]:
    """Перенести новые файлы в дерево кандидата. Возвращает перенесённое — молчаливый перенос
    нечем отличить от отсутствия новых файлов."""
    carried = []
    for rel in untracked(root):
        source = root / rel
        if not source.is_file():
            continue
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        carried.append(rel)
    return carried


def candidate_tree(patch: str, root: Path = ROOT, carry: bool = True) -> Path:
    """Копия HEAD в отдельном worktree с наложенным патчем. Рабочее дерево не трогаем вовсе.

    `carry` разводит два дерева: новый файл принадлежит ПРАВКЕ, а не HEAD. Занесённый в базовую
    линию, он делает её химерой — код HEAD плюс файлы правки — и красит там сторожа (у нового
    модуля на HEAD нет ни одного читателя). Ровно так и вышло: цикл поймал это сменой цвета,
    о которой намерение не говорило.

    `root` — параметр, а не константа: связь «дерево собрано И новые файлы в нём» проверяется
    набором на своём временном репозитории, иначе тест бьёт по частям, а ломается связь.
    """
    tmp = Path(tempfile.mkdtemp(prefix="vpm_whatif_"))
    tree = tmp / "tree"
    subprocess.run(["git", "worktree", "add", "--detach", str(tree), "HEAD"],
                   cwd=root, capture_output=True, text=True, check=True)
    if patch.strip():
        # `--index`: без него удалённый правкой файл исчезает с диска, но остаётся в `git ls-files`,
        # и сторож, берущий цели у git, умирает чтением несуществующего — вся его карта пропадает
        # из сравнения как «проверка исчезла». Ровно так правка с переездом модуля и не судилась.
        applied = subprocess.run(["git", "apply", "--index", "--whitespace=nowarn", "-"], cwd=tree,
                                 input=patch, capture_output=True, text=True)
        if applied.returncode != 0:
            drop_tree(tree, root)
            sys.exit(f"Патч не накладывается на HEAD:\n{applied.stderr[-1500:]}")
    carried = carry_untracked(tree, root) if carry else []
    if carried:
        # Та же причина с другой стороны: перенесённый файл невидим для целей, взятых у git.
        subprocess.run(["git", "add", "--", *carried], cwd=tree, capture_output=True, text=True)
        print(f"  новых файлов перенесено: {len(carried)} ({', '.join(carried[:4])}"
              f"{' …' if len(carried) > 4 else ''})")
    return tree


def drop_tree(tree: Path, root: Path = ROOT) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(tree)],
                   cwd=root, capture_output=True, text=True)
    shutil.rmtree(tree.parent, ignore_errors=True)


def _named(label: str) -> str:
    """Имя, которым намерение зовёт проверку: у сценария оно до шага (` · `), у маршрута — до
    рубежа (`: `). Одним разделителем не обойтись, а промах по имени немой."""
    return label.split(" · ")[0].split(": ")[0]


def compare(intent: dict, before: dict[str, bool], after: dict[str, bool]) -> dict:
    """Сверка заявленного с полученным. Одна на печать и на подпись: два счёта разошлись бы."""
    common = set(before) & set(after)
    turned_red = {label for label in common if before[label] and not after[label]}
    turned_green = {label for label in common if not before[label] and after[label]}
    appeared, vanished = set(after) - set(before), set(before) - set(after)

    # У одного имени исходов бывает НЕСКОЛЬКО (вставка шага разом рождает новые проверки и уносит
    # старые под новым номером), поэтому и заявленное, и полученное — множества, а не одно значение.
    declared: dict[str, set[str]] = {}
    for item in intent["expect"]:
        declared.setdefault(str(item["scenario"]), set()).add(str(item["becomes"]))

    def key(label: str) -> str:
        """Точное имя, если намерение зовёт проверку так; иначе — нормализованное.

        Метка с рубежом внутри иначе непроизносима: `_named` срезала бы её по первому
        разделителю, и заявленное не совпало бы с полученным НИКОГДА — правка выглядела бы
        одновременно «не сбылась» и «скрытый риск».
        """
        return label if label in declared else _named(label)

    got: dict[str, set[str]] = {}
    for labels, outcome in ((turned_red, "red"), (turned_green, "green"),
                            (appeared, "appeared"), (vanished, "vanished")):
        for label in labels:
            got.setdefault(key(label), set()).add(outcome)

    fulfilled = [(name, out) for name, outs in sorted(declared.items())
                 for out in sorted(outs) if out in got.get(name, set())]
    surprise = {(name, out) for name, outs in got.items() for out in outs
                if out not in declared.get(name, set())}
    missed = [(name, out) for name, outs in sorted(declared.items())
              for out in sorted(outs) if out not in got.get(name, set())]
    news = [(label, "appeared" if label in appeared else "vanished")
            for label in sorted(appeared | vanished)]
    return {"common": len(common), "colour": len(turned_red | turned_green),
            "declared": declared, "got": got, "fulfilled": fulfilled, "surprise": surprise,
            "missed": missed, "news": [(label, out) for label, out in news
                                       if (key(label), out) in surprise],
            "gone": {key(label) for label in vanished}}


def report(intent: dict, before: dict[str, bool], after: dict[str, bool]) -> int:
    sums = compare(intent, before, after)
    declared, got, surprise = sums["declared"], sums["got"], sums["surprise"]

    print(f"\n═══ НАМЕРЕНИЕ: {intent['intent']} ═══")
    print(f"  где: {intent['where']}\n  зачем: {intent['why']}")
    print(f"  проверок сравнено {sums['common']}; сменили цвет {sums['colour']}")

    print("\n── ЗАЯВЛЕННОЕ СБЫЛОСЬ ──")
    for name, out in sums["fulfilled"]:
        print(f"  ✓ {name} → {out}")
    if not declared:
        print("  заявлено, что цвет не меняет НИЧЕГО — весь вердикт во второй колонке")
    elif not sums["fulfilled"]:
        print("  (ничего)")

    print("\n── ИЗМЕНИЛОСЬ НЕЗАЯВЛЕННОЕ (скрытый риск) ──")
    for name, out in sorted(surprise):
        if out in ("red", "green"):
            print(f"  ⚠ {name} → {out} — намерение об этом не говорило")
    for label, outcome in sums["news"]:
        print(f"  ⚠ проверка {'появилась' if outcome == 'appeared' else 'исчезла'}: {label}")
    if not surprise:
        print("  (ничего — правка задела ровно то, что заявлено)")

    print("\n── ЗАЯВЛЕННОЕ НЕ СБЫЛОСЬ ──")
    for name, out in sums["missed"]:
        if name in sums["gone"] and out not in ("vanished",):
            # Исчезнувшая проверка — не «цвет не сменился»: наблюдения не стало вовсе, и принять
            # это за «ничего не поменялось» значит принять отсутствие улики за чистый результат.
            print(f"  ✗ {name}: ждали {out}, а проверки ИСЧЕЗЛИ — улики нет, это не «без изменений»")
        else:
            print(f"  ✗ {name}: ждали {out}, получили {sorted(got.get(name, set())) or 'без изменений'}")
    if not sums["missed"]:
        print("  (ничего)")

    return 0 if not (sums["missed"] or surprise) else 1


def stamp_fields(intent: dict, sums: dict, path: Path, секунд: float = 0.0) -> dict:
    """Состав подписи цикла. Отдельно от печати, потому что подпись читает ЧЕЛОВЕК в чужой сессии.

    Именем зовётся файл намерения, а не список тронутых путей: по нему прогон повторяют, а список
    путей в подпись не влезает и обрывается на полуслове.
    """
    ждали = Counter(out for outs in sums["declared"].values() for out in outs)
    return {"what": f"цикл по «{path.stem}» — сравнено {sums['common']} проверок, цвет сменили "
                    f"{sums['colour']}, скрытых рисков {len(sums['surprise'])}",
            "intent": path.stem,
            "expected": " + ".join(f"{n}×{out}" for out, n in sorted(ждали.items()))
                        or "цвет не меняется",
            "actual": f"сбылось {len(sums['fulfilled'])} · риска {len(sums['surprise'])} · "
                      f"не сбылось {len(sums['missed'])}",
            "cmd": f"python3 scripts/guards/what_if.py --intent {path}",
            "detail": {"намерение": intent, "сбылось": sums["fulfilled"],
                       "не сбылось": sums["missed"], "риск": sorted(sums["surprise"]),
                       "секунд": round(секунд, 1)}}


MAPS = ("tests", ".journal")


def save_maps(key: str, before: dict[str, bool], after: dict[str, bool], root: Path = ROOT) -> Path:
    """Обе карты рядом с подписью: перестроить отчёт по исправленному намерению — секунда."""
    path = root.joinpath(*MAPS) / f"maps-{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"before": before, "after": after}, ensure_ascii=False), encoding="utf-8")
    return path


def replay(key: str, intent: dict, root: Path = ROOT) -> int:
    """Отчёт по СОХРАНЁННЫМ картам. Не новое измерение — прогона не было, и говорится это прямо."""
    path = root.joinpath(*MAPS) / f"maps-{key}.json"
    if not path.exists():
        sys.exit(f"карт по ключу {key} нет ({path}): перестраивать нечего, нужен прогон")
    карты = json.loads(path.read_text(encoding="utf-8"))
    code = report(intent, карты["before"], карты["after"])
    print(f"\n⟦vpm {key}⟧ отчёт перестроен по сохранённым картам — НОВОГО ПРОГОНА НЕ БЫЛО")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", required=True, type=Path)
    parser.add_argument("--patch", type=Path, help="файл диффа; по умолчанию — незакоммиченная правка")
    parser.add_argument("--replay", help="ключ подписи: перестроить отчёт по сохранённым картам")
    args = parser.parse_args()

    начало = time.monotonic()
    intent = load_intent(args.intent)
    if (немые := unspoken(intent)):
        print("⚠️ метки, которых нет ни в одном наборе (опечатка обойдётся в прогон):")
        for метка in немые:
            print(f"   • {метка}")
    if args.replay:
        return replay(args.replay, intent)
    patch = args.patch.read_text(encoding="utf-8") if args.patch else subprocess.run(
        ["git", "diff", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout
    if not patch.strip() and not untracked():
        # Правка бывает и БЕЗ диффа — из одних новых файлов. Отвергать её как «патча нет» значит
        # не судить самый частый вид прибавления: новый сторож, новый набор, новый модуль.
        sys.exit("Патча нет: ни незакоммиченных правок, ни новых файлов, а --patch не задан.")

    print("Прогон на HEAD (базовая линия)…")
    base = candidate_tree("", carry=False)   # база — ЧИСТЫЙ HEAD, без файлов правки
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

    code = report(intent, before, after)
    поля = stamp_fields(intent, compare(intent, before, after), args.intent,
                        time.monotonic() - начало)
    ключ, подпись = _stamp.sign("цикл", "ВЕРДИКТ", поля["what"], intent=поля["intent"],
                                expected=поля["expected"], actual=поля["actual"],
                                cmd=поля["cmd"], detail=поля["detail"])
    save_maps(ключ, before, after)
    print("\n" + подпись)
    return code


if __name__ == "__main__":
    sys.exit(main())
