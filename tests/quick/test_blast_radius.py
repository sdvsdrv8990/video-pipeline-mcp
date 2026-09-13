"""
tests/quick/test_blast_radius.py — вторая улика сторожа радиуса проверяется без внешнего сервера.

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
import _stamp  # noqa: E402  мера потолка и подпись сдвига — общие у всех сторожей
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
        br.static_blind(set(touched), ask=lambda names, где=None: sites)
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

    print("§10 имя, которого граф НЕ ЗНАЕТ, не выдаётся за «вызывающих нет»")
    # Источник отвечает `No symbol named "X" found.` — строки нет в ABSENT, поэтому она
    # превращалась в пустой список, а читатель печатал успокоение на нулевой улике.
    text = run({})
    ok("улики нет" in text, "§10 молчание графа названо молчанием")
    ok("молчащих вызывающих у тронутых функций нет" not in text,
       "§10 успокоительный вердикт на нулевой улике не печатается")
    text = run({"target": []})
    ok("молчащих вызывающих" in text and "улики нет" not in text,
       "§10 граф ЗНАЕТ символ и вызывающих не нашёл — это улика, а не молчание")

    print("§12 чужая формулировка отказа тоже значит «улики нет» — перечислять беды нельзя")
    чужая = 'Error: unknown symbol "target" in project'
    ответ = _symbol_graph.callers(["target"], ask=lambda имя: чужая)
    ok(ответ is not None and "target" not in ответ,
       "§12 ответ, не объявивший символа, уликой не считается при ЛЮБОЙ формулировке")

    print("§13 однофамилец из другого файла — улика ЛОЖНАЯ, и она не берётся")
    чужой_дом = ("Symbol: Engine.register (method)\nDefined: core/engine/engine.py:114–133  [python]\n\n"
                 "Callers (2):\n  ← server.py:166\n  ← server.py:167\n")
    ответ = _symbol_graph.callers(["register"], ask=lambda имя: чужой_дом,
                                  где={"register": "core/firewall/firewall.py"})
    ok(ответ is not None and "register" not in ответ,
       "§13 объявлен символ из ДРУГОГО файла — улики по тронутому нет")
    ответ = _symbol_graph.callers(["register"], ask=lambda имя: чужой_дом,
                                  где={"register": "core/engine/engine.py"})
    ok(ответ is not None and ответ.get("register") == ["server.py:166", "server.py:167"],
       "§13 объявлен ТОТ ЖЕ файл — улика берётся")
    ok(_symbol_graph.объявление(чужой_дом) == ("Engine.register", "core/engine/engine.py"),
       "§13 объявление ответа разбирается: символ и его файл")
    ok(_symbol_graph.объявление(чужая) is None, "§13 ответ без объявления даёт None, а не пустую пару")

    print("§14 пустой вход и повтор: «не спрашивали» отличимо от «спросили и не знаем»")
    спрошено: list[str] = []
    def считающий(имя: str) -> str:
        спрошено.append(имя)
        return чужой_дом
    прежний = _symbol_graph._Server
    def запрет(*_а, **_к):
        raise OSError("источник поднят на ПУСТОМ входе")
    _symbol_graph._Server = запрет
    try:
        ok(_symbol_graph.callers([]) == {},
           "§14 пустой вход не поднимает источник и отвечает пустым словарём, а не None")
    finally:
        _symbol_graph._Server = прежний
    _symbol_graph.callers(["register", "register"], ask=считающий, где={"register": "core/engine/engine.py"})
    ok(спрошено == ["register"], "§14 повтор имени спрашивается ОДИН раз: подъём источника стоит секунды")

    print("§11 контракт ответа: неизвестное имя отсутствует, известное — присутствует")
    известный = ("Symbol: safe_resolve (function)\nDefined: core/paths.py:50–67  [python]\n\n"
                 "Callers (1):\n  ← core/secrets.py:77\n")
    образцы = {"safe_resolve": известный, "нет_такого": 'No symbol named "нет_такого" found.'}
    ответ = _symbol_graph.callers(["safe_resolve", "нет_такого"], ask=lambda имя: образцы[имя])
    ok(ответ is not None and ответ.get("safe_resolve") == ["core/secrets.py:77"],
       "§11 известный символ несёт свои точки вызова")
    ok(ответ is not None and "нет_такого" not in ответ,
       "§11 неизвестное имя ОТСУТСТВУЕТ в ответе: улики нет — не пустой список")

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
    print(f"    замер: радиус {len(радиус)}, слепая {len(слепая)}")
    ok(len(радиус) > len(слепая) > 100,
       "§7 замер вырос против зашитых ста двух: радиус шире слепой зоны, обе за сотню")
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
    print(f"    счёт {_счёт} · слепая {_ждём['слепая-зона']} · радиус {_ждём['радиус']}")
    ok(_счёт == _ждём["слепая-зона"] != _ждём["радиус"],
       "§7 blind() СПРАШИВАЕТ слепую зону, а не радиус — список мало объявить, "
       "им обязан пользоваться счётчик")

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

    ok(_stamp.мера_потолка("92\n") == "92", "потолок числом меряется числом")
    ok(_stamp.мера_потолка("a::b\nc::d\n") == "2", "потолок именами меряется СЧЁТОМ имён")
    ok(br._потолок_именами.__doc__ is not None, "формат потолка объявлен читателю")

    настоящий = br.BLIND_BASELINE
    # Потолок строится из ТЕКУЩЕГО молчания: выше набор подменил карту и `_functions` стендом,
    # и список из боевого файла говорил бы о другом мире, чем тот, который здесь судится.
    имена = sorted(f"{rel}::{fn}" for rel, fns in br._молчащие().items() for fn in fns)
    времянка = Path(tempfile.mkdtemp(prefix="vpm-слепая-")) / "baseline.txt"
    try:
        # Прежний формат — одно число: он не отличает прирост от давнего долга и обязан быть красным
        времянка.write_text("92\n", encoding="utf-8")
        br.BLIND_BASELINE = времянка
        буфер = io.StringIO()
        with redirect_stdout(буфер):
            код = br.blind()
        ok(код == 1 and "не в именах" in буфер.getvalue(),
           "потолок ЧИСЛОМ отвергается вслух: счёт не переживает пересборку карты")

        времянка.write_text("\n".join(имена[:-2]) + "\n", encoding="utf-8")
        буфер = io.StringIO()
        with redirect_stdout(буфер):
            код = br.blind()
        вывод = буфер.getvalue()
        ok(код == 1, "молчащая функция вне потолка — красный")
        ok(all(имя in вывод for имя in имена[-2:]),
           "прирост назван ПОИМЁННО, а не спрятан под срезом восьми худших")
        ok("ДОЛГ, а не прирост" in вывод or "ПРИРОСТ" in вывод,
           "отчёт различает долг и прирост словами, а не оставляет читателя гадать")
    finally:
        br.BLIND_BASELINE = настоящий

    print("§9 горячая точка: показ приоритета, а не отказ")
    ряд = br.горячие()
    ok(ряд and all(len(с) == 3 for с in ряд), "горячие точки называются файлом, правками и размером")
    правки = [п for _, п, _ in ряд]
    ok(правки == sorted(правки, reverse=True), "список идёт по убыванию правок: первым дорогое")
    размеры = [р for _, _, р in ряд]
    все = {rel: len([s for s in (br.ROOT / rel).read_text(encoding="utf-8").splitlines() if s.strip()])
           for rel in br._measured_files("радиус") if (br.ROOT / rel).exists()}
    медиана = sorted(все.values())[len(все) // 2]
    ok(all(р > медиана for р in размеры), "мельче медианы в горячие не попадает: это порог, а не список")
    print(f"    горячих {len(ряд)} · медиана {медиана} строк")

    буфер = io.StringIO()
    with redirect_stdout(буфер):
        код = br.показ_горячих()
    ok(код == 0, "показ НЕ отказывает: метка замера частично про «где смотрели», обвинять ею нельзя")
    ok("не отказ" in буфер.getvalue(), "показ говорит вслух, что он не отказ")

    настоящее = br._evidence.горячие
    try:
        br._evidence.горячие = lambda *а, **к: {}
        ok(br.горячие() == [], "нет объявления — список пуст, а не выдуманные пороги")
    finally:
        br._evidence.горячие = настоящее

    print("§покрытие РОДАМИ: чего в зоне не пробовал никто")
    зоны = Path(tempfile.mkdtemp(prefix="vpm_роды_")) / "tests" / "scenarios"
    зоны.mkdir(parents=True)
    (зоны / "удача.yaml").write_text(
        "- scenario: s1\n  why: работает\n  when:\n    - call: t\n      expect: {ok: true}\n",
        encoding="utf-8")
    (зоны / "снос.yaml").write_text(
        "- scenario: s2\n  why: сносим\n  род: разрушительный\n  when:\n"
        "    - call: t\n      expect: {ok: false}\n", encoding="utf-8")
    буфер = io.StringIO()
    with redirect_stdout(буфер):
        код = br.показ_родов(зоны.parents[1])
    показ = буфер.getvalue()
    ok(код == 0 and "удача.yaml" in показ.splitlines()[-1],
       "зона без разрушительного названа ПОИМЁННО в итоге, а не только числом")
    ok("разрушительных 1" in показ,
       "счёт по родам печатается: покрытие зоны стало числом, а не впечатлением")
    пусто = Path(tempfile.mkdtemp(prefix="vpm_роды_пусто_"))
    (пусто / "tests" / "scenarios").mkdir(parents=True)
    буфер = io.StringIO()
    with redirect_stdout(буфер):
        код = br.показ_родов(пусто)
    ok(код == 1 and "не по чему" in буфер.getvalue(),
       "объявлений нет — читатель говорит это отказом, а не печатает нулевое покрытие")

    print("§карта КЛАССА: кто уже стоит на этом источнике")
    карта, вне = br.карта_классов(ROOT)
    ok(any(и.endswith("CATALOG.md") for и in карта),
       "источник выведен РАЗБОРОМ: каталог зон попал в карту, хотя реестра «механизм → источник» нет")
    каталог = карта.get("scripts/guards/CATALOG.md", set())
    ok(any(м.startswith("ось «") for м in каталог),
       "ось названа ПОИМЁННО, а не файлом: расширять зовут конкретного судью, а не модуль")
    ok(len(каталог) > 1,
       "на людном источнике видно ВСЕХ: иначе кандидат не находится и заводится сосед")
    буфер = io.StringIO()
    with redirect_stdout(буфер):
        код = br.показ_класса("scripts/guards/CATALOG.md", ROOT)
    ok(код == 0 and "кандидат уже есть" in буфер.getvalue(),
       "занятый источник отвечает советом расширять — это и есть проверка ДО заведения судьи")
    буфер = io.StringIO()
    with redirect_stdout(буфер):
        br.показ_класса("config/невиданное-никем.yaml", ROOT)
    ok("не стоит НИКТО" in буфер.getvalue(),
       "пустой источник сказан словами: молчание тут читалось бы как «кандидат есть»")
    буфер = io.StringIO()
    with redirect_stdout(буфер):
        br.показ_класса("", ROOT)
    свод = буфер.getvalue()
    ok(вне and all(f"⚠ {имя}" in свод for имя in вне[:3]),
       "механизмы вне карты названы В ПОКАЗЕ: их радиус неизвестен, и это долг, а не чистота")
    ok("механизмов вне карты" in свод, "их счёт напечатан — долг считается, а не упоминается")

    сломанное = Path(tempfile.mkdtemp(prefix="vpm_класс_"))
    (сломанное / "scripts" / "guards").mkdir(parents=True)
    (сломанное / "scripts" / "guards" / "сторож.py").write_text("def сломан(:\n", encoding="utf-8")
    буфер = io.StringIO()
    with redirect_stdout(буфер):
        код = br.показ_класса("", сломанное)
    ok(код == 1 and "разбирать нечего" in буфер.getvalue(),
       "источники вырезаны, разбор не поднялся — отказ, а не пустая карта, выданная за покрытие")

    print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
    for fail in _fails:
        print(f"  ✗ {fail}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
