"""Гейт «слом публичной поверхности»: griffe между ревизиями, сверенный с объявленными сломами коммита.

Три исхода, а не падение:
  0 — изменений нет или каждое объявлено и случилось: перечень объявленного печатается;
  1 — слом не объявлен или объявленное не случилось: что именно и какой строкой объявить;
  2 — сравнить нельзя (нет ревизии сравнения, griffe отказал сам): причина.
Объявление — строки трейлера сообщения коммита `API-Break: <путь>: <объект>: <род изменения>` (номер строки не входит:
он сдвигается). Источник объявлений — сообщения коммитов base..head или файл (`--declared-from`, для гейта до коммита).

Запуск: python scripts/guards/api_breaks.py core tools [--base HEAD~1] [--head HEAD] [--declared-from FILE]
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import PurePath

TRAILER = "API-Break"
WORKTREE = "WORKTREE"  # --head: рабочее дерево вместо ревизии — строки трейлера до коммита
# griffe check (griffecli 2.3.0, _internal/cli.py:161-165): сломы — строками в stderr и код 1; ошибка git — 2; сбой
# расширений — тоже 1, но без строк слома. Поэтому слом — разобранные строки stderr, а не код.
_LINE = re.compile(r"^(?P<path>[^:\s]+\.py):\d+: (?P<obj>[^:]+): (?P<msg>.+)$")


class Unmeasured(Exception):
    """Сравнить нельзя: причина — в тексте."""


def _git(*args: str) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise Unmeasured(f"git {' '.join(args)}: {p.stderr.strip() or p.returncode}")
    return p.stdout


def found(packages: list[str], base: str, head: str) -> dict[str, str]:
    """Слом griffe: ключ объявления → строка griffe."""
    out: dict[str, str] = {}
    against = [] if head == WORKTREE else ["-b", head]  # без -b griffe сравнивает с рабочим деревом
    for pkg in packages:
        # Тем же интерпретатором: griffe — пакет окружения, а не программа в PATH; нет пакета — код 1 без строк слома.
        p = subprocess.run([sys.executable, "-m", "griffe", "check", pkg, "-s", ".", "-a", base, *against,
                            "-f", "oneline"],
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ, "NO_COLOR": "1"})
        lines = [m for m in map(_LINE.match, p.stderr.splitlines()) if m]
        if p.returncode != 0 and not lines:
            raise Unmeasured(f"griffe check {pkg} отказал сам (код {p.returncode}): {p.stderr.strip()[-400:]}")
        for m in lines:
            # griffe печатает путь ОС (на Windows — «\»), трейлер и git — всегда «/».
            path = PurePath(m["path"]).as_posix()
            out[f"{path}: {m['obj']}: {m['msg'].split(': ', 1)[0]}"] = m.group(0)
    return out


def declared(base: str, head: str, source: str | None) -> set[str]:
    """Объявления — из сообщений всех коммитов `base..head`, а не одного HEAD: у pull request CI проверяет слитый коммит,
    чьё сообщение трейлеров ветки не несёт, а дифф griffe и так считается по всему диапазону."""
    if not source and head == WORKTREE:
        return set()  # у рабочего дерева сообщения ещё нет — исход 1 печатает строки трейлера к объявлению
    text = open(source, encoding="utf-8").read() if source else _git("log", "--format=%B", f"{base}..{head}")
    return {m.group(1).strip() for m in re.finditer(rf"^{TRAILER}:\s*(.+)$", text, re.M)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("packages", nargs="+", help="пакеты публичной поверхности (core tools)")
    ap.add_argument("--base", default="HEAD~1", help="ревизия сравнения (по умолчанию HEAD~1)")
    ap.add_argument("--head", default="HEAD",
                    help=f"проверяемая ревизия (по умолчанию HEAD); {WORKTREE} — рабочее дерево, до коммита")
    ap.add_argument("--declared-from", help="файл с объявлениями вместо сообщений коммитов base..head")
    a = ap.parse_args()
    try:
        _git("rev-parse", "--verify", f"{a.base}^{{commit}}")
        breaks, said = found(a.packages, a.base, a.head), declared(a.base, a.head, a.declared_from)
    except Unmeasured as e:
        print(f"слом публичной поверхности: СРАВНИТЬ НЕЛЬЗЯ — {e}")
        return 2
    undeclared, unrealized = sorted(set(breaks) - said), sorted(said - set(breaks))
    if undeclared or unrealized:
        if undeclared:
            print(f"слом публичной поверхности: НЕ ОБЪЯВЛЕН ({len(undeclared)}) — {a.base}..{a.head}:")
            for k in undeclared:
                print(f"  - {breaks[k][:200]}")
            print("  если изменение намеренное — добавь в сообщение коммита строки:")
            for k in undeclared:
                print(f"    {TRAILER}: {k}")
        if unrealized:
            print(f"объявлено, но не случилось ({len(unrealized)}) — убери строку или проверь правку:")
            for k in unrealized:
                print(f"  - {TRAILER}: {k}")
        return 1
    print(f"слом публичной поверхности: {'нет изменений' if not breaks else f'объявленные изменения ({len(breaks)})'}"
          f" — {a.base}..{a.head}")
    for k in sorted(breaks):
        print(f"  - {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
