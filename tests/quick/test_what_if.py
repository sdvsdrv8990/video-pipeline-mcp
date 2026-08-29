"""
tests/quick/test_what_if.py — отчёт «ожидали против получили» проверяется без живого прогона.

Standalone-прогон:  python tests/quick/test_what_if.py
Вердикты подставляются словарями, поэтому набор быстрый: сравнение двух деревьев стоит минуты и
живёт в самом стороже. Проверяется, что заявленное отделено от незаявленного, а ИСЧЕЗНУВШАЯ
проверка не выдаётся за «цвет не сменился».
"""
import io
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "guards"))

import what_if  # noqa: E402

_checks = 0
_fails = []


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def run(expect, before, after) -> tuple[str, int]:
    intent = {"intent": "проба", "where": "core/x.py", "why": "проба", "expect": expect}
    out = io.StringIO()
    with redirect_stdout(out):
        code = what_if.report(intent, before, after)
    return out.getvalue(), code


def main() -> int:
    print("§1 заявленное сбылось")
    text, code = run([{"scenario": "alpha", "becomes": "red"}],
                     {"alpha · 1. шаг": True}, {"alpha · 1. шаг": False})
    ok("✓ alpha → red" in text, "смена цвета зачтена как исполнение намерения")
    ok(code == 0, "чистое намерение даёт exit 0")

    print("§2 изменилось незаявленное")
    text, code = run([{"scenario": "alpha", "becomes": "red"}],
                     {"alpha · 1. шаг": True, "beta · 1. шаг": True},
                     {"alpha · 1. шаг": False, "beta · 1. шаг": False})
    ok("beta" in text and "намерение об этом не говорило" in text, "соседнее падение названо риском")
    ok(code == 1, "скрытый риск не пропускается молча")

    print("§3 проверка ИСЧЕЗЛА — это не «без изменений»")
    text, code = run([{"scenario": "reaction_to_client", "becomes": "red"}],
                     {"reaction_to_client: code доезжает": True}, {"другая проверка": True})
    ok("ИСЧЕЗЛИ" in text and "улики нет" in text, "исчезновение названо отсутствием улики")
    ok("получили без изменений" not in text, "исчезнувшее НЕ выдаётся за «цвет не сменился»")
    ok(code == 1, "исчезнувшая улика не считается успехом")

    print("§4 имя маршрута и имя сценария читаются одинаково")
    ok(what_if._named("reaction_to_client: рубеж core/x.py") == "reaction_to_client",
       "у маршрута имя берётся до рубежа")
    ok(what_if._named("alpha · 1. шаг → успех") == "alpha", "у сценария имя берётся до шага")

    print("§5 три карты объявлены источником вердиктов")
    roster = what_if.suites(ROOT)
    guards = {p.name for p in (ROOT / "scripts" / "guards").glob("*.py") if not p.name.startswith("_")}
    ok(any("routes" in s for s in roster) and any("scenarios" in s for s in roster),
       "сравниваются и сценарии, и маршруты потоков данных")
    ok(sum(s.startswith("tests/quick/") for s in roster) == len(guards) + 1,
       "каждый сторож представлен своим домом, и сверх них ровно один — дом хуков")
    ok("tests/quick/test_hooks.py" in roster,
       "машинерия сессии тоже сравнивается: сервер не импортирует ни сторожей, ни хуков, и без "
       "этой строки правка хука читается как «ничего не изменилось»")
    ok("tests/quick/test_findings_count.py" in roster,
       "набор, грузящий сторожа компиляцией из исходника, взят из ОБЪЯВЛЕНИЯ, а не выведен из дерева")

    print("§6 «не меняется ничего» — тоже заявление")
    out, code = run([], {"a": True, "b": True}, {"a": True, "b": True})
    ok(code == 0 and "не меняет НИЧЕГО" in out, "пустой expect принят как утверждение, и оно сбылось")
    out, code = run([], {"a": True}, {"a": False})
    ok(code == 1 and "⚠" in out, "заявили «ничего», а цвет сменился — это скрытый риск, а не успех")
    bad = Path(tempfile.mkdtemp()) / "i.yaml"
    bad.write_text("intent: п\nwhere: п\nwhy: п\n", encoding="utf-8")
    err = io.StringIO()
    try:
        with redirect_stdout(err):
            what_if.load_intent(bad)
        raised = False
    except SystemExit:
        raised = True
    ok(raised, "ОТСУТСТВИЕ `expect` по-прежнему отказ: промолчать про поведение нельзя")

    print("§7 одна метка на несколько проверок не съедает соседей")
    tree = Path(tempfile.mkdtemp())
    (tree / "tests" / "quick").mkdir(parents=True)
    (tree / "tests" / "CATALOG.md").write_text(
        "| Тест | Зона |\n|---|---|\n| `test_g.py` | сам сторож `scripts/guards/g.py` |\n", encoding="utf-8")
    (tree / "tests" / "quick" / "test_g.py").write_text(
        'print("  \\u2713 одна метка")\nprint("  \\u2717 одна метка")\n', encoding="utf-8")
    got = what_if.verdicts(tree)
    ok(len(got) == 2, "две проверки под ОДНОЙ меткой сравниваются обе, а не схлопываются в последнюю")
    ok(set(got.values()) == {True, False}, "цвет каждой сохранён: порядковый номер различает их")
    shutil.rmtree(tree, ignore_errors=True)

    print("§8 появление проверки — объявляемый исход, а не всегда скрытый риск")
    # Правка, ВЕСЬ смысл которой в новой проверке, цвет не меняет ни у кого. Без этого рода
    # исхода она обречена падать в «скрытый риск», а третья колонка врать «не сбылось».
    text, code = run([{"scenario": "alpha", "becomes": "appeared"}],
                     {"alpha · 1. шаг": True},
                     {"alpha · 1. шаг": True, "alpha · 2. новый шаг": True})
    ok("✓ alpha → appeared" in text, "объявленное появление зачтено как исполнение")
    ok("проверка появилась" not in text, "объявленное появление не числится скрытым риском")
    ok(code == 0, "объявленное появление даёт exit 0")

    text, code = run([], {"alpha · 1. шаг": True},
                     {"alpha · 1. шаг": True, "alpha · 2. новый шаг": True})
    ok("проверка появилась" in text and code == 1,
       "НЕобъявленное появление по-прежнему риск: молчать о новой проверке нельзя")

    print("§9 вставка шага: старая проверка исчезает под новым номером — объявляется отдельно")
    text, code = run([{"scenario": "alpha", "becomes": "appeared"},
                      {"scenario": "alpha", "becomes": "vanished"}],
                     {"alpha · 3. хвост": True},
                     {"alpha · 2. вставка": True, "alpha · 4. хвост": True})
    ok(code == 0 and "✓ alpha → vanished" in text,
       "перенумерация выражается парой исходов у ОДНОГО имени")
    text, _ = run([{"scenario": "alpha", "becomes": "vanished"}],
                  {"alpha · 1. шаг": True}, {"alpha · 1. шаг": True})
    ok("ждали vanished" in text, "заявленное исчезновение, которого не было, — не сбылось")

    print("§10 новый файл доезжает в дерево кандидата: `git diff` его не содержит")
    # Правка, добавляющая модуль, раскатывалась ПОЛОВИНОЙ: ссылки на него есть, файла нет, и
    # сервер в дереве кандидата не поднимался вовсе.
    repo = Path(tempfile.mkdtemp(prefix="vpm-untracked-"))
    def git(*a):
        return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (repo / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("artifact.txt\n", encoding="utf-8")
    git("add", "-A"); git("commit", "-qm", "init")
    (repo / "core").mkdir()
    (repo / "core" / "новый.py").write_text("y = 2\n", encoding="utf-8")
    (repo / "artifact.txt").write_text("прогон\n", encoding="utf-8")

    got = what_if.untracked(repo)
    ok(got == ["core/новый.py"],
       f"новый файл виден, отслеживаемый и игнорируемый — нет (получено {got})")

    tree = Path(tempfile.mkdtemp(prefix="vpm-tree-")) / "tree"
    tree.mkdir(parents=True)
    carried = what_if.carry_untracked(tree, repo)
    ok(carried == ["core/новый.py"] and (tree / "core" / "новый.py").exists(),
       "новый файл перенесён вместе с каталогом — иначе импорт в дереве кандидата не найдёт модуль")
    ok(not (tree / "artifact.txt").exists(),
       "игнорируемое не переносится: артефакт прогона правкой не является")
    shutil.rmtree(tree.parent, ignore_errors=True)

    # СВЯЗЬ, а не часть: сегодня сломалось именно то, что дерево кандидата собиралось БЕЗ переноса.
    built = what_if.candidate_tree("", repo)
    try:
        ok((built / "core" / "новый.py").exists(),
           "дерево кандидата собрано ВМЕСТЕ с новым файлом — иначе правка раскатывается половиной")
        ok(not (built / "artifact.txt").exists(),
           "и без игнорируемого: дерево кандидата — правка, а не рабочий стол")
    finally:
        what_if.drop_tree(built, repo)

    # База — ЧИСТЫЙ HEAD: новый файл принадлежит правке, а занесённый в базу он делает её химерой
    # и красит там сторожа (у нового модуля на HEAD читателей нет).
    base = what_if.candidate_tree("", repo, carry=False)
    try:
        ok(not (base / "core" / "новый.py").exists(),
           "в базовую линию файлы правки не попадают — иначе сравниваем правку саму с собой")
    finally:
        what_if.drop_tree(base, repo)
    shutil.rmtree(repo, ignore_errors=True)

    print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
    for fail in _fails:
        print(f"  - {fail}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
