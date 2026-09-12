"""scripts/radius_survey.py — чем сегодня судится каждый род связи в дереве, а где не судится ничем.

Рода берутся из дерева разбором, и рядом с каждым называется ЕГО источник улики; где источника нет,
печатается «улики нет», а не ноль — ноль читался бы как «связей такого рода нет».

Не судит: кода возврата «хорошо или плохо» здесь нет. Счёт по `ast` — НИЖНЯЯ граница по природе:
подстановка цели из данных видна как признак, а не как разрешённая цель.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ЗОНЫ = {
    "сервер": ("core/", "tools/", "server.py"),
    "наборы и скрипты": ("tests/", "scripts/"),
}
# Группы родов: заказ владельца — рода делим на ГРУППЫ и пополняем по необходимости.
ГРУППЫ = {
    "связь кода": ("импорт", "вызов функции", "вызов класса", "повтор"),
    "решение по данным": ("разрешение имени", "ветвление по значению"),
    "доезд значения": ("рубеж потока", "объявление→читатель", "отказ→реакция"),
    "смысл": ("цепочка по назначению",),
}


def файлы(зона: str) -> list[Path]:
    кор = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    корни = ЗОНЫ[зона]
    return [ROOT / rel for rel in кор if any(rel.startswith(k) for k in корни)]


def роды_по_ast(пути: list[Path]) -> Counter:
    """Счёт звеньев, видимых разбором: имена родов — из словаря групп, не из вкуса."""
    счёт: Counter = Counter()
    for путь in пути:
        try:
            дерево = ast.parse(путь.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for узел in ast.walk(дерево):
            if isinstance(узел, (ast.Import, ast.ImportFrom)):
                счёт["импорт"] += 1
            elif isinstance(узел, (ast.For, ast.While, ast.AsyncFor, ast.comprehension)):
                счёт["повтор"] += 1
            elif isinstance(узел, (ast.If, ast.IfExp, ast.Match)):
                счёт["ветвление по значению"] += 1
            elif isinstance(узел, ast.Call):
                цель = узел.func
                имя = цель.id if isinstance(цель, ast.Name) else getattr(цель, "attr", "")
                if имя in ("getattr", "import_module", "__import__"):
                    счёт["разрешение имени"] += 1
                elif isinstance(цель, (ast.Subscript,)) or имя == "get" and узел.args:
                    счёт["разрешение имени"] += 1
                elif имя[:1].isupper():
                    счёт["вызов класса"] += 1
                else:
                    счёт["вызов функции"] += 1
    return счёт


def ветви_в_покрытии() -> str:
    """Судится ли ветка прогоном: покрытие СТРОК отвечает «строка исполнилась», а не «ветка взята»."""
    данные = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    прогон = данные.get("tool", {}).get("coverage", {}).get("run", {})
    выкл = прогон.get("omit") or []
    если_ветви = прогон.get("branch")
    ответ = "ветви МЕРЯЮТСЯ" if если_ветви else "улики нет: покрытие ветвей выключено"
    return f"{ответ}; вне покрытия вовсе: {', '.join(выкл) or '—'}"


def потоки() -> str:
    """Читатель карты один — тот, которого зовут приёмка и шим инвариантов."""
    источник = _guards("_routes")
    if источник is None:
        return "улики нет: источник карты маршрутов не поднялся"
    маршруты = источник.routes()
    рубежи = sum(len(м.get("hops") or []) for м in маршруты)
    return f"прогон: след сценария-опоры; маршрутов {len(маршруты)}, рубежей {рубежи}"


def реакции() -> str:
    """Реестр читает ТОТ ЖЕ код, что сервер, а долг считает ось сторожа — второго разбора нет."""
    sys.path.insert(0, str(ROOT))
    try:
        from core.reactions import Reactions
    except Exception as беда:
        return f"улики нет: читатель реестра не поднялся ({type(беда).__name__})"
    объявлено = len(Reactions(ROOT / "config" / "server_reactions.yaml").reactions)
    сторож = _guards("invariants")
    if сторож is None:
        return f"объявлено {объявлено}; улики нет: ось долга не поднялась"
    долг = сторож.declared_but_unscripted()
    коды = [row for row in долг if "код отказа" in row]
    инструменты = [row for row in долг if "инструмент" in row]
    return (f"прогон: сценарий ждёт код; объявлено {объявлено}, "
            f"без сценария кодов {len(коды)} и инструментов {len(инструменты)}")


def объявления() -> str:
    """Ось «ручка без читателя» уже стоит в сторожах — здесь только её ответ."""
    сторож = _guards("invariants")
    if сторож is None:
        return "улики нет: ось «ручка без читателя» не поднялась"
    ручки = сторож.knob_without_reader()
    приватно = сторож.declaration_loaded_privately()
    return (f"оси сторожа: ручек без читателя {len(ручки)}, "
            f"объявлений в обход единой двери {len(приватно)}")


_ПАМЯТЬ: dict[str, str] = {}


def граф() -> str:
    """Предел второй улики показывается ЗАМЕРОМ на символе, у которого вызывающие точно есть.

    Ответ запоминается на прогон: он один и тот же для всех родов группы, а каждый вопрос к
    источнику поднимает сервер заново.
    """
    if "граф" in _ПАМЯТЬ:
        return _ПАМЯТЬ["граф"]
    источник = _guards("_symbol_graph")
    if источник is None:
        _ПАМЯТЬ["граф"] = "улики нет: источник графа не поднялся"
        return _ПАМЯТЬ["граф"]
    короткое = источник.callers(["safe_resolve"])
    полное = источник.callers(["core/paths.py#safe_resolve"])
    if короткое is None:
        _ПАМЯТЬ["граф"] = "улики нет: граф не ответил (нет сервера или сети)"
        return _ПАМЯТЬ["граф"]
    точек = len(короткое.get("safe_resolve") or [])
    грепом = len(subprocess.run(["git", "grep", "-n", "safe_resolve(", "--", "core", "tools", "server.py"],
                                cwd=ROOT, capture_output=True, text=True).stdout.splitlines())
    # Контракт источника: имя ОТСУТСТВУЕТ в ответе — улики по нему нет; пустой список значит
    # «граф знает символ и вызывающих не нашёл». Прежде эти два ответа сливались.
    непонятое = "core/paths.py#safe_resolve"
    честно = полное is not None and непонятое not in полное
    предел = ("; непонятую форму имени называет «улики нет»" if честно
              else "; на непонятую форму имени отвечает ПУСТОТОЙ — неотличимо от «вызывающих нет»")
    _ПАМЯТЬ["граф"] = (f"граф: НИЖНЯЯ граница, по короткому имени точек {точек} "
                       f"при {грепом} строках грепа{предел}")
    return _ПАМЯТЬ["граф"]


def _guards(имя: str):
    """Источники улики поднимаются по требованию: нет сервера или индекса — «улики нет», не ноль."""
    sys.path.insert(0, str(ROOT / "scripts" / "guards"))
    try:
        return __import__(имя)
    except Exception:
        return None


ОТКУДА = {
    "импорт": граф,
    "вызов функции": граф,
    "вызов класса": граф,
    "повтор": lambda: "улики нет: повтор не отличают от одного прохода ни прогон, ни граф",
    "разрешение имени": lambda: "улики нет: цель подставляется данными, графу она не видна",
    "ветвление по значению": ветви_в_покрытии,
    "рубеж потока": потоки,
    "объявление→читатель": объявления,
    "отказ→реакция": реакции,
    "цепочка по назначению": lambda: "улики нет: дома у цепочек ещё нет — это и есть заказ плана 29",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="машинный ответ для тандема и скилов")
    a = ap.parse_args()
    свод: dict[str, dict] = {}
    for зона in ЗОНЫ:
        свод[зона] = dict(роды_по_ast(файлы(зона)))
    улики = {род: источник() for группа in ГРУППЫ.values() for род in группа for источник in (ОТКУДА[род],)}
    if a.json:
        print(json.dumps({"зоны": свод, "улики": улики}, ensure_ascii=False, indent=2))
        return 0
    for группа, рода in ГРУППЫ.items():
        print(f"\n══ группа: {группа}")
        for род in рода:
            по_зонам = " · ".join(f"{зона}: {свод[зона].get(род, 0)}" for зона in ЗОНЫ) or "—"
            видно = по_зонам if any(свод[з].get(род) for з in ЗОНЫ) else "разбором не считается"
            print(f"  {род}")
            print(f"     в дереве: {видно}")
            print(f"     чем судится: {улики[род]}")
    print("\nПеречитать: .venv/bin/python scripts/radius_survey.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
