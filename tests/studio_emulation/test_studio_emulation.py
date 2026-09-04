"""
tests/studio_emulation/test_studio_emulation.py — приёмка правки ИИ по React на эмуляции студии.

Standalone-прогон:  python tests/studio_emulation/test_studio_emulation.py
Проверяет ОБЕ стороны каждого условия приёмки (`scripts/guards/acceptance_studio.py`): проверка ловит
своё нарушение на подставленной правке и молчит на чистом дереве. Предмет — эмуляция
`app/`: студии на диске ещё нет, а правило без исполнителя не работает вовсе.
"""
import io
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
GUARDS = ROOT / "scripts" / "guards"
APP = Path(__file__).resolve().parent / "app"
sys.path.insert(0, str(GUARDS))
sys.dont_write_bytecode = True


def _load(path: Path, name: str):
    """Компиляция ИЗ ИСХОДНИКА: кэш байткода признаёт свежим .pyc при том же размере и секунде."""
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


surface = _load(GUARDS / "_studio_surface.py", "_studio_surface")
sys.modules["_studio_surface"] = surface
advisor = _load(GUARDS / "acceptance_studio.py", "acceptance_studio")

_checks = 0
_fails = []


def ok(cond, msg, detail=None):
    """Метка проверки СТАБИЛЬНА: улика печатается отдельной строкой, иначе цикл сравнивает текст,
    который меняется от прогона к прогону, и всякая правка выглядит появлением новой проверки."""
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
    print(f"  {'✓' if cond else '✗'} {msg}")
    if detail:
        print(f"      · {detail}")


def scene(*edits: tuple[str, str, str], snapshot: bool = True) -> Path:
    """Копия эмуляции с подставленной правкой: (файл, было, стало). Живое дерево не трогаем."""
    tmp = Path(tempfile.mkdtemp(prefix="vpm_studio_"))
    tree = tmp / "app"
    shutil.copytree(APP, tree)
    for name, was, now in edits:
        target = tree / name
        text = target.read_text(encoding="utf-8")
        if was and was not in text:
            raise AssertionError(f"якорь правки не найден в {name}: {was!r}")
        target.write_text(text.replace(was, now) if was else text + now, encoding="utf-8")
    if snapshot:
        # Снимок берётся с ЧИСТОГО дерева: правка обязана расходиться с принятой формой — ровно
        # как у ИИ, который тронул стиль после того, как форму приняли.
        (tmp / advisor.SNAPSHOT).write_text(surface.surface_json(APP), encoding="utf-8")
    return tree


def notes(check, tree: Path, **kw) -> list[str]:
    return check(surface.read(tree), tree=tree, **kw)


