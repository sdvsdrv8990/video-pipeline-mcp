#!/usr/bin/env python3
"""scripts/quality_metrics.py — замер метрик качества на СВОЕЙ разметке.

Не судит и потолка не держит: отвечает на вопрос «какая метрика указывает на файлы, где НАШИ
находки реально случились». Разметку даёт реестр находок, метрики — `ast` и `git log`.

Оговорка, без которой числа врут: метка — «находка НАЗВАЛА файл», а искали мы чаще там, где
работали, поэтому churn частично предсказывает, ГДЕ МЫ СМОТРЕЛИ. Контроль третями по размеру
конфаунд ослабляет, но не снимает.

    .venv/bin/python scripts/quality_metrics.py
"""
from __future__ import annotations

import ast
import collections
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
ВЕТВИ = (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.AsyncFor)
ВНЕ = ("node_modules", "__pycache__", "/.venv/", "/.git/", "studio_emulation")


def разметка() -> collections.Counter:
    """Файлы, которые называли находки. Метку даёт реестр, а не мнение."""
    текст = (ROOT / "docs/roadmap/02_findings.md").read_text(encoding="utf-8")
    путь = re.compile(r"`([A-Za-z_0-9./\-]+\.(?:py|yaml|yml|toml|md))[`:]")
    счёт: collections.Counter = collections.Counter()
    for строка in текст.split("\n"):
        if re.match(r"^\|\s*~?~?F\d+", строка):
            for m in путь.finditer(строка):
                if (ROOT / m.group(1)).exists():
                    счёт[m.group(1)] += 1
    return счёт


def _цикло(дерево: ast.AST) -> int:
    n = 1
    for узел in ast.walk(дерево):
        if isinstance(узел, ВЕТВИ):
            n += 1
        elif isinstance(узел, ast.BoolOp):
            n += len(узел.values) - 1
        elif isinstance(узел, (ast.IfExp, ast.comprehension)):
            n += 1
    return n


def _когнитивная(дерево: ast.AST) -> int:
    """SonarSource в упрощении: ветвление стоит +1 и ЕЩЁ +1 за каждый уровень вложенности."""
    итог = 0

    def обойти(узел: ast.AST, глубина: int) -> None:
        nonlocal итог
        for ребёнок in ast.iter_child_nodes(узел):
            вложено = isinstance(ребёнок, ВЕТВИ)
            if вложено:
                итог += 1 + глубина
            elif isinstance(ребёнок, ast.BoolOp):
                итог += 1
            обойти(ребёнок, глубина + 1 if вложено else глубина)

    обойти(дерево, 0)
    return итог


def замер() -> dict[str, dict]:
    churn: collections.Counter = collections.Counter()
    вывод = subprocess.run(["git", "log", "--format=%H", "--name-only"],
                           cwd=ROOT, capture_output=True, text=True).stdout
    for строка in вывод.split("\n"):
        if строка.endswith(".py"):
            churn[строка] += 1
    итог: dict[str, dict] = {}
    ввоз: collections.Counter = collections.Counter()
    for p in ROOT.rglob("*.py"):
        if any(x in str(p) for x in ВНЕ):
            continue
        rel = str(p.relative_to(ROOT))
        try:
            текст = p.read_text(encoding="utf-8")
            дерево = ast.parse(текст)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        итог[rel] = {"loc": len([s for s in текст.split("\n") if s.strip()]),
                     "cyclo": _цикло(дерево), "cog": _когнитивная(дерево),
                     "churn": churn.get(rel, 0), "isinstance": 0, "признак": 0}
        for узел in ast.walk(дерево):
            if isinstance(узел, ast.Call) and isinstance(узел.func, ast.Name):
                if узел.func.id == "isinstance":
                    итог[rel]["isinstance"] += 1
                elif узел.func.id in ("hasattr", "getattr") and len(узел.args) >= 2:
                    итог[rel]["признак"] += 1
            if isinstance(узел, ast.ImportFrom) and узел.module:
                ввоз[узел.module.replace(".", "/")] += 1
            elif isinstance(узел, ast.Import):
                for a in узел.names:
                    ввоз[a.name.replace(".", "/")] += 1
    for rel, запись in итог.items():
        запись["fanin"] = ввоз.get(rel[:-3].replace("/__init__", ""), 0)
        запись["hotspot"] = запись["churn"] * запись["cog"]
    return итог


def main() -> int:
    m, d = замер(), разметка()
    меченые = {f for f in d if f.endswith(".py") and f in m}
    база = len(меченые) / len(m)
    print(f"файлов .py {len(m)} · с меткой находки {len(меченые)} · базовая доля {база:.1%}")

    print("\nТочность топ-20 и подъём над базой:")
    for имя in ("churn", "loc", "hotspot", "cyclo", "cog", "fanin"):
        ранг = sorted(m, key=lambda f: -m[f][имя])[:20]
        точн = sum(1 for f in ранг if f in меченые) / 20
        print(f"  {имя:8} {точн:>5.0%}  подъём {точн/база:.1f}×")

    print("\nСверх размера (трети по размеру; верхняя половина по метрике / нижняя):")
    по_размеру = sorted(m, key=lambda f: m[f]["loc"])
    n = len(по_размеру) // 3
    трети = [по_размеру[:n], по_размеру[n:2 * n], по_размеру[2 * n:]]
    for имя in ("churn", "hotspot", "cog", "cyclo", "fanin"):
        части, делит = [], 0
        for треть in трети:
            ранг = sorted(треть, key=lambda f: -m[f][имя])
            в, н = ранг[:len(ранг) // 2], ранг[len(ранг) // 2:]
            дв = sum(1 for f in в if f in меченые) / max(len(в), 1)
            дн = sum(1 for f in н if f in меченые) / max(len(н), 1)
            части.append(f"{дв:>4.0%}/{дн:<4.0%}")
            делит += дв - дн > 0.10
        print(f"  {имя:8} " + " ".join(части) + f"  разделяет {делит} из 3")

    print("\nПрочность — плотность признака на 1000 строк:")
    for имя in ("isinstance", "признак"):
        def плотн(набор: set, поле: str = имя) -> float:
            строк = sum(m[f]["loc"] for f in набор) or 1
            return sum(m[f][поле] for f in набор) / строк * 1000
        б, ч = плотн(меченые), плотн(set(m) - меченые)
        вывод = "РАЗЛИЧАЕТ" if б > ч * 1.3 else ("инвертирован" if ч > б * 1.3 else "не различает")
        print(f"  {имя:11} болевшие {б:>5.1f} · чистые {ч:>5.1f}  {вывод}")
    print(f"\nвсего isinstance {sum(v['isinstance'] for v in m.values())} · "
          f"признак {sum(v['признак'] for v in m.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
