#!/usr/bin/env python3
"""tests/quick/test_release_age.py — гейт возраста версий замка студии (`scripts/release_age.py`).

Истина о версии — время публикации, которое подставляет набор (реестр-двойник), и время коммита временного репозитория;
дверь — код выхода и перечень гейта, как их видит шаг CI. Сеть не нужна.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.release_age import LOCK, main, window

_checks = 0
_fails: list[str] = []
AT = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)      # время коммита головы во временном репозитории
BASE_LOCK = json.loads((ROOT / LOCK).read_text(encoding="utf-8"))
NEW = BASE_LOCK["packages"]["node_modules/lucide-react"]["version"] + "-probe"    # версия, которой в базе нет


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def _commit(repo: Path, message: str, at: datetime) -> None:
    stamp = at.isoformat()
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    for args in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


def repo_with(head_change, pyproject: str | None = None) -> Path:
    """Временный репозиторий: база — замок дерева, голова — его правка `head_change(lock)`, коммит головы — в AT."""
    repo = Path(tempfile.mkdtemp(prefix="release_age_"))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "studio").mkdir()
    (repo / "pyproject.toml").write_text(pyproject or (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
                                         encoding="utf-8")
    (repo / LOCK).write_text(json.dumps(BASE_LOCK, indent=2), encoding="utf-8")
    _commit(repo, "база", AT - timedelta(days=1))
    lock = json.loads(json.dumps(BASE_LOCK))
    head_change(lock)
    (repo / LOCK).write_text(json.dumps(lock, indent=2), encoding="utf-8")
    _commit(repo, "голова", AT)
    return repo


def lucide(version: str, resolved: str | None = None):
    def change(lock: dict) -> None:
        entry = lock["packages"]["node_modules/lucide-react"]
        entry["version"] = version
        entry["resolved"] = resolved or f"https://registry.npmjs.org/lucide-react/-/lucide-react-{version}.tgz"
    return change


def gate(repo: Path, base: str, when) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main([base], root=repo, when=when)
    return code, out.getvalue()


def aged(days: float):
    """Реестр-двойник: любая версия опубликована за `days` дней до коммита головы."""
    return lambda name, version: AT - timedelta(days=days)


print("== 1. Срок — одно правило: pyproject [tool.uv.pip] и Dependabot держат то же число ==")
span = window(ROOT / "pyproject.toml")
plan = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
held = {u["package-ecosystem"]: (u.get("cooldown") or {}).get("default-days") for u in plan["updates"]}
check("срок гейта — из pyproject, число Dependabot у каждой экосистемы то же",
      span.days > 0 and set(held.values()) == {span.days}, (span, held))
for case, text, want in (("срок не в днях", '[tool.uv.pip]\nexclude-newer = "3 hours"\n', "«3 days» или «P3D»"),
                         ("срок не объявлен", "[tool.uv]\n", "не объявлен")):
    spoiled = Path(tempfile.mkdtemp(prefix="release_age_pp_")) / "pyproject.toml"
    spoiled.write_text(text, encoding="utf-8")
    try:
        window(spoiled)
        said = "принят"
    except SystemExit as e:
        said = str(e)
    check(f"{case} — отказ с формой, а не тихое умолчание", want in said, said)

print("== 2. Дверь гейта: код выхода и перечень по возрасту новых записей ==")
repo = repo_with(lucide(NEW))
code, said = gate(repo, "HEAD~1", aged(4))
check("законный: новая запись старше срока — код 0", code == 0 and "новых записей 1" in said, said)
code, said = gate(repo, "HEAD~1", aged(1))
check("больной: запись моложе срока — код 1, запись названа с датой",
      code == 1 and f"lucide-react@{NEW}" in said and "моложе срока" in said, said)
code, said = gate(repo, "HEAD~1", aged(span.days))
check("граница: ровно срок — уже не моложе, код 0", code == 0, said)


def silent(name, version):
    raise LookupError("No match found for version")


code, said = gate(repo, "HEAD~1", silent)
check("реестр не назвал время — код 1 с причиной, а не пропуск", code == 1 and "время не названо" in said, said)
asked: list[str] = []


def flaky(name, version):
    asked.append(version)
    if len(asked) == 1:
        raise LookupError("ECONNRESET")
    return AT - timedelta(days=10)


code, said = gate(repo, "HEAD~1", flaky)
check("сбой сети один раз — повтор, а не вина версии (PR-06)", code == 0 and len(asked) == 2, (said, asked))
code, said = gate(repo, "HEAD", aged(10))
check("правки замка нет — новых записей 0, код 0", code == 0 and "новых записей 0" in said, said)
every: list[str] = []
code, said = gate(repo, "0" * 40, lambda name, version: every.append(name) or AT - timedelta(days=10))
check("базы нет в истории — судятся все записи замка: реестр спрошен о каждой объявленной",
      code == 0 and "судятся все записи" in said and f"новых записей {len(every)}" in said
      and len(every) > len(BASE_LOCK["packages"][""].get("devDependencies", {})), said.splitlines()[0])
foreign = repo_with(lucide(NEW, "git+ssh://git@example.org/lucide.git#abc"))
code, said = gate(foreign, "HEAD~1", silent)
check("запись не из реестра называется и не судится — времени публикации у неё нет",
      code == 0 and f"не судится, не из реестра: lucide-react@{NEW}" in said, said)

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
