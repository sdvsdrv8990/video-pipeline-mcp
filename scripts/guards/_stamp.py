#!/usr/bin/env python3
"""scripts/guards/_stamp.py — подпись вывода: чем он был и по какому запросу.

Не сторож: ничего не судит и exit-кода не даёт. Отвечает на вопрос, которого сам вывод не несёт, —
УЛИКА это, ОТКАЗ или ВЕРДИКТ, каким было объявленное ожидание и где лежит полная запись.
Скопированная в другую сессию строка без подписи требует повторного дознания; с ключом хватает
одной команды: `reproduce.py --stamp <ключ>`.

Словари родов и ролей объявлены здесь и проверяются: незнакомое имя — отказ, а не тихая запись
неизвестно чего. Журнал свой (`stamps-*.jsonl`): производитель сценариев читает `trail-*` и
`scenarios-*`, и подмешивать в его вход другую форму записи нельзя.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ("tests", ".journal")
ROLES = ("УЛИКА", "ОТКАЗ", "ВЕРДИКТ", "ОСТАТОК", "ОТЛОЖЕН", "В РАБОТЕ")
# Членство здесь отнимает право ЗАКРЫВАТЬ хвост, а не меняет показ: признание — не итог.
ПРИЗНАНИЯ = ("ОТЛОЖЕН", "В РАБОТЕ")
KINDS = ("цикл", "гейт", "проба")
# Список исполнимых слов ПОИМЁННЫЙ, а не эвристика: «первое слово без пробелов» пропускает прозу
# («замер разделов памяти по regex» тоже начинается одним словом), и правило стало бы вакуумным.
RUNNERS = ("python", "python3", ".venv/bin/python", ".venv/bin/", "bash", "sh", "pytest", "ruff",
           "mypy", "bandit", "git", "grep", "sed", "awk", "ls", "find", "curl", "make", "npm",
           "node", "docker", "cat", "wc", "diff", "PYTHONPATH=", "VPM_")


def runnable(cmd: str) -> bool:
    """Строка похожа на команду: начинается исполнимым словом ЛИБО присваиванием среды.

    Судится ФОРМА, а не исполнимость: проверять запуском значит исполнять произвольную строку при
    подписи. Поэтому синтаксически битая команда правило проходит — и это ловится первым же
    повтором, а не молчанием.

    Присваивание (`PYTHONPATH=`, `VPM_`) исполнимым словом не является, но команда с него
    начинается законно — без этой половины законный повтор был бы отвергнут.
    """
    голова = cmd.strip()
    return bool(голова) and голова.startswith(RUNNERS)


def _head(root: Path) -> tuple[str, bool]:
    """Коммит и грязь дерева. Нет git — «улики нет», а не выдуманный ноль."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(root),
                             capture_output=True, text=True, timeout=20)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=str(root),
                               capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "", False
    if sha.returncode != 0:
        return "", False
    return sha.stdout.strip(), bool(dirty.stdout.strip())


def sign(kind: str, role: str, what: str, *, intent: str = "", expected: str = "",
         actual: str = "", cmd: str = "", detail: dict | None = None, closes: str = "",
         root: Path = ROOT) -> tuple[str, str]:
    """Записать наблюдение и вернуть (ключ, две строки подписи)."""
    if kind not in KINDS:
        raise ValueError(f"род `{kind}` не объявлен; известны {list(KINDS)}")
    if role not in ROLES:
        raise ValueError(f"роль `{role}` не объявлена; известны {list(ROLES)}")
    # Остаток судится тем же правилом: «чем продолжить» без команды — это пожелание, а не хвост.
    if role in ("УЛИКА", "ОСТАТОК") and not runnable(cmd):
        чем = "улика без команды повтора" if role == "УЛИКА" else "остаток без команды продолжения"
        raise ValueError(
            f"{чем}: `повторить` = {cmd!r}. То, что нельзя запустить, поднимать нечем — оно "
            f"стареет молча, как любая проза. Дай команду, начинающуюся с одного из "
            f"{list(RUNNERS[:6])}…")
    stamped = time.time()
    key = hashlib.sha1(f"{stamped}|{kind}|{what}".encode()).hexdigest()[:4]
    head, dirty = _head(root)
    day = time.strftime("%Y%m%d", time.localtime(stamped))
    path = root.joinpath(*JOURNAL) / f"stamps-{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = sum(1 for _ in path.open(encoding="utf-8")) + 1 if path.exists() else 1
    where = f"{'/'.join(JOURNAL)}/{path.name}:{line}"
    record = {"ts": stamped, "key": key, "kind": kind, "role": role, "what": what,
              "intent": intent, "expected": expected, "actual": actual, "cmd": cmd,
              "head": head, "dirty": dirty, "where": where, "closes": closes,
              "detail": detail or {}}
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
    keys = [f"род={kind}"]
    if closes:
        keys.append(f"закрывает={closes}")
    if intent:
        keys.append(f"намерение={intent}")
    if expected:
        keys.append(f"ждали={expected}")
    if actual:
        keys.append(f"факт={actual}")
    keys += [f"HEAD={head or 'нет git'}{' (дерево грязное)' if dirty else ''}", f"запись={where}"]
    return key, f"⟦vpm {key} {role}⟧ {what}\n⟦ключи⟧ " + " · ".join(keys)


