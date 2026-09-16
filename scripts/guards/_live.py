#!/usr/bin/env python3
"""scripts/guards/_live.py — живая величина: число о дереве, у которого есть команда.

Зовёт: _evidence.
Зовут: invariants, _place.

Источник улики, не судья: гоняет объявленные команды, находит маркеры `⟨живое имя=N⟩` в тексте и
говорит, где написанное разошлось с прогоном; вердикт по этому выносит `invariants`.

Почему маркер, а не разбор прозы: угадать, какая цифра в тексте — утверждение о НАШЕМ дереве,
нельзя. Счёт чужого репозитория и число внутри закрытой находки, записанное как прошлое, для любого
разбора неотличимы от утверждения, и ложное обвинение выключает сторожа быстрее, чем его отсутствие.
Поэтому бремя перевёрнуто: утверждение о дереве само несёт имя своего источника.

    _live.py --список              # что объявлено и чему равно сейчас
    _live.py --сверить             # где написанное разошлось с прогоном (код 1, если разошлось)
    _live.py --обнови              # переписать маркеры прогоном
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Маркер несёт и ИМЯ величины, и её значение: имя даёт источник, значение делает прозу читаемой
# без прогона. Без значения текст пришлось бы рендерить перед чтением, а его читают глазами в git.
МАРКЕР = re.compile(r"⟨живое\s+([\w-]+)=([^⟩]*)⟩")
ЖИВЫЕ_ЗОНЫ = (".claude/skills", ".claude/hooks", "scripts/guards", "docs/roadmap", "README.md",
              "tests")
# Журнал сессий и его записи заморожены: там число записано как ПРОШЛОЕ, и переписать его
# прогоном значит подделать историю. Единственная зона, где расхождение с диском законно.
ЗАМОРОЖЕНО = ("docs/roadmap/sessions", "docs/roadmap/_sessions.md")
РАСШИРЕНИЯ = (".md", ".py", ".yaml", ".yml", ".txt")
# Чужое дерево внутри нашего: судить его нашими правилами не за что, а обход стоит секунд.
ЧУЖОЕ = ("node_modules", ".venv", "__pycache__", ".git")


def объявление(path: Path | None = None) -> dict:
    """Словарь живых величин."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _evidence
    return _evidence.словарь(path).get("живые") or {}


