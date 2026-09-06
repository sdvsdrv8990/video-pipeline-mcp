#!/usr/bin/env python3
"""scripts/guards/patrol.py — НАРЯД: поднять набор сторожей по улике и свести их вердикты.

Ручной режим системы сторожей рядом с авто: событие будит хук само, а наряд поднимают, когда
события не будет, — правка скриптом мимо `Edit`, чужое дерево, проверка перед сдачей. Кого
поднимать, решает объявление `evidence.yaml`, а не этот файл; свой вердикт наряд не выносит —
сводит чужие.

    patrol.py                        # улика выведена из тронутого в дереве
    patrol.py --улика редактирование # главная улика; --файл X выведет род сам
    patrol.py --кто --улика создание # сухой ход: кто отзовётся, без запуска
    patrol.py --улики                # словарь: три главные и привязанные к ним роды
    patrol.py --факт --улика создание # кто ОБЯЗАН был сработать по улике — и сработал ли
    patrol.py --задача правка-сервера # что рождает этот тип работы и кого поднять сверх авто
    patrol.py --новый [--таски]      # НОВЫЙ файл под набором судей; --таски пишет несделанное остатком
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _evidence                                                           # noqa: E402
import _stamp                                                              # noqa: E402
import _verdict                                                            # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "bin" / "python"


def словарь() -> int:
    понятия = _evidence.понятия()
    if понятия:
        # Заголовок считает, а не пересказывает: он уже врал «три значения» на пяти понятиях,
        # потому что число было вписано словом. Группировка по слову до дефиса — само слово и
        # есть многозначное, роды после дефиса.
        группы: dict[str, list[str]] = {}
        for имя in понятия:
            группы.setdefault(имя.split("-")[0], []).append(имя)
        спорные = [с for с, роды in группы.items() if len(роды) > 1]
        print(f"▸ ходовых слов с несколькими машинными значениями: {len(спорные)} "
              f"({', '.join(f'«{с}» — {len(группы[с])}' for с in спорные) or 'нет'})")
        for имя, тело in понятия.items():
            print(f"   {имя}: {тело.get('про')}\n      где: {тело.get('где')}"
                  f"\n      проверить: {тело.get('чем проверить')}")
    for имя, главная in _evidence.главные().items():
        роды = [р for р, тело in _evidence.роды().items() if тело.get("главная") == имя]
        отзыв = [с for с, _ in _evidence.наряд([имя])]
        print(f"\n▸ {имя} — {главная.get('про')}")
        print(f"  видно:     {главная.get('видно')}")
        print(f"  роды:      {', '.join(роды) or '—'}")
        print(f"  поднимает: {', '.join(отзыв) or 'никого — улика объявлена, а сторожей на неё нет'}")
    return 0


def задача(имя: str) -> int:
    """Разбор типа работы: какая улика рождается, кто проснётся сам, кого поднять рукой.

    Отвечает на вопрос ВЫБОРА — до работы, а не после: пропуск срабатывания чаще всего не в том,
    что сторож сломан, а в том, что его не позвали, потому что он не пришёл на ум.
    """
    объявленные = _evidence.задачи()
    if имя not in объявленные:
        print(f"patrol: тип работы `{имя}` не объявлен; известны {sorted(объявленные)}",
              file=sys.stderr)
        return 2
    дело = объявленные[имя]
    улики = list(дело.get("улики") or [])
    print(f"── тип работы: {имя} — {(дело.get('про') or '').strip()}")
    print(f"   улика:     {', '.join(улики) or '—'}")
    print(f"   скил:      {дело.get('скил') or '—'}")
    print(f"   проснутся: {', '.join(_evidence.ожидаются(улики)) or 'никто — события у этой улики нет'}")
    набор = dict(_evidence.наряд(улики))
    набор.update({и: list(к or []) for и, к in (дело.get("сверх") or {}).items()})
    for сторож, команда in sorted(набор.items()):
        сверх = " (сверх авто)" if сторож in (дело.get("сверх") or {}) else ""
        print(f"   рукой:     {сторож} {' '.join(команда)}{сверх}")
    print(f"   факт:      .venv/bin/python scripts/guards/patrol.py --факт --улика {улики[0] if улики else '—'}")
    return 0


def факт(улики: list[str], минут: float) -> int:
    """Обязанные сработать хуки против СЛЕДА: молчание объявленного — пропуск срабатывания.

    Правило срабатывания живёт в одном месте — в самом следе (`.claude/hooks/_trace.py`); здесь
    только сверка ожидаемого с фактическим. Окно нужно, потому что вчерашняя отметка про
    сегодняшнюю работу не говорит ничего.
    """
    sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
    import _trace                                                          # noqa: PLC0415

    ожидаемые = _evidence.ожидаются(улики)
    if not ожидаемые:
        print(f"   на улику {', '.join(улики)} не встаёт ни один хук — события у неё нет "
              f"(у «чтения» это остаток 75e4, а не забытая строка)")
        return 0
    сейчас, молчали = time.time(), []
    for имя in ожидаемые:
        когда = _trace.seen(имя)
        свежо = когда and (сейчас - когда) / 60 <= минут
        назад = "ни разу" if not когда else f"{(сейчас - когда) / 60:.0f} мин назад"
        print(f"   {'✓' if свежо else '✗'} {имя} — {назад}")
        молчали += [] if свежо else [имя]
    if молчали:
        print(f"   промолчали за {минут:.0f} мин: {', '.join(молчали)}. Либо событие до них не "
              f"дошло (правка мимо инструмента, чужой matcher), либо сторож сломан молча.")
    return len(молчали)


def улики_наряда(args: argparse.Namespace) -> list[str]:
    """Улику называет правщик ЛИБО она выводится из тронутого — второе не требует верить на слово."""
    названы = list(args.улика) + _evidence.rods_of(args.файл)
    return названы or _evidence.rods_of(_verdict.touched(ROOT))


def поднять(имя: str, команда: list[str], полностью: bool = False) -> tuple[str, int, str]:
    """Сторож поднимается СВОИМ путём и своим питоном: вердикт принадлежит ему, а не оболочке."""
    исполнитель = str(PYTHON) if PYTHON.exists() else sys.executable
    done = subprocess.run([исполнитель, str(Path(__file__).with_name(имя)), *команда],
                          cwd=str(ROOT), capture_output=True, text=True)
    вывод = done.stdout or done.stderr or ""
    хвост = [с for с in вывод.splitlines() if с.strip()]
    return имя, done.returncode, (вывод if полностью else (хвост[-1] if хвост else "(молча)"))


def неотслеживаемые() -> list[str]:
    """Файлы, которых git ещё не знает. Игнорируемое не берём — это артефакты, а не правка."""
    done = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                          cwd=str(ROOT), capture_output=True, text=True)
    return sorted(и for и in done.stdout.split("\0")
                  if и.endswith((".py", ".yaml", ".md", ".sh", ".txt", ".toml")))


def свести(файлы: list[str], выводы: dict[str, str]) -> dict[str, list[str]]:
    """Слова судей, названные ПО ИМЕНИ файла. Чужие строки не берём: сведение — не пересказ."""
    слова: dict[str, list[str]] = {ф: [] for ф in файлы}
    for имя, вывод in sorted(выводы.items()):
        for строка in вывод.splitlines():
            for ф in файлы:
                if ф in строка and строка.strip():
                    слова[ф].append(f"{имя}: {строка.strip()[:150]}")
    return слова



TIERS = ("tests", "tiers.yaml")
CATALOG = ("tests", "CATALOG.md")


def сам_себе_дом(файл: str) -> bool:
    """Дом не требуется с того, кто им и является: набор — сам себе дом, каталог — их опись.

    Без этого правка любого набора обвиняла бы сама себя, и обвинение стало бы фоном.
    """
    имя = Path(файл).name
    return имя.startswith("test_") or файл.endswith(("CATALOG.md", "tiers.yaml", "conftest.py"))


def дома(файлы: list[str]) -> dict[str, list[str]]:
    """Набор-дом каждого тронутого файла: его ЗОНА из каталога называет этот файл.

    Связь выводится, а не держится вторым списком: каталог и так обязан называть зону, а список
    рядом разошёлся бы с ним в первый же переезд.
    """
    найдено: dict[str, list[str]] = {ф: [] for ф in файлы}
    for строка in (ROOT.joinpath(*CATALOG)).read_text(encoding="utf-8").splitlines():
        ячейки = строка.split("|")
        if not строка.startswith("|") or len(ячейки) < 3:
            continue
        набор = ячейки[1].strip().strip("*").strip("`").strip()
        if not набор.startswith("test_"):
            continue
        for ф in файлы:
            if Path(ф).name in ячейки[2] or ф in ячейки[2]:
                найдено[ф].append(набор)
    return найдено


def выбор(файлы: list[str], объявление: dict) -> tuple[set[str], list[tuple[str, dict]], list[dict]]:
    """Чистый выбор: задетые деревья, нужные ярусы и условия вне графика.

    Отделено от печати, потому что судится именно решение: ярус, выбранный по одному дереву из
    пяти, выглядит как «минимальный» ровно так же, как выбранный по всем.
    """
    задеты = {имя for имя, пути in (объявление["деревья"]).items()
              for ф in файлы if any(ф == п or ф.startswith(п) for п in пути)}
    нужны = [(имя, тело) for имя, тело in объявление["ярусы"].items()
             if задеты & set(тело.get("поднимает") or [])]
    вне = [у for у in объявление.get("вне_графика") or []
           if any(ф.startswith(у["место"]) for ф in файлы)]
    return задеты, нужны, вне


def ярус() -> int:
    """Какой ярус прогонов нужен ЭТОЙ правке — по радиусу, а не по типу работы «на глаз»."""
    объявление = yaml.safe_load(ROOT.joinpath(*TIERS).read_text(encoding="utf-8"))
    файлы = _verdict.touched(ROOT)
    if not файлы:
        print("правки нет — улики нет, а не «ярус нулевой»")
        return 0
    задеты, нужны, вне = выбор(файлы, объявление)
    print(f"── тронуто файлов: {len(файлы)} · деревьев: {', '.join(sorted(задеты)) or 'вне объявленных'}")
    их_дома = дома(файлы)
    без_дома = [ф for ф, н in их_дома.items() if not н and not сам_себе_дом(ф)]
    for ф, наборы in sorted(их_дома.items()):
        if наборы:
            print(f"   {ф} → {', '.join(sorted(set(наборы)))}")
    if без_дома:
        print(f"   ✗ без набора-дома: {len(без_дома)} — {', '.join(без_дома[:3])}"
              f"{' …' if len(без_дома) > 3 else ''}")
    print(f"\n── ярусы, которые поднимает эта правка: {len(нужны)}")
    for имя, тело in нужны:
        print(f"   ▸ {имя} — {тело['про']}")
        print(f"     цена: {тело['цена']}")
        print(f"     чем:  {тело['чем']}")
    if вне:
        print("\n── вне графика (правка этих мест поднимает ярус независимо от радиуса):")
        for у in вне:
            print(f"   ▸ {у['место']} → ярус `{у['ярус']}`: {у['почему']}")
    return 0


def новый(цели: list[str], таски: bool) -> int:
    """Новый файл судится НАБОРОМ судей: что каждый сказал ПРО НЕГО, и что осталось несделанным.

    Правила не дублируются — судьи поднимаются свои, а наряд лишь сводит их слова по имени файла.
    Несделанное с `--таски` уходит подписью ОСТАТОК: дальше его читают сторож намерений (запрещает
    запись, пока не признано) и сторож дисциплины — двойная проверка от лени.
    """
    try:                                       # след: без отметки механизм выглядит пылящимся
        sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
        import _trace
        _trace.mark("patrol.py")
    except Exception:                          # noqa: BLE001 — след не важнее самой работы
        pass
    файлы = цели or неотслеживаемые()
    if not файлы:
        print("новых файлов нет — улики нет, а не «всё в порядке»: назови файл через --файл")
        return 0
    # Улика «создание» плюс РОДЫ самих файлов: новый `.py` дерева сервера — это ещё и
    # `правка-сервера`, и приёмка сервера обязана его увидеть, а не только храповик текста.
    судьи = _evidence.наряд(["создание", *_evidence.rods_of(файлы)])
    слова = свести(файлы, {и: поднять(и, к, полностью=True)[2] for и, к in судьи})
    долг = 0
    for ф in файлы:
        сказано = слова[ф]
        print(f"\n▸ {ф} — судей поднято {len(судьи)}: {', '.join(и for и, _ in судьи)}")
        for с in сказано:
            print(f"   ✗ {с}")
        if not сказано:
            print("   ✓ ни один судья не назвал этот файл — он уже в их наборах и конфигурациях")
        долг += len(сказано)
        if таски and сказано:
            for с in сказано:
                _stamp.sign("проба", "ОСТАТОК", f"новый файл {ф}: {с[:120]}",
                            intent="новый файл под набором судей",
                            expected="файл входит в наборы и конфигурации судей",
                            cmd=f".venv/bin/python scripts/guards/patrol.py --новый --файл {ф}",
                            root=ROOT)
    if долг and not таски:
        print(f"\nнесделанного {долг}. Записать остатками (их прочтут сторожа намерений и "
              f"дисциплины): повтори с --таски")
    return долг


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--улика", action="append", default=[], help="главная улика либо род")
    parser.add_argument("--файл", action="append", default=[], help="тронутый файл — род выведется сам")
    parser.add_argument("--кто", action="store_true", help="сухой ход: кто отзовётся, без запуска")
    parser.add_argument("--улики", action="store_true", help="словарь улик и кого они поднимают")
    parser.add_argument("--новый", action="store_true",
                        help="новый файл под набором судей: что каждый сказал про него")
    parser.add_argument("--таски", action="store_true",
                        help="несделанное записать остатками — их читают оба сторожа")
    parser.add_argument("--задача", metavar="ТИП",
                        help="разбор типа работы: улика, кто проснётся сам, кого поднять рукой")
    parser.add_argument("--ярус", action="store_true",
                        help="какой ярус прогонов нужен этой правке — по радиусу, не «на глаз»")
    parser.add_argument("--факт", action="store_true",
                        help="сверить след: кто из обязанных хуков сработал, а кто промолчал")
    parser.add_argument("--за", type=float, default=60.0, metavar="МИНУТ",
                        help="окно свежести отметки для --факт (по умолчанию 60)")
    args = parser.parse_args(argv)

    if args.улики:
        return словарь()
    if args.задача:
        return задача(args.задача)
    if args.ярус:
        return ярус()
    if args.новый:
        долг = новый(list(args.файл), args.таски)
        return _verdict.close("новый файл под набором судей", долг,
                              " ".join([".venv/bin/python scripts/guards/patrol.py",
                                        *(argv if argv is not None else sys.argv[1:])]))

    улики = улики_наряда(args)
    известные = set(_evidence.главные()) | set(_evidence.роды())
    if (чужие := [у for у in улики if у not in известные]):
        print(f"patrol: улика не объявлена: {чужие}; известны {sorted(известные)}", file=sys.stderr)
        return 2
    if not улики:
        print("patrol: улики нет — дерево чисто и ни одна не названа. Это не «всё хорошо»: "
              "назови улику (--улика) или файл (--файл), иначе поднимать нечего.")
        return 0

    набор = _evidence.наряд(улики)
    главные = _evidence.expand(улики)
    print(f"── наряд по улике: {', '.join(улики)} → главные: {', '.join(главные) or '—'}")

    if args.факт:
        молчали = факт(главные or улики, args.за)
        return _verdict.close(f"факт срабатывания по улике {', '.join(главные) or '—'}", молчали,
                              " ".join([".venv/bin/python scripts/guards/patrol.py",
                                        *(argv if argv is not None else sys.argv[1:])]),
                              детали={"окно_минут": args.за})
    if not набор:
        print("   на эту улику не отзывается никто — это находка, а не тишина: "
              "объяви сторожа в scripts/guards/evidence.yaml")
        return 1
    if args.кто:
        for имя, команда in набор:
            print(f"   · {имя} {' '.join(команда)}")
        return 0

    красных = 0
    for имя, команда in набор:
        сторож, код, последнее = поднять(имя, команда)
        итог = "чисто" if код == 0 else ("НЕ СУДИЛ" if код == 2 else "КРАСНЫЙ")
        print(f"   {'✓' if код == 0 else '✗'} {сторож} {' '.join(команда)} — {итог} · {последнее[:100]}")
        красных += код != 0
    return _verdict.close(f"наряд по улике {', '.join(главные)}", красных,
                          " ".join([".venv/bin/python scripts/guards/patrol.py",
                                    *(argv if argv is not None else sys.argv[1:])]),
                          точный=False)


if __name__ == "__main__":
    sys.exit(main())
