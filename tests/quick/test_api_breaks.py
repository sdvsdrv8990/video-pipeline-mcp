#!/usr/bin/env python3
"""tests/quick/test_api_breaks.py — гейт «слом публичной поверхности» (`scripts/guards/api_breaks.py`) мерится на двери:
код выхода и названная причина при поданных строках griffe и трейлерах коммита.

Истина — трейлер в форме git (`/` на любой ОС), как его пишет сообщение коммита; строки griffe — как он печатает их на
своей ОС (форма Windows — из лога CI 38483575642, ячейка lint windows). ОС эмулируется видом пути pathlib при загрузке
гейта, а не подменой его внутренностей. Каждый род — парой; разрушительный — griffe отказал сам. Последний случай
судит сам набор: мутант без перевода пути обязан быть пойман случаем Windows.
"""
import contextlib
import io
import pathlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "guards" / "api_breaks.py"

_checks = 0
_fails: list[str] = []

WIN = r"core\runner\supervisor.py:120: RunnerSupervisor.start: Public object was removed"
POSIX = "core/runner/supervisor.py:120: RunnerSupervisor.start: Public object was removed"
TRAILER = "API-Break: core/runner/supervisor.py: RunnerSupervisor.start: Public object was removed"

# имя · путь ОС · строки griffe · код griffe · сообщение коммита · ожидаемый код · что обязано быть в выводе
CASES = [
    ("Windows: слом объявлен трейлером git", pathlib.PureWindowsPath, [WIN], 1, TRAILER, 0, "объявленные изменения (1)"),
    ("POSIX: слом объявлен трейлером git", pathlib.PurePosixPath, [POSIX], 1, TRAILER, 0, "объявленные изменения (1)"),
    ("Windows: слом не объявлен — строка к объявлению в форме git", pathlib.PureWindowsPath, [WIN], 1, "", 1, TRAILER),
    ("POSIX: объявлено, но не случилось", pathlib.PurePosixPath, [], 0, TRAILER, 1, "объявлено, но не случилось"),
    ("griffe отказал сам — сравнить нельзя, причина названа", pathlib.PurePosixPath,
     ["No module named griffe"], 1, TRAILER, 2, "СРАВНИТЬ НЕЛЬЗЯ"),
]


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def loaded(flavour, source: str) -> types.ModuleType:
    """Гейт, загруженный как на ОС с этим видом пути: всё, что он берёт из pathlib, — того же вида."""
    module, real = types.ModuleType("api_breaks_on_os"), pathlib.PurePath
    pathlib.PurePath = flavour
    try:
        exec(compile(source, str(GATE), "exec"), module.__dict__)
    finally:
        pathlib.PurePath = real
    return module


def door(module, griffe_lines, griffe_rc, message) -> tuple[int, str]:
    """main() гейта с подставными git и griffe: код выхода и вывод — то, что видит CI."""
    def run(argv, **_):
        if argv[:2] == ["git", "log"]:
            return types.SimpleNamespace(returncode=0, stdout=message, stderr="")
        if argv[0] == "git":
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(returncode=griffe_rc, stdout="", stderr="\n".join(griffe_lines))
    saved = module.subprocess, sys.argv
    module.subprocess, sys.argv = types.SimpleNamespace(run=run), ["api_breaks.py", "core"]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = module.main()
    finally:
        module.subprocess, sys.argv = saved
    return rc, out.getvalue()


SRC = GATE.read_text(encoding="utf-8")

print("== гейт API-Break: исход на двери ==")
for name, flavour, lines, grc, msg, want_rc, want_text in CASES:
    rc, text = door(loaded(flavour, SRC), lines, grc, msg)
    check(f"{name}: код {want_rc}", rc == want_rc, f"код {rc}")
    check(f"{name}: в выводе «{want_text[:40]}»", want_text in text, text[-300:])
    check(f"{name}: без трассировки", "Traceback" not in text)

print("== суд над набором: мутант без перевода пути ==")
ANCHOR = 'path = PurePath(m["path"]).as_posix()'
check("якорь мутанта ложится ровно раз", SRC.count(ANCHOR) == 1, f"раз: {SRC.count(ANCHOR)}")
rc, _ = door(loaded(pathlib.PureWindowsPath, SRC.replace(ANCHOR, 'path = m["path"]')), [WIN], 1, TRAILER)
check("мутант пойман случаем Windows (код не 0)", rc != 0, f"код {rc}")

print(f"\n{'=' * 50}\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