def прогон(команда: str, root: Path = ROOT) -> tuple[str, str]:
    """Значение величины и причина, если его нет. Пустой вывод — ОТКАЗ, а не пустое значение:
    так выглядит битая команда-доказательство, и принять её молча значит завести величину,
    которая согласна с любым написанным числом."""
    try:
        r = subprocess.run(["bash", "-lc", команда], cwd=str(root), capture_output=True,
                           text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return "", f"команда не запустилась: {e}"
    if r.returncode != 0:
        return "", f"команда вернула код {r.returncode}: {(r.stderr or '').strip()[:120]}"
    значение = r.stdout.strip()
    if not значение:
        return "", "команда отработала, но не напечатала ничего — сверять нечем"
    return значение, ""


def величины(root: Path = ROOT, декл: dict | None = None) -> dict[str, tuple[str, str]]:
    """Имя → (значение, причина отказа). Гоняется КАЖДАЯ объявленная команда, а не только та, на
    которую нашёлся маркер: битая команда обязана краснеть до того, как ею воспользуются.

    `декл` подставляется набором: иначе проверить сторожа можно было бы только через настоящее
    объявление, и набор судил бы состояние дерева вместо поведения сторожа.
    """
    итог = {}
    for имя, тело in (объявление() if декл is None else декл).items():
        команда = (тело or {}).get("команда") or ""
        итог[имя] = прогон(команда, root) if команда else ("", "у величины нет команды")
    return итог


def _живой(файл: Path, root: Path) -> bool:
    отн = str(файл.relative_to(root)).replace("\\", "/")
    if any(часть in ЧУЖОЕ for часть in отн.split("/")):
        return False
    if any(отн == з or отн.startswith(з.rstrip("/") + "/") for з in ЗАМОРОЖЕНО):
        return False
    return any(отн == з or отн.startswith(з.rstrip("/") + "/") for з in ЖИВЫЕ_ЗОНЫ)


def маркеры(root: Path = ROOT) -> list[tuple[Path, int, str, str]]:
    """Маркеры живых зон: (файл, строка, имя величины, написанное значение)."""
    найдено = []
    for зона in ЖИВЫЕ_ЗОНЫ:
        цель = root / зона
        файлы = [цель] if цель.is_file() else (sorted(цель.rglob("*")) if цель.is_dir() else [])
        for файл in файлы:
            if not файл.is_file() or файл.suffix not in РАСШИРЕНИЯ or not _живой(файл, root):
                continue
            if файл.resolve() == Path(__file__).resolve():
                continue                 # спецификация маркера — не маркер: иначе сторож обвинит
                                         # собственный докстринг в зове необъявленной величины
            try:
                текст = файл.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "⟨живое" not in текст:
                continue
            for n, строка in enumerate(текст.splitlines(), 1):
                for имя, значение in МАРКЕР.findall(строка):
                    найдено.append((файл, n, имя, значение.strip()))
    return найдено


def расхождения(root: Path = ROOT, декл: dict | None = None) -> list[str]:
    """Где написанное разошлось с прогоном либо зовёт необъявленную величину."""
    факт = величины(root, декл)
    беды = []
    for имя, (_, причина) in sorted(факт.items()):
        if причина:
            беды.append(f"величина `{имя}` не измеряется: {причина}")
    for файл, n, имя, написано in маркеры(root):
        где = f"{файл.relative_to(root)}:{n}"
        if имя not in факт:
            беды.append(f"{где}: маркер зовёт `{имя}` — такой величины в объявлении нет")
            continue
        значение, причина = факт[имя]
        if причина:
            continue                     # о битой команде сказано выше, второй раз не обвиняем
        if написано != значение:
            беды.append(f"{где}: `{имя}` написано {написано!r}, прогон даёт {значение!r} — "
                        f"`_live.py --обнови` перепишет")
    return беды


def обнови(root: Path = ROOT) -> int:
    """Переписать маркеры прогоном. Правится ТОЛЬКО значение внутри маркера: проза вокруг остаётся
    авторской, а число перестаёт быть её частью."""
    факт = величины(root)
    тронуто = 0
    for файл in sorted({ф for ф, *_ in маркеры(root)}):
        текст = файл.read_text(encoding="utf-8")

        def замена(m: re.Match) -> str:
            значение, причина = факт.get(m.group(1), ("", "величина не объявлена"))
            return m.group(0) if причина else f"⟨живое {m.group(1)}={значение}⟩"

        новый = МАРКЕР.sub(замена, текст)
        if новый != текст:
            файл.write_text(новый, encoding="utf-8")
            тронуто += 1
            print(f"обновлён {файл.relative_to(root)}")
    print(f"файлов переписано: {тронуто}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--список", action="store_true", help="что объявлено и чему равно сейчас")
    ap.add_argument("--сверить", action="store_true", help="где написанное разошлось с прогоном")
    ap.add_argument("--обнови", action="store_true", help="переписать маркеры прогоном")
    a = ap.parse_args()
    if getattr(a, "обнови"):
        return обнови()
    if getattr(a, "список"):
        for имя, (значение, причина) in sorted(величины().items()):
            отказ = f"   ОТКАЗ: {причина}" if причина else ""
            print(f"  {имя:18} = {значение or '—'}{отказ}")
        print(f"маркеров в живых зонах: {len(маркеры())}")
        return 0
    беды = расхождения()
    for б in беды:
        print(f"   ✗ {б}")
    print(f"расхождений: {len(беды)}")
    return 1 if беды else 0


if __name__ == "__main__":
    sys.exit(main())
