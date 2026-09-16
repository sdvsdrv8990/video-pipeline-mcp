#!/usr/bin/env python3
"""scripts/guards/_place.py — место: где символ лежит и куда класть новый.

Зовёт: _evidence, _live.
Зовут: invariants.

Источник улики, не судья: отвечает на вопрос, который решался догадкой, — в каком модуле и в каком
его месте живёт код и куда встаёт новый.

`ast`, а не граф символов: место берётся дёшево и надёжно, без сети и индекса; граф отвечает на
другой вопрос («кто зовёт») и остаётся второй уликой. Ссылка адресуется СИМВОЛОМ, а не строкой:
номер строки протухает от чужой правки молча, и проверить его нечем.

    _place.py --файл X · --символ X#имя · --ссылки · --память · --json (для тандема и скилов)
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ССЫЛКА_СТРОКОЙ = re.compile(r"\b([\w./-]+\.(?:py|ts|tsx|yaml|yml|md))[:#](\d{1,5})\b")
РОДЫ = {ast.FunctionDef: "функция", ast.AsyncFunctionDef: "функция", ast.ClassDef: "класс"}


def _короткое(файл: Path) -> str:
    """Путь от корня, а вне корня — как дали. Подставное дерево набора лежит во временном
    каталоге, и `relative_to` там падает: источник улики обязан отвечать и о чужом файле."""
    try:
        return str(файл.resolve().relative_to(ROOT))
    except ValueError:
        return str(файл)


def _дерево(файл: Path) -> ast.AST | None:
    try:
        return ast.parse(файл.read_text(encoding="utf-8", errors="replace"), filename=str(файл))
    except (OSError, SyntaxError):
        return None                          # не разбирается — «улики нет», а не пустой список


def символы(файл: Path) -> dict[str, dict]:
    """Символы модуля: имя → род, первая и последняя строка.

    Вложенные зовутся через точку (`Класс.метод`): без этого два метода с одним именем в разных
    классах слились бы в один, и ссылка вела бы не туда — ровно та беда, ради которой ссылку и
    переводят со строки на имя.
    """
    дерево = _дерево(файл)
    if дерево is None:
        return {}
    найдено: dict[str, dict] = {}

    def обойти(узел: ast.AST, префикс: str) -> None:
        for ветка in ast.iter_child_nodes(узел):
            род = РОДЫ.get(type(ветка))
            if not род:
                continue
            имя = f"{префикс}{ветка.name}"                                        # type: ignore[attr-defined]
            найдено[имя] = {"род": род,
                            "строка": ветка.lineno,                               # type: ignore[attr-defined]
                            "конец": getattr(ветка, "end_lineno", ветка.lineno),  # type: ignore[attr-defined]
                            "приватный": ветка.name.startswith("_")}              # type: ignore[attr-defined]
            обойти(ветка, f"{имя}.")

    обойти(дерево, "")
    return найдено


def символ_на(файл: Path, строка: int) -> str:
    """Символ, накрывающий эту строку. Пусто — строка на уровне модуля либо за его пределами.

    Из вложенных берётся САМЫЙ УЗКИЙ: метод точнее своего класса, и перевод ссылки со строки на
    символ обязан сохранять точность, а не терять её.
    """
    накрывают = [(имя, т) for имя, т in символы(файл).items()
                 if т["строка"] <= строка <= т["конец"]]
    if not накрывают:
        return ""
    return min(накрывают, key=lambda п: п[1]["конец"] - п[1]["строка"])[0]


def зона(цель: Path | str) -> str:
    """Зона файла из объявления — та же, которой судятся записи."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _evidence
    путь = str(цель)
    if Path(путь).is_absolute():
        try:
            путь = str(Path(путь).resolve().relative_to(ROOT))
        except ValueError:
            return ""
    return _evidence.зона_пути(путь)


def куда(файл: Path) -> dict:
    """Параметры вставки: куда в ЭТОМ модуле встаёт новый символ.

    Точка вставки — перед хвостом запуска (`main`, `if __name__`), а не в конец файла: хвост
    обязан оставаться последним, и дописанное после него читается как мёртвое.
    """
    таблица = символы(файл)
    текст = файл.read_text(encoding="utf-8", errors="replace").splitlines()
    запуск = next((n for n, s in enumerate(текст, 1) if s.startswith("if __name__")), 0)
    хвост = таблица.get("main", {}).get("строка", 0)
    порог = min([x for x in (запуск, хвост) if x] or [0])
    публичные = [и for и, т in таблица.items() if not т["приватный"] and "." not in и]
    return {"файл": _короткое(файл),
            "зона": зона(файл),
            "строк": len(текст),
            "символов": len(таблица),
            "публичных": len(публичные),
            "перед": порог or None,
            "вставка": (f"перед строкой {порог}" if порог else "в конец файла"),
            "почему": ("хвост запуска обязан остаться последним" if порог
                       else "хвоста запуска в модуле нет")}


def радиус(файл: Path) -> dict:
    """Всё, что известно о месте, ОДНИМ ответом — его и спрашивает тандем через `--json`."""
    таблица = символы(файл)
    return {**куда(файл),
            "символы": [{"имя": и, **т} for и, т in sorted(таблица.items(),
                                                           key=lambda п: п[1]["строка"])]}