def tails(root: Path = ROOT) -> list[dict]:
    """Незакрытые остатки, старые сверху. Закрытие — ДРУГАЯ подпись, назвавшая ключ хвоста.

    Читается весь журнал, а не последний день: хвост живёт до закрытия, и «неделю назад» — самый
    частый его возраст. Журнала нет — пусто, а не выдуманный ноль.
    """
    записи, закрыты, отложены, в_работе = [], set(), set(), set()
    for path in sorted(root.joinpath(*JOURNAL).glob("stamps-*.jsonl")):
        for row in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not row.strip():
                continue
            try:
                запись = json.loads(row)
            except json.JSONDecodeError:
                continue
            # Закрывает ТОЛЬКО не-признание: иначе «отложил» и «беру» тихо равнялись бы «закрыл»,
            # и хвост исчезал бы из показа вместе с запретом — та потеря, ради которой всё затевалось.
            if запись.get("closes") and запись.get("role") not in ПРИЗНАНИЯ:
                закрыты.add(запись["closes"])
            # Отложенный хвост из показа НЕ уходит: он не закрыт. Уходит он только из блокировки —
            # иначе «отложил» стало бы способом забыть, а не решением подождать.
            if запись.get("role") == "ОТЛОЖЕН" and запись.get("closes"):
                отложены.add(запись["closes"])
            # «В работе» отличается от «отложен» смыслом, а не действием: запрет снимают оба, но
            # показ должен различать взятое сегодня и отодвинутое — иначе брошенное не видно.
            if запись.get("role") == "В РАБОТЕ" and запись.get("closes"):
                в_работе.add(запись["closes"])
            if запись.get("role") == "ОСТАТОК":
                записи.append(запись)
    живые = [z for z in sorted(записи, key=lambda z: z.get("ts", 0)) if z["key"] not in закрыты]
    for хвост in живые:
        хвост["отложен"] = хвост["key"] in отложены
        хвост["в работе"] = хвост["key"] in в_работе
        хвост["признан"] = хвост["отложен"] or хвост["в работе"]
    return живые


def find(key: str, root: Path = ROOT) -> list[dict]:
    """Записи по ключу. Ключ короткий, поэтому совпадений бывает несколько — отдаём все."""
    found = []
    for path in sorted(root.joinpath(*JOURNAL).glob("stamps-*.jsonl")):
        for row in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not row.strip():
                continue
            try:
                record = json.loads(row)
            except json.JSONDecodeError:
                continue                 # обрыв строки не отменяет остальных
            if record.get("key") == key:
                found.append(record)
    return found


def цена(root: Path = ROOT) -> dict[str, dict]:
    """Экономика цикла по журналу: за что заплачено и что за это поймано.

    Считается по НАМЕРЕНИЯМ, а не по прогонам: повтор после фикса — тот же вопрос, заданный
    второй раз, и считать его отдельным наблюдением значит завышать выигрыш втрое.
    """
    итог: dict[str, dict] = {}
    for path in sorted(root.joinpath(*JOURNAL).glob("stamps-*.jsonl")):
        for row in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not row.strip():
                continue
            try:
                запись = json.loads(row)
            except json.JSONDecodeError:
                continue
            if запись.get("kind") != "цикл" or запись.get("role") != "ВЕРДИКТ":
                continue
            имя = запись.get("intent") or "(без имени)"
            подробность = запись.get("detail") or {}
            место = итог.setdefault(имя, {"прогонов": 0, "поймал": 0, "секунд": 0.0,
                                          "без длительности": 0})
            место["прогонов"] += 1
            место["поймал"] += len(подробность.get("риск") or [])
            секунд = float(подробность.get("секунд") or 0)
            место["секунд"] += секунд
            место["без длительности"] += секунд == 0
    return итог


