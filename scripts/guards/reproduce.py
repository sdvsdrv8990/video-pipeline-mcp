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
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (ROOT / "logs" / "trail", ROOT / "tests" / ".journal")
RUNNER = ROOT / "tests" / "scenarios" / "test_scenarios.py"


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
        if entry.get("scenario") != "__run__" and entry.get("tool"):
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


def pick(entries: list[dict], tool: str | None, code: str | None) -> int:
    """Индекс ОТКАЗА, который воспроизводим. Последний подходящий: свежий интереснее старого."""
    for i in range(len(entries) - 1, -1, -1):
        entry = entries[i]
        if entry.get("ok"):
            continue
        if tool and entry.get("tool") != tool:
            continue
        if code and entry.get("code") != code:
            continue
        return i
    raise SystemExit("в записи нет отказа под эти условия — воспроизводить нечего")


def as_steps(entries: list[dict]) -> list[dict]:
    """Записи → шаги объявления. Успех проверяется фактами, отказ — кодом: «просто упало» не ожидание."""
    steps = []
    for entry in entries:
        expect: dict = {"ok": bool(entry.get("ok"))}
        if entry.get("ok"):
            if entry.get("facts"):
                expect["facts"] = list(entry["facts"])
        else:
            expect["code"] = entry.get("code") or "UNKNOWN_ERROR"
        steps.append({"call": entry["tool"], "with": entry.get("args") or {}, "expect": expect})
    return steps


def render(name: str, why: str, steps: list[dict]) -> str:
    """Объявление в стиле проекта. Пишем руками, а не yaml.dump: тот ломает порядок и кавычит всё."""
    lines = [f"- scenario: {name}", f"  why: {why}", "  when:"]
    for step in steps:
        lines.append(f"    - call: {step['call']}")
        lines.append("      with:")
        for key, value in (step["with"] or {}).items():
            lines.append(f"        {key}: {json.dumps(value, ensure_ascii=False)}")
        if not step["with"]:
            lines[-1] = "      with: {}"
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--record", help="файл записи; по умолчанию самый свежий из следа и журнала")
    ap.add_argument("--tool", help="воспроизводить отказ этого инструмента")
    ap.add_argument("--code", help="воспроизводить отказ с этим кодом")
    ap.add_argument("--name", help="имя сценария; по умолчанию из инструмента и кода")
    ap.add_argument("--minimize", action="store_true",
                    help="выбрасывать шаги, пока отказ выживает (прогон на каждую попытку)")
    ap.add_argument("--write", help="дописать объявление в этот файл вместо вывода в stdout")
    a = ap.parse_args()

    record = latest(a.record)
    entries = _entries(record)
    if not entries:
        raise SystemExit(f"{record}: ни одной записи о вызове")
    at = pick(entries, a.tool, a.code)
    entry = entries[at]
    name = a.name or f"rep_{entry['tool']}_{(entry.get('code') or 'refusal')}".lower()
    why = (f"отказ {entry.get('code')} на {entry['tool']} уже случался — "
           f"воспроизведён из записи {record.name}")

    steps = as_steps(entries[: at + 1])
    print(f"запись: {record}\nотказ: {entry['tool']} → {entry.get('code')} (шаг {at + 1} из {len(entries)})",
          file=sys.stderr)
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