def _разбор_ссылки(root: Path, где: Path, строка_где: int, путь: str, номер: int) -> dict:
    """Один исход ссылки. Три исхода различаются намеренно: цели нет · строка за пределом файла
    (доказуемо битая) · строка внутри файла (непроверяемая — самая частая и самая тихая)."""
    цель = root / путь
    запись = {"где": f"{где.relative_to(root)}:{строка_где}", "цель": путь, "строка": номер}
    if not цель.exists():
        return {**запись, "исход": "цели нет", "символ": ""}
    всего = len(цель.read_text(encoding="utf-8", errors="replace").splitlines())
    if номер > всего:
        return {**запись, "исход": "за пределом файла", "символ": "", "строк": всего}
    символ = символ_на(цель, номер) if цель.suffix == ".py" else ""
    return {**запись, "исход": "внутри файла", "символ": символ}


def ссылки_на_строку(root: Path = ROOT) -> list[dict]:
    """Ссылки вида `файл:строка` в живых зонах — и во что каждая обязана превратиться."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _live
    итог = []
    for имя_зоны in _live.ЖИВЫЕ_ЗОНЫ:
        цель = root / имя_зоны
        файлы = [цель] if цель.is_file() else (sorted(цель.rglob("*")) if цель.is_dir() else [])
        for файл in файлы:
            if not файл.is_file() or файл.suffix not in _live.РАСШИРЕНИЯ:
                continue
            if not _live._живой(файл, root):
                continue
            for n, строка in enumerate(файл.read_text(encoding="utf-8",
                                                      errors="replace").splitlines(), 1):
                if "://" in строка:
                    continue             # адрес в сети не место в дереве: `…/CHANGELOG.md:9`
                for сов in ССЫЛКА_СТРОКОЙ.finditer(строка):
                    # `sha:файл.py:строка` — файл НА ТОТ КОММИТ, где строка существовала.
                    # Судить её длиной сегодняшнего файла значит обвинять историю.
                    if сов.start() and строка[сов.start() - 1] == ":":
                        continue
                    итог.append(_разбор_ссылки(root, файл, n, сов.group(1), int(сов.group(2))))
    return итог


def битые_ссылки(root: Path = ROOT) -> list[str]:
    """Ссылки, битость которых ДОКАЗАНА: цель СУЩЕСТВУЕТ, а строки в ней нет.

    Судится только этот исход. «Цели нет» сюда не входит намеренно: так выглядит и фикстура
    набора (`core/x.py`), и ссылка по одному имени файла без пути — обвинять их значит краснеть
    на данных, а ложное обвинение выключает сторожа быстрее, чем его отсутствие. Ссылка же в
    существующий файл за его последнюю строку фикстурой быть не может.
    """
    return [f"{с['где']} зовёт {с['цель']}:{с['строка']}, а в файле строк {с['строк']} — "
            f"ссылка ведёт в пустоту"
            for с in ссылки_на_строку(root) if с["исход"] == "за пределом файла"]


def разделы_памяти(root: Path = ROOT) -> list[dict]:
    """Разделы памяти с их зонами: чтение и запись памяти спрашивают то же объявление, что код."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _evidence
    import invariants
    дом = invariants.memory_dir(root)
    if not дом.is_dir():
        return []
    return [{"раздел": ф.stem, "зона": _evidence.зона_раздела(ф.stem),
             "символов": len(ф.read_text(encoding="utf-8", errors="replace"))}
            for ф in sorted(дом.glob("*.md")) if ф.name != "MEMORY.md"]


def main() -> int:
    try:                                       # след: без отметки механизм выглядит пылящимся
        sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
        import _trace
        _trace.mark("_place.py")
    except Exception:  # след не важнее самой работы
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--файл", help="модуль: символы, зона, куда класть новое")
    ap.add_argument("--символ", help="ссылка вида путь#имя — где он и сколько занимает")
    ap.add_argument("--ссылки", action="store_true", help="ссылки на строку в живых зонах")
    ap.add_argument("--память", action="store_true", help="разделы памяти и их зоны")
    ap.add_argument("--json", action="store_true", help="машинный ответ для тандема и скилов")
    a = ap.parse_args()

    if getattr(a, "символ"):
        путь, _, имя = getattr(a, "символ").partition("#")
        таблица = символы(ROOT / путь)
        ответ = {"цель": путь, "имя": имя, "найден": имя in таблица, **(таблица.get(имя) or {})}
        if not ответ["найден"]:
            ответ["похожие"] = [и for и in таблица if имя.lower() in и.lower()][:5]
        print(json.dumps(ответ, ensure_ascii=False, indent=2))
        return 0 if ответ["найден"] else 1

    if getattr(a, "файл"):
        ответ = радиус(ROOT / getattr(a, "файл"))
        if a.json:
            print(json.dumps(ответ, ensure_ascii=False, indent=2))
        else:
            print(f"зона {ответ['зона'] or '—'} · строк {ответ['строк']} · "
                  f"символов {ответ['символов']} (публичных {ответ['публичных']})")
            print(f"вставка: {ответ['вставка']} — {ответ['почему']}")
            for с in ответ["символы"]:
                print(f"  {с['строка']:5}–{с['конец']:<5} {с['род']:8} {с['имя']}")
        return 0

    if getattr(a, "память"):
        разделы = разделы_памяти()
        print(json.dumps(разделы, ensure_ascii=False, indent=2) if a.json
              else "\n".join(f"  {р['раздел']:32} зона {р['зона'] or '—'}" for р in разделы))
        return 0

    ссылки = ссылки_на_строку()
    if a.json:
        print(json.dumps(ссылки, ensure_ascii=False, indent=2))
        return 0
    по_исходу: dict[str, int] = {}
    for с in ссылки:
        по_исходу[с["исход"]] = по_исходу.get(с["исход"], 0) + 1
    for исход, n in sorted(по_исходу.items(), key=lambda п: -п[1]):
        print(f"  {исход:20} {n}")
    for беда in битые_ссылки():
        print(f"   ✗ {беда}")
    print(f"всего ссылок на строку: {len(ссылки)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