def запуск(argv: list[str]) -> tuple[int, str]:
    """Прогон сторожа его же командной строкой: путь от аргумента до вывода тоже часть контракта."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        код = advisor.main(argv)
    return код, out.getvalue() + err.getvalue()


def main() -> int:
    print("\n=== П1: ни один компонент не сломан ===")
    ok(not notes(advisor.broken, scene()), "чистая эмуляция: ни одного сломанного компонента")
    сломан = notes(advisor.broken, scene(("screens/NicheScreen.tsx",
                                          'import { Card } from "../primitives/Card";\n', "")))
    ok(len(сломан) == 1 and "<Card>" in сломан[0],
       "снесённый импорт примитива назван поимённо", сломан)
    переименован = notes(advisor.broken, scene(("primitives/Button.tsx", "export function Button",
                                                "export function ActionButton")))
    ok(any("Button" in note and "не экспортирует" in note for note in переименован),
       "переименованный экспорт ловится у ИМПОРТЁРА, хотя строка импорта цела", переименован)

    print("\n=== П2: стили, шрифты и отступы не сменены ===")
    ok(not notes(advisor.styles, scene()), "чистая эмуляция: стиль весь из токенов, форма = снимку")
    мимо = notes(advisor.styles, scene(("primitives/Card.tsx", "fontSize: tokens.font.body",
                                        'fontSize: "13px"')))
    ok(any('"13px"' in note and "мимо токена" in note for note in мимо),
       "размер шрифта мимо токена назван файлом и строкой", мимо)
    мёртвый = notes(advisor.styles, scene(("tokens.ts", 'lg: "24px"', 'lg: "24px", xl: "32px"')))
    ok(any("space.xl" in note and "читателя нет" in note for note in мёртвый),
       "объявленный токен без читателя — мёртвая половина декларации")
    форма = notes(advisor.styles, scene(("primitives/Card.tsx", "variant, title, children",
                                         "variant, title, children, density")))
    ok(any("Card: props разошлось со снимком" in note for note in форма),
       "смена параметров компонента расходится со снимком формы")
    цвет = notes(advisor.styles, scene(("tokens.ts", 'ink: "#1a1a1a"', 'ink: "#333333"')))
    ok(any("color.ink" in note and "#1a1a1a" in note and "#333333" in note for note in цвет),
       "подменённое ЗНАЧЕНИЕ токена видно, хотя читатели те же")

    print("\n=== П8: стилизация и анимация не теряются, а возвращаются ДОСЛОВНО ===")
    ok(not notes(advisor.motion, scene()), "чистая эмуляция: ни одно объявление стиля не потеряно")
    стёрта = scene(("primitives/Card.tsx", '        animationName: "appear",\n', ""))
    пропажа = notes(advisor.motion, стёрта)
    ok(any('ИСЧЕЗЛО объявление animationName' in note and '"appear"' in note for note in пропажа),
       "стёртая анимация названа дословно — тем, чем она была")
    подмена = notes(advisor.motion, scene(("primitives/Card.tsx",
                                           "animationDuration: tokens.duration.enter",
                                           'animationDuration: "150ms"')))
    ok(any("подменено animationDuration" in note and "160ms" in note and "150ms" in note
           for note in подмена),
       "похожая длительность не считается возвратом: названы и прежняя, и новая")
    кадры = notes(advisor.motion, scene(("animations.ts", "opacity: 0;", "opacity: 0.5;")))
    ok(any("кадры анимации `appear` подменены" in note and "opacity: 0;" in note for note in кадры),
       "подменённые кадры печатаются целиком — вернуть можно, не читая историю git")
    висит = notes(advisor.motion, scene(("app" if False else "primitives/Card.tsx",
                                         'animationName: "appear"', 'animationName: "slide"')))
    ok(any("зовёт кадры `slide`" in note for note in висит),
       "анимация зовёт несуществующие кадры — она не проиграется, и это видно до запуска")
    вся_анимация = scene(("primitives/Card.tsx", '        animationName: "appear",\n'
                          '        animationDuration: tokens.duration.enter,\n', ""))
    код, вывод = запуск(["--восстановить", "Card", "--дерево", str(вся_анимация)])
    ok(код == 0 and 'animationName: "appear",' in вывод,
       "`--восстановить` печатает исчезнувшее строкой, годной к вставке")
    ok("animationDuration: tokens.duration.enter, (= 160ms)" in вывод,
       "рядом со строкой стоит ЗНАЧЕНИЕ токена: возврат не требует ни бэкапа, ни истории")
    код, вывод = запуск(["--восстановить", "Card", "--дерево", str(scene())])
    ok(код == 0 and "восстанавливать нечего" in вывод,
       "на чистом дереве восстановление честно говорит, что терять было нечего")
    код, вывод = запуск(["--восстановить", "НетТакого"])
    ok(код == 2, "восстановление несуществующего компонента — отказ, а не пустой ответ")
    без_снимка = notes(advisor.styles, scene(snapshot=False))
    ok(any("снимка формы нет" in note for note in без_снимка),
       "снимок снесён — судья говорит это вслух, а не засчитывает чистым")

    print("\n=== П3: не вышел за рамки задачи и не оставил мёртвого кода ===")
    ok(not notes(advisor.dead, scene()), "чистая эмуляция: ни висячего компонента, ни мёртвого пропа")
    висячий = notes(advisor.dead, scene(("primitives/Card.tsx", "", '''
export function Ghost({ title }: { title: string }) {
  return <div data-component="Ghost">{title}</div>;
}
''')))
    ok(any("Ghost" in note and "не отрисован" in note for note in висячий),
       "компонент, которого никто не рисует, назван мёртвым", висячий)
    проп = notes(advisor.dead, scene(("primitives/Button.tsx", "variant, label, onPress",
                                      "variant, label, onPress, tooltip")))
    ok(any("Button.tooltip" in note for note in проп), "проп без читателя — мёртвая половина")

    зона = Path(tempfile.mkdtemp(prefix="vpm_zone_"))
    subprocess.run(["git", "init", "-q"], cwd=зона, check=True)
    (зона / "app").mkdir()
    (зона / "app" / "Card.tsx").write_text("in", encoding="utf-8")
    (зона / "server.py").write_text("out", encoding="utf-8")
    вне = advisor.scope({}, zones=("app/*",), root=зона)
    ok(len(вне) == 1 and "server.py" in вне[0], "тронутое вне зоны задачи названо", вне)
    ok(not advisor.scope({}, zones=("app/*", "server.py"), root=зона),
       "объявленная задачей зона шире — обвинять не за что")
    ok(not advisor.scope({}, zones=(), root=зона),
       "зона не объявлена — судья молчит, а не выдумывает границу")

    print("\n=== П4: понятно, с каким компонентом работать ===")
    ok(not notes(advisor.addressable, scene()), "чистая эмуляция: каждый компонент адресуем маркером")
    безымянный = notes(advisor.addressable, scene(("primitives/Button.tsx",
                                                   'data-component="Button"\n      ', "")))
    ok(any("Button" in note and "скриншоту" in note for note in безымянный),
       "компонент без маркера: по скриншоту его не найти", безымянный)
    двойной = notes(advisor.addressable, scene(("primitives/Button.tsx",
                                                'data-component="Button"',
                                                'data-component="Card"')))
    ok(any("не определить" in note for note in двойной),
       "один маркер на два файла — адресация перестала быть однозначной")

    got = surface.read(APP)
    ok(advisor.who(got, "Card") == 0, "`--кто Card` отвечает файлом и строкой")
    ok(advisor.who(got, "Cardd") == 1, "неизвестный маркер — отказ, а не тихий ответ наугад")

    print("\n=== эксперт: сценарии выбираются по роду улики ===")
    config = advisor.scenarios()
    ok(advisor.rods_of(["app/tokens.ts"], config) == ["правка-токенов"],
       "род улики выведен из тронутого файла, а не спрошен у правщика")
    ok(advisor.rods_of(["app/primitives/Card.tsx"], config) == ["правка-компонента"],
       "компонент и объявление стиля — разные роды улики, и совет к ним разный")
    код, вывод = запуск(["--совет", "--файл", "app/tokens.ts"])
    ok(код == 0 and "размер-или-шрифт-мимо-токена" in вывод,
       "совет по правке токенов называет сценарий, который здесь ломается")
    ok("ловит:" in вывод and "журнал:" in вывод,
       "у совета есть исполнитель и запись, а не одно мнение")
    код, вывод = запуск(["--совет", "--улика", "выдуманный-род"])
    ok(код == 2, "род улики вне объявления — отказ, а не молчаливый пустой совет")
    код, вывод = запуск(["--совет"])
    ok(код == 0 and вывод.count("▸") == len(config["сценарии"]),
       "без улики показаны ВСЕ сценарии — это штурм идей, а не отказ")

    print("\n=== эксперт: задания стенда ===")
    задания = advisor.tasks(APP)["задания"]
    ok(len(задания) >= 6 and all(z["зона"] and z["приёмка"] for z in задания),
       "у каждого задания стенда объявлена зона и приёмка")
    код, вывод = запуск(["--задания"])
    ok(код == 0 and "по-скриншоту" in вывод, "спектр заданий печатается целиком")
    код, вывод = запуск(["--суд", "--задача", "выдуманное"])
    ok(код == 2, "суд по несуществующему заданию — отказ, а не суд без границ")
    код, вывод = запуск(["--суд", "--задача", "плотность-карточки"])
    # Не по печатной строке, а по СЛЕДСТВИЮ: критерий границ обязан быть СУЖДЁН. Проверка на
    # объявление зоны мутацию «зона не подставилась» пережила — печать шла раньше подстановки.
    ok("зона суда взята из объявления" in вывод and "критерий не судится" not in вывод
       and "П3а вышел за рамки задачи:" in вывод,
       "граница суда приходит из задания: критерий границ реально судится, а не объявлен")

    print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
    for fail in _fails:
        print(f"  ✗ {fail}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
