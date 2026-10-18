"""scripts/release_age.py — возраст версий, которые правка вносит в замок студии, только stdlib.

Записи `studio/package-lock.json`, которых нет в замке базы с той же версией (`git show БАЗА:studio/package-lock.json`),
сверяются по времени публикации у реестра (`npm view ИМЯ@ВЕРСИЯ time --json`) со сроком возраста версии проекта —
`pyproject.toml [tool.uv.pip] exclude-newer` (Dependabot держит то же число). Отсчёт — от времени коммита HEAD, а не от
часов прогона: один коммит — один вердикт. Моложе срока или реестр время не назвал — отказ с перечнем.

Запуск: python3 scripts/release_age.py БАЗА — коммит, против которого правка (в CI: база PR или прежняя вершина main).
Базы нет в истории (пусто, нули, коммит не загружен) — судятся все записи замка.
"""
import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = "studio/package-lock.json"
_DAYS = re.compile(r"(\d+) days?|P(\d+)D")


def window(pyproject: Path) -> timedelta:
    """Срок возраста версии проекта — `[tool.uv.pip] exclude-newer`; нет ключа или срок не в днях — отказ с формой."""
    try:
        value = str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["uv"]["pip"]["exclude-newer"])
    except KeyError:
        raise SystemExit(f"{pyproject.name}: нет [tool.uv.pip] exclude-newer — срок возраста версии не объявлен") from None
    found = _DAYS.fullmatch(value.strip())
    if not found:
        raise SystemExit(f"{pyproject.name} [tool.uv.pip] exclude-newer = {value!r}: гейт судит срок в днях — "
                         "«3 days» или «P3D»")
    return timedelta(days=int(found.group(1) or found.group(2)))


def entries(lock: dict) -> dict[tuple[str, str], str]:
    """Записи замка: (имя в реестре, версия) → откуда взята. Корень и ссылки на свои каталоги — не версии реестра;
    вшитая в tarball родителя (`inBundle`) приходит с ним — её возраст судится возрастом родителя."""
    found = {}
    for key, entry in (lock.get("packages") or {}).items():
        if not key or entry.get("link") or entry.get("inBundle") or "version" not in entry:
            continue
        found[(entry.get("name") or key.rsplit("node_modules/", 1)[-1], entry["version"])] = str(entry.get("resolved", ""))
    return found


def added(head: dict, base: dict | None) -> dict[tuple[str, str], str]:
    """Что правка вносит: записи головы, которых нет в базе с той же версией; базы нет — все записи."""
    known = entries(base) if base is not None else {}
    return {pair: source for pair, source in entries(head).items() if pair not in known}


def published(name: str, version: str, studio: Path) -> datetime:
    """Время публикации версии у реестра — ответом `npm view`; нет ответа или версии — LookupError с причиной."""
    npm = shutil.which("npm")
    if npm is None:
        raise LookupError("npm не найден — время публикации спросить не у кого")
    done = subprocess.run([npm, "view", f"{name}@{version}", "time", "--json"], capture_output=True, text=True,
                          encoding="utf-8", cwd=studio, check=False)
    if done.returncode:
        said = done.stderr.strip().splitlines()
        raise LookupError(said[-1] if said else f"npm view: код {done.returncode}")
    stamp = json.loads(done.stdout).get(version)
    if not stamp:
        raise LookupError("реестр не назвал время этой версии")
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def young(news: dict[tuple[str, str], str], at: datetime, span: timedelta,
          when: Callable[[str, str], datetime]) -> tuple[list[str], list[str], list[str]]:
    """Суд записей на момент `at`: моложе срока, время не названо (после повтора — сбой сети не вина версии) и не из
    реестра (git, file — времени публикации у них нет; называются, но не судятся)."""
    fresh, unknown, foreign = [], [], []
    for (name, version), source in sorted(news.items()):
        if not source.startswith(("https://", "http://")):
            foreign.append(f"{name}@{version} ({source or 'источник не записан'})")
            continue
        try:
            try:
                stamp = when(name, version)
            except LookupError:
                stamp = when(name, version)
        except LookupError as e:
            unknown.append(f"{name}@{version}: {e}")
            continue
        if at - stamp < span:
            fresh.append(f"{name}@{version}: опубликована {stamp:%Y-%m-%d %H:%M} UTC, на момент коммита ей {at - stamp}")
    return fresh, unknown, foreign


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", cwd=root, check=False)


def main(argv: list[str], root: Path = ROOT, when: Callable[[str, str], datetime] | None = None) -> int:
    if len(argv) != 1:
        raise SystemExit("Запуск: python3 scripts/release_age.py БАЗА")
    ref = argv[0].strip()
    shown = _git(root, "show", f"{ref}:{LOCK}") if ref.strip("0") else None
    base = json.loads(shown.stdout) if shown is not None and shown.returncode == 0 else None
    at = datetime.fromisoformat(_git(root, "log", "-1", "--format=%cI", "HEAD").stdout.strip())
    span = window(root / "pyproject.toml")
    news = added(json.loads((root / LOCK).read_text(encoding="utf-8")), base)
    print(f"возраст версий замка студии: срок {span.days} дн. на момент коммита HEAD {at.isoformat()}; "
          + (f"база {ref}" if base is not None else f"базы нет в истории ({ref or 'пусто'}) — судятся все записи")
          + f"; новых записей {len(news)}")
    fresh, unknown, foreign = young(news, at, span, when or (lambda name, version: published(name, version,
                                                                                         root / "studio")))
    for line in foreign:
        print(f"  не судится, не из реестра: {line}")
    for line in fresh:
        print(f"  ✗ моложе срока: {line}")
    for line in unknown:
        print(f"  ✗ время не названо: {line}")
    if fresh or unknown:
        print(f"Отказ: {len(fresh)} моложе срока, {len(unknown)} без времени. Возьми версию старше {span.days} дн. "
              "(npm view ИМЯ time) или дождись срока.")
        return 1
    print("  ✓ все новые записи старше срока")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