def трение(дом: Path | None = None) -> list[tuple[int, int, int]]:
    """Отказы гейта фактов по сессиям: (отказов, разных ключей, упёртых).

    Читателя у этого счёта не было вовсе, поэтому «сторож кричит» не отличалось от «сторож молчит».
    Отказов примерно поровну с ключами — это пошлина за широту работы, а не упрямство ИИ.
    """
    корень = дом or Path.home() / ".claude" / "state" / "vpm-fact-gate"
    ряд = []
    for путь in sorted(корень.glob("*.json")) if корень.is_dir() else []:
        try:
            с = json.loads(путь.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ряд.append((int(с.get("denials") or 0), len(с.get("denied") or {}),
                    sum(1 for v in (с.get("denied_count") or {}).values() if v >= 3)))
    return sorted(ряд, reverse=True)


def печать_цены(итог: dict[str, dict]) -> None:
    """Отчёт для человека: что заплачено, что поймано, чего замерить не удалось."""
    if not итог:
        print("вердиктов цикла в журнале нет — считать нечего")
        return
    поймавших = sum(1 for м in итог.values() if м["поймал"])
    прогонов = sum(м["прогонов"] for м in итог.values())
    секунд = sum(м["секунд"] for м in итог.values())
    немые = sum(м["без длительности"] for м in итог.values())
    print(f"намерений {len(итог)} · прогонов {прогонов} · "
          f"поймали незаявленное {поймавших} из {len(итог)}")
    print(f"замерено {секунд / 60:.0f} мин; прогонов без записанной длительности: {немые}\n")
    for имя, м in sorted(итог.items(), key=lambda x: -x[1]["поймал"]):
        метка = f"поймал {м['поймал']}" if м["поймал"] else "вхолостую"
        цена_ = f"{м['секунд'] / 60:.1f} мин" if м["секунд"] else "длительность не записана"
        print(f"  {имя:26} прогонов {м['прогонов']}  {метка:12} {цена_}")
    ряд = трение()
    if ряд:
        отказов = sum(о for о, _, _ in ряд)
        ключей = sum(к for _, к, _ in ряд)
        упёртых = sum(у for _, _, у in ряд)
        print(f"\nтрение гейта фактов: отказов {отказов} · разных файлов {ключей} · "
              f"упёртых {упёртых} (сессий с отказами: {sum(1 for о, _, _ in ряд if о)})")
        print("  отказов ≈ файлов — пошлина за широту работы; отказов заметно больше — правка "
              "идёт без взгляда")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # `--tails` спрашивает журнал и ничего не подписывает, поэтому обязательные поля подписи для
    # него не обязательны: иначе прочитать остаток можно было бы только заведя новый.
    if "--цена" in sys.argv:
        печать_цены(цена())
        return 0
    if "--tails" in sys.argv:
        for хвост in tails():
            возраст = (time.time() - хвост.get("ts", 0)) / 86400
            print(f"⟦vpm {хвост['key']} ОСТАТОК⟧ {хвост['what']}  (дней: {возраст:.0f})")
            print(f"  ждали: {хвост.get('expected') or '—'}")
            print(f"  продолжить: {хвост.get('cmd') or '—'}")
        return 0
    ap.add_argument("--kind", required=True, choices=KINDS)
    ap.add_argument("--role", required=True, choices=ROLES)
    ap.add_argument("--what", required=True, help="одна строка: что это и что доказывает")
    ap.add_argument("--intent", default="", help="имя намерения или запроса")
    ap.add_argument("--expected", default="", help="что было объявлено ожидаемым")
    ap.add_argument("--actual", default="", help="что вышло на самом деле")
    ap.add_argument("--cmd", default="", help="команда повтора (для остатка — чем продолжить)")
    ap.add_argument("--closes", default="", help="ключ остатка, который эта подпись закрывает")
    ap.add_argument("--tails", action="store_true", help="показать незакрытые остатки и выйти")
    ap.add_argument("--цена", action="store_true",
                    help="экономика цикла по журналу: за что заплачено и что поймано")
    a = ap.parse_args()
    print(sign(a.kind, a.role, a.what, intent=a.intent, expected=a.expected,
               actual=a.actual, cmd=a.cmd, closes=a.closes)[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
