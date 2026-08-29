#!/usr/bin/env python3
"""
scripts/guards/reproduce.py — след отказа → объявление сценария

## Назначение
Отказ, случившийся один раз, воспроизводят по тому, что от него осталось. След сервера
(`core/observability/trail.py`) и журнал харнесса (`tests/.journal/*.jsonl`) пишут ОДНУ форму
записи, поэтому оба читаются здесь одним кодом.

## Границы
Производит, а не судит: exit-код говорит «нашёл / не нашёл», а не «хорошо / плохо». Сценарий,
который ни разу не был красным, регрессией не является — краснота доказывается отдельно
(`what_if.py` на кандидатном патче). Гонки не воспроизводятся вовсе: запись хранит порядок, не время.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (ROOT / "logs" / "trail", ROOT / "tests" / ".journal")
RUNNER = ROOT / "tests" / "scenarios" / "test_scenarios.py"
OBSERVATIONS = ROOT / "tests" / "harness" / "observations.yaml"
# Полная решётка растёт как 2^N: дальше карта нечитаема, а обход дороже пользы. Длиннее — минимизируй.
LATTICE_LIMIT = 4


def _entries(path: Path) -> list[dict]:
    """Строки одного файла записи. Битую строку пропускаем: обрыв не отменяет остального."""
    out = []
    for row in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not row.strip():
            continue
        try:
            entry = json.loads(row)
        except ValueError:
            continue
        # Отказ до диспетчера не несёт имени инструмента вовсе — отбор по `tool` выбрасывал бы
        # ровно те строки, ради которых вторая точка записи и ставилась.
        if entry.get("scenario") != "__run__" and (entry.get("tool") or entry.get("rpc") or entry.get("level")):
            out.append(entry)
    return out


def latest(explicit: str | None) -> Path:
    """Самый свежий файл записи из обоих источников, либо названный явно."""
    if explicit:
        return Path(explicit)
    files = [f for source in SOURCES if source.is_dir()
             for f in list(source.glob("trail-*.jsonl")) + list(source.glob("scenarios-*.jsonl"))]
    if not files:
        raise SystemExit(f"воспроизводить нечего: ни одной записи в {', '.join(map(str, SOURCES))}")
    return max(files, key=lambda f: f.stat().st_mtime)


def every_record() -> list[Path]:
    """ВСЕ записи обоих источников. Вердикт о послаблении по одному файлу — артефакт выбора файла:
    свежайшей записью бывает тонкий след на 41 вызов, и «факт не появился» означает в ней только
    то, что прогон был коротким."""
    return sorted(f for source in SOURCES if source.is_dir()
                  for f in list(source.glob("trail-*.jsonl")) + list(source.glob("scenarios-*.jsonl")))


def pick(entries: list[dict], tool: str | None, code: str | None) -> int:
    """Индекс ОТКАЗА, который воспроизводим. Последний подходящий: свежий интереснее старого."""
    for i in range(len(entries) - 1, -1, -1):
        entry = entries[i]
        if entry.get("ok"):
            continue
        # Удержание соединений — УЛИКА, а не воспроизводимый отказ: запросом его не повторить,
        # и сделать вид, что повторили, значит выдать другой сценарий за этот.
        if entry.get("level") == "socket":
            continue
        if tool and entry.get("tool") != tool:
            continue
        if code and entry.get("code") != code:
            continue
        return i
    raise SystemExit("в записи нет отказа под эти условия — воспроизводить нечего")


# Заголовки, которыми воспроизводится отказ периметра. Ключ сюда не попадает: он замаскирован
# в самой записи, и подставлять вместо него что-то своё значит воспроизводить другой отказ.
PERIMETER_HEADERS = ("Host", "Origin", "Content-Type")


def as_steps(entries: list[dict]) -> list[dict]:
    """Записи → шаги объявления. Успех проверяется фактами, отказ — кодом: «просто упало» не ожидание.

    Уровень решает ФОРМУ шага: до диспетчера инструмента нет, и воспроизводится такой отказ
    конвертом — методом протокола и заголовками, а не вызовом.
    """
    steps = []
    for entry in entries:
        expect: dict = {"ok": bool(entry.get("ok"))}
        if entry.get("ok"):
            if entry.get("facts"):
                expect["facts"] = list(entry["facts"])
        else:
            expect["code"] = entry.get("code") or "UNKNOWN_ERROR"
        level = entry.get("level") or "engine"
        if level == "socket":
            raise SystemExit("удержание соединений шагом не воспроизводится: это наблюдение нижнего "
                             "слоя, а не запрос — возьми отказ уровнем выше")
        if level == "engine":
            steps.append({"call": entry["tool"], "with": entry.get("args") or {}, "expect": expect})
            continue
        args = entry.get("args") or {}
        step = {"rpc": entry.get("rpc") or "tools/list", "with": {}, "expect": expect}
        headers = {k: args[k] for k in PERIMETER_HEADERS if args.get(k)}
        if headers:
            step["headers"] = headers
        if level == "identity":
            # Ключ в записи замаскирован — воспроизводим отсутствием, а не выдумкой. Отказ тот же
            # по коду, но это ДРУГАЯ его причина, и знать об этом читающему обязательно.
            step["token"] = ""
        steps.append(step)
    return steps


def render(name: str, why: str, steps: list[dict]) -> str:
    """Объявление в стиле проекта. Пишем руками, а не yaml.dump: тот ломает порядок и кавычит всё."""
    lines = [f"- scenario: {name}", f"  why: {why}", "  when:"]
    for step in steps:
        kind = "call" if "call" in step else "rpc"
        lines.append(f"    - {kind}: {step[kind]}")
        lines.append("      with:")
        for key, value in (step["with"] or {}).items():
            lines.append(f"        {key}: {json.dumps(value, ensure_ascii=False)}")
        if not step["with"]:
            lines[-1] = "      with: {}"
        if step.get("headers"):
            lines.append(f"      headers: {json.dumps(step['headers'], ensure_ascii=False)}")
        if "token" in step:
            lines.append(f"      token: {json.dumps(step['token'], ensure_ascii=False)}")
        lines.append("      expect:")
        for key, value in step["expect"].items():
            lines.append(f"        {key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def survives(steps: list[dict], name: str, why: str, target_code: str) -> bool:
    """Выживает ли отказ без выброшенных шагов. Один прогон на попытку — это и есть цена минимизации."""
    if not steps or steps[-1]["expect"].get("code") != target_code:
        return False
    probe = ROOT / "tests" / "scenarios" / "_reproduce_probe.yaml"
    probe.write_text(render(name, why, steps), encoding="utf-8")
    try:
        done = subprocess.run([sys.executable, str(RUNNER)], cwd=str(ROOT), capture_output=True,
                              text=True, timeout=900, env=_env() | {"VPM_SCENARIO": name})
        return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        probe.unlink(missing_ok=True)


def _env() -> dict:
    import os
    return dict(os.environ)


def minimize(steps: list[dict], name: str, why: str) -> list[dict]:
    """Выбрасываем шаг за шагом с конца к началу, пока отказ ВЫЖИВАЕТ.

    Двести вызовов — не сценарий, а полотно, которое никто не сопровождает. Идём от конца: поздние
    шаги чаще случайны, ранние чаще создают состояние, без которого отказа не будет.
    """
    target = steps[-1]["expect"].get("code") or ""
    kept = list(steps)
    index = len(kept) - 2                          # последний шаг — сам отказ, его не трогаем
    while index >= 0:
        trial = kept[:index] + kept[index + 1:]
        if survives(trial, name, why, target):
            kept = trial
        index -= 1
    return kept



def _dig(entry: dict, path: str):
    """Значение по объявленному адресу вида `args.path`. Нет — None, и это скажут вслух.

    Сегмент `*` разворачивает список: `data.files.*` и `data.created.*.path` дают ПО ЗНАЧЕНИЮ НА
    ЭЛЕМЕНТ — один вызов рождает несколько предметов, и каждый спрашивается отдельно.
    """
    node: object = entry
    for part in path.split("."):
        if part == "*":
            if not isinstance(node, list):
                return None
            return node
        if isinstance(node, list):
            return [(_step(item, part)) for item in node]
        node = _step(node, part)
        if node is None:
            return None
    return node


def _step(node: object, key: str):
    return node.get(key) if isinstance(node, dict) else None


REF = re.compile(r"^\$\{(identity|args\.[\w.]+|data\.[\w.]+)\}$")


def _fill(value, item: dict):
    """`${identity}` — имя предмета, `${args.x}`/`${data.x}` — значение из ЗАПИСАННОГО вызова.

    Второй адрес нужен потому, что не всякий предмет назывался одним именем: лист живёт в паре
    «книга + имя», столбец — в тройке, и одним `identity` их не спросить.
    """
    if not isinstance(value, str):
        return value
    matched = REF.match(value)
    if not matched:
        return value
    where = matched.group(1)
    if where == "identity":
        return item["name"]
    got = _dig(item["entry"], where)
    if got is None:
        raise SystemExit(f"наблюдение факта {item['fact']}: по адресу {where} значения нет — "
                         "поправь объявление в tests/harness/observations.yaml")
    return got


def _fill_deep(value, item: dict):
    if isinstance(value, dict):
        return {k: _fill_deep(v, item) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_deep(v, item) for v in value]
    return _fill(value, item)


def artefacts(entries: list[dict], observations: dict) -> list[dict]:
    """Что запись создала: по одному наблюдаемому предмету на успешный вызов.

    Ненаблюдаемый факт — не повод построить карту молча: без предиката состояние станет ярлыком,
    поэтому недостающее объявление называется по имени и работа останавливается.
    """
    out = []
    for at, entry in enumerate(entries):
        if not entry.get("ok"):
            continue
        for fact in entry.get("facts") or []:
            rule = observations.get(fact)
            # Объявленная НЕнаблюдаемость — тоже решение: у такого факта нет адреса имени, и
            # предметом карты он не становится, но и «забытым» он больше не считается.
            if rule is None or not rule.get("identity"):
                continue
            name = _dig(entry, str(rule["identity"]))
            if name is None:
                raise SystemExit(f"шаг {at + 1} ({entry['tool']}): по адресу {rule['identity']} "
                                 f"имени нет — наблюдать нечего, поправь объявление наблюдения")
            # Список имён — это НЕСКОЛЬКО предметов из одного вызова, а не одно составное имя:
            # пачка иначе выродилась бы в первый элемент, и остальные остались бы непроверенными.
            for one in (name if isinstance(name, list) else [name]):
                out.append({"at": at, "fact": fact, "name": str(one), "rule": rule, "entry": entry})
            break
    return out


def depends(earlier: dict, later: dict) -> bool:
    """Зависит ли поздний предмет от раннего: имя раннего названо в аргументах позднего.

    Проверка КОНСЕРВАТИВНА: лишняя зависимость только сузит решётку, пропущенная породит порядок,
    невозможный на живом сервере, и обход упадёт не там, где интересно.
    """
    name = earlier["name"]
    for value in (later["entry"].get("args") or {}).values():
        if isinstance(value, str) and name in value and value != name:
            return True
    return name in str(later["entry"].get("with") or "")


def _check(items: list[dict], present: set[int]) -> list[dict]:
    """Предикат состояния: КАЖДЫЙ предмет спрашивается — и тот, что уже есть, и тот, которого нет.

    Спрашивать только созданное недостаточно: два состояния тогда различались бы длиной списка, а
    не наблюдением, и лишний предмет, появившийся не в свой черёд, остался бы невидимым.
    """
    steps = []
    for i, item in enumerate(items):
        rule = item["rule"]
        args = _fill_deep(rule.get("with") or {}, item)
        # Ожидание тоже подставляется: «в очереди стоит ЭТА строка» без имени предмета не
        # выражается, а один текст ожидания на все предметы проверял бы не тот из них.
        steps.append({"call": rule["observe"], "with": args,
                      "expect": _fill_deep(rule["present"] if i in present else rule["absent"], item)})
    return steps


def promote(entries: list[dict], name: str, why: str) -> str:
    """Последовательность → карта: состояния как ИДЕАЛЫ порядка, переходы как добавление предмета.

    Линейная карта повторяет записанный путь и нового не показывает. Решётка независимых вызовов
    даёт порядки, отсутствующие в записи, — их и проверяет обход.
    """
    observations = yaml.safe_load(OBSERVATIONS.read_text(encoding="utf-8")) or {}
    items = artefacts(entries, observations)
    if not items:
        seen = sorted({f for e in entries for f in (e.get("facts") or [])})
        raise SystemExit("в записи нет НАБЛЮДАЕМОГО предмета: встречены факты "
                         f"{', '.join(seen) or '(никаких)'} — объяви наблюдение в "
                         f"{OBSERVATIONS.relative_to(ROOT)} либо возьми другую запись")
    if len(items) > LATTICE_LIMIT:
        raise SystemExit(f"наблюдаемых предметов {len(items)} при пределе {LATTICE_LIMIT}: "
                         "полная решётка станет нечитаемой — сперва минимизируй (`--minimize`)")

    need = {i: {j for j in range(i) if depends(items[j], items[i])} for i in range(len(items))}
    states, ideals = {}, []
    for size in range(len(items) + 1):
        for combo in combinations(range(len(items)), size):
            present = set(combo)
            if all(need[i] <= present for i in present):
                ideals.append(present)
    for present in ideals:
        # Пустое состояние именуется словом, а не пустой склейкой индексов: иначе оно совпадает
        # по имени с состоянием, где есть предмет №0, и два разных мира становятся одним.
        states["s_" + ("_".join(str(i) for i in sorted(present)) or "empty")] = present

    lines = [f"map: {name}", f"why: {why}", "start: s_empty", "states:"]
    for state, present in states.items():
        have = ", ".join(items[i]["name"] for i in sorted(present)) or "ничего"
        lines.append(f"  {state}:")
        lines.append(f"    means: снаружи видно — {have}")
        if len(present) == len(items):
            lines.append("    terminal: true")
        lines.append("    check:")
        for step in _check(items, present):
            lines.append(f"      - call: {step['call']}")
            lines.append(f"        with: {json.dumps(step['with'], ensure_ascii=False)}")
            lines.append(f"        expect: {json.dumps(step['expect'], ensure_ascii=False)}")
    lines.append("transitions:")
    for state, present in states.items():
        for i, item in enumerate(items):
            if i in present or not need[i] <= present:
                continue
            target = next(k for k, v in states.items() if v == present | {i})
            lines.append(f"  - from: {state}")
            lines.append(f"    to: {target}")
            lines.append("    via:")
            lines.append(f"      - call: {item['entry']['tool']}")
            lines.append(f"        with: {json.dumps(item['entry'].get('args') or {}, ensure_ascii=False)}")
            lines.append(f"        expect: {json.dumps({'ok': True}, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"



def _reject_fiction(text: str) -> None:
    """Порождённую карту судит СВОЙ разбор харнесса, а не наш.

    Второй судья разошёлся бы с первым молча, и карта, принятая здесь, падала бы на обходе —
    то есть через минуты ожидания вместо секунды.
    """
    sys.path.insert(0, str(ROOT))
    from tests.harness.scenario import Vocabulary  # noqa: PLC0415
    from tests.harness.scenario_map import analyse, load_map  # noqa: PLC0415
    probe = ROOT / "tests" / "scenarios" / "_promote_probe.map.yaml"
    probe.write_text(text, encoding="utf-8")
    try:
        notes = analyse(load_map(probe, Vocabulary()))
    except Exception as exc:                       # разбор харнесса — вердикт, а не наша поломка
        raise SystemExit(f"порождённая карта не разбирается харнессом: {exc}") from None
    finally:
        probe.unlink(missing_ok=True)
    if notes:
        raise SystemExit("порождённая карта не проходит разбор харнесса:\n  " + "\n  ".join(notes))


def exemptions(entries: list[dict], unscripted: set[str], exempt: set[str]) -> tuple[list[str], list[str]]:
    """Вердикт по ПОСЛАБЛЕНИЯМ: что из объявленных исключений живой прогон опроверг.

    Послабление — не грех, а решение; проверяется оно не спором, а боем. Отсюда ровно два вопроса,
    решаемых по записи: код, объявленный непокрытым, ВЫСТРЕЛИЛ у клиента (значит случается в работе,
    и его никто не ждёт), и факт, объявленный ненаблюдаемым, не появился НИ РАЗУ (значит послабление
    не проверено ничем — ни наблюдателем, ни прогоном).

    Третий вопрос замерен и отвергнут: «в записи есть поле, которым наблюдают другие факты» дал 33
    пары на живом прогоне, и почти весь улов — `args.path`, который есть у чтения ровно так же, как
    у создания. Наличие поля не отличает созданное от прочитанного.
    """
    fired = {entry.get("code"): entry for entry in entries if entry.get("code")}
    seen = {fact for entry in entries for fact in (entry.get("facts") or [])}
    stale = [f"код `{code}` объявлен непокрытым сценарием, а в бою ВЫСТРЕЛИЛ "
             f"({fired[code].get('tool') or '?'}, сценарий {fired[code].get('scenario') or '?'}): "
             "послабление устарело — отказ случается у клиента, и никто его не ждёт"
             for code in sorted(unscripted & set(fired))]
    untested = [f"факт `{fact}` объявлен ненаблюдаемым и в записи не появился ни разу: "
                "послабление не проверено ничем — ни наблюдателем, ни прогоном"
                for fact in sorted(exempt - seen)]
    return stale, untested


def _exempt_facts(root: Path = ROOT) -> set[str]:
    """Факты, освобождённые от наблюдения объявлением (`observes: нечего`)."""
    path = root / "tests" / "harness" / "observations.yaml"
    if not path.exists():
        return set()
    declared = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {name for name, item in declared.items()
            if isinstance(item, dict) and item.get("observes") == "нечего"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--record", help="файл записи; по умолчанию самый свежий из следа и журнала")
    ap.add_argument("--tool", help="воспроизводить отказ этого инструмента")
    ap.add_argument("--code", help="воспроизводить отказ с этим кодом")
    ap.add_argument("--name", help="имя сценария; по умолчанию из инструмента и кода")
    ap.add_argument("--minimize", action="store_true",
                    help="выбрасывать шаги, пока отказ выживает (прогон на каждую попытку)")
    ap.add_argument("--promote", action="store_true",
                    help="карта вместо сценария: состояния из записи, переходы — решётка независимых вызовов")
    ap.add_argument("--write", help="дописать объявление в этот файл вместо вывода в stdout")
    ap.add_argument("--exemptions", action="store_true",
                    help="вердикт по послаблениям: что из объявленных исключений опроверг живой прогон")
    a = ap.parse_args()

    record = latest(a.record)
    entries = _entries(record)
    if not entries:
        raise SystemExit(f"{record}: ни одной записи о вызове")
    if a.exemptions:
        # Список непокрытых берётся у своего хозяина, а не считается здесь заново: два выражения
        # одного факта разошлись бы молча.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from invariants import declared_but_unscripted
        unscripted = {note.split("`")[1] for note in declared_but_unscripted() if "код отказа" in note}
        records = [record] if a.record else every_record()
        whole = [entry for path in records for entry in _entries(path)]
        stale, untested = exemptions(whole, unscripted, _exempt_facts())
        print(f"записей: {len(records)}, вызовов {len(whole)}")
        print(f"\n── ПОСЛАБЛЕНИЕ УСТАРЕЛО ({len(stale)}) ──")
        print("\n".join(f"  ✗ {note}" for note in stale) or "  (ничего)")
        print(f"\n── ПОСЛАБЛЕНИЕ НЕ ПРОВЕРЕНО БОЕМ ({len(untested)}) ──")
        print("\n".join(f"  ? {note}" for note in untested) or "  (ничего)")
        return 0

    at = pick(entries, a.tool, a.code)
    entry = entries[at]
    # Предмет отказа: инструмент, если он был; иначе конверт — метод протокола и уровень.
    subject = entry.get("tool") or f"{entry.get('level') or 'perimeter'}_{entry.get('rpc') or 'конверт'}"
    name = a.name or f"rep_{subject}_{entry.get('code') or 'refusal'}".lower().replace("/", "_")
    why = (f"отказ {entry.get('code')} на уровне {entry.get('level') or 'engine'} ({subject}) "
           f"уже случался — воспроизведён из записи {record.name}")

    print(f"запись: {record}\nотказ: {entry['tool']} → {entry.get('code')} (шаг {at + 1} из {len(entries)})",
          file=sys.stderr)

    if a.promote:
        text = promote(entries[: at + 1], a.name or f"rep_map_{entry['tool']}".lower(),
                       f"порядки, которых в записи не было, обязаны приводить в то же состояние; "
                       f"построено из {record.name}")
        _reject_fiction(text)
    else:
        steps = as_steps(entries[: at + 1])
        if a.minimize:
            before = len(steps)
            steps = minimize(steps, name, why)
            print(f"минимизация: {before} → {len(steps)} шагов", file=sys.stderr)
        text = render(name, why, steps)
    if a.write:
        target = Path(a.write)
        target.write_text((target.read_text(encoding="utf-8") + "\n" if target.exists() else "") + text,
                          encoding="utf-8")
        print(f"объявление дописано: {target}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
