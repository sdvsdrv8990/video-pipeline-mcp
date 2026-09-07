"""
tests/quick/test_blast_radius.py — вторая улика сторожа радиуса проверяется без внешнего сервера.

Standalone-прогон:  python tests/quick/test_blast_radius.py
Проверяет: статическая ось ЛОВИТ вызывающего, которого не исполняет ни один сценарий, МОЛЧИТ на
покрытом, и при отсутствии улики (нет графа, нет карты) не обвиняет никого.
"""
import io
import json
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "guards"))
sys.path.insert(0, str(ROOT))

import _symbol_graph  # noqa: E402
import blast_radius as br  # noqa: E402

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


def run(sites, touched=(("core/x.py", "target"),)) -> str:
    """Прогон оси на подставленном ответе графа: живой сервер сюда не зовётся."""
    out = io.StringIO()
    with redirect_stdout(out):
        br.static_blind(set(touched), ask=lambda names: sites)
    return out.getvalue()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vpm_blast_"))
    covered_file = tmp / "covered.json"
    covered_file.write_text(json.dumps({"core/x.py": ["covered_fn"]}), encoding="utf-8")
    br.COVERED = covered_file
    br._functions = lambda rel: {"covered_fn": (1, 10), "silent_fn": (20, 30)}

    print("§1 ловит молчащего вызывающего")
    text = run({"target": ["core/x.py:25"]})
    ok("core/x.py::silent_fn" in text, "вызывающий без сценария назван поимённо")
    ok("не исполняет ни один сценарий" in text, "сказано, ЧЕМ он плох")
    ok("обратного не доказывает" in text, "улика объявлена неполной, а не доказательством")

    print("§2 молчит на покрытом")
    text = run({"target": ["core/x.py:5"]})
    ok("silent_fn" not in text and "⚠" not in text, "покрытый вызывающий обвинения не получает")
    ok("молчащих вызывающих у тронутых функций нет" in text, "молчание сказано вслух, а не пропуском")

    print("§3 улики нет — обвинения нет")
    text = run(None)
    ok("улики нет" in text and "⚠" not in text, "нет графа → пропуск, а не ложное «чисто»")
    br.COVERED = tmp / "нет-такого.json"
    text = run({"target": ["core/x.py:25"]})
    ok("улики нет" in text and "silent_fn" not in text, "нет карты → сверять не с чем, молчим")
    br.COVERED = covered_file

    print("§4 область замера и вызовы вне функций")
    text = run({"target": ["tests/quick/test_x.py:25", "scripts/models.py:25"]})
    ok("⚠" not in text, "тесты и скрипты в обвинение не попадают: они не мерятся покрытием")
    text = run({"target": ["core/x.py:15"]})
    ok("вне функций: 1" in text, "вызов на уровне модуля посчитан отдельно, а не записан в молчащие")

    print("§5 пустой вход")
    ok(run({}, touched=()) == "", "нечего трогать — нечего и печатать")

    print("§7 деревья замера берутся из ОБЪЯВЛЕНИЯ, а не зашиты")
    радиус = br._measured_files("радиус")
    слепая = br._measured_files("слепая-зона")
    корни = lambda сп: {x.split("/")[0] for x in сп}
    ok({"core", "tools", "server.py", "scripts", ".claude"} <= корни(радиус),
       "§7 радиус видит все четыре дерева, включая .claude/hooks — корень СКРЫТЫЙ и вырезался целиком")
    ok("tests" in корни(радиус) and "tests" not in корни(слепая),
       "§7 тесты судятся радиусом, но НЕ слепой зоной: они исполнители, а не исполняемое")
    ok(not [x for x in радиус if "/.journal/" in x or "/.blast/" in x or "__pycache__" in x],
       "§7 журналы прогонов и кэш в замер не попадают")
    ok(len(радиус) > len(слепая) > 100,
       f"§7 замер вырос против зашитых 102: радиус {len(радиус)}, слепая {len(слепая)}")
    import _evidence
    ok(set(_evidence.деревья()) == {"сервер", "сторожа", "хуки", "тесты"},
       "§7 объявление деревьев читается общей дверью `_evidence`, а не своим yaml.safe_load")

    # Список деревьев мало объявить — им обязан ПОЛЬЗОВАТЬСЯ счётчик. Мутация «blind() спрашивает
    # радиус вместо слепой зоны» выжила, пока проверялся только список: 393 функции тестов
    # немедленно всплыли бы в худших файлах, а счёт молчания стал бы шумом.
    вывод = io.StringIO()
    with redirect_stdout(вывод):
        br.blind()
    строки = вывод.getvalue().splitlines()
    ok(строки and "функций вне всех сценариев" in строки[0],
       "§7 слепая зона печатает свой счёт (иначе карты нет — это другой случай)")
    import json as _js
    _карта = _js.loads(br.COVERED.read_text(encoding="utf-8")) if br.COVERED.exists() else {}
    _ждём = {в: sum(len(set(br._functions(r)) - set(_карта.get(r) or []))
                    for r in br._measured_files(в)) for в in ("слепая-зона", "радиус")}
    _счёт = int(re.search(r"сценариев: (\d+)", строки[0])[1]) if строки else -1
    ok(_счёт == _ждём["слепая-зона"] != _ждём["радиус"],
       f"§7 blind() СПРАШИВАЕТ слепую зону: счёт {_счёт} = {_ждём['слепая-зона']}, "
       f"а не радиус {_ждём['радиус']} — список мало объявить, им обязан пользоваться счётчик")

    print("§8 файл знает СВОЁ дерево и каким вопросом судится")
    for путь, ждём_дерево, ждём_слепую in (
        ("core/paths.py", "сервер", True),
        ("server.py", "сервер", True),
        ("scripts/guards/patrol.py", "сторожа", False),
        (".claude/hooks/vpm-fact-gate.py", "хуки", False),
        ("tests/quick/test_hooks.py", "тесты", False),
    ):
        д = br.дерево_файла(путь)
        ok(д is not None and д[0] == ждём_дерево and ("слепая-зона" in д[1]) is ждём_слепую,
           f"§8 {путь} → дерево «{ждём_дерево}», слепая зона {ждём_слепую}")
    ok(br.дерево_файла("README.md") is None and br.дерево_файла("studio/app/tokens.css") is None,
       "§8 вне объявленных деревьев — честное None, а не молчаливое «сервер»")

    print("§6 разбор ответа графа")
    answer = ("Symbol: safe_resolve (function)\nDefined: core/paths.py:50-67  [python]\n\n"
              "Callers (2):\n  ← core/secrets.py:77\n  ← tools/_context.py:55\n\n"
              "Callees (1):\n  → resolve [unresolved]\n")
    ok(_symbol_graph._sites(answer) == ["core/secrets.py:77", "tools/_context.py:55"],
       "точки вызова взяты из живого формата ответа, вызываемые не перепутаны с вызывающими")
    ok(_symbol_graph._sites("No symbol graph found. Run codebase_graph_build first.") == [],
       "ответ без вызывающих не выдумывает точек")

    print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
    for fail in _fails:
        print(f"  ✗ {fail}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
