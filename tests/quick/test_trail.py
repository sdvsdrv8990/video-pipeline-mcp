"""
tests/quick/test_trail.py — след вызовов сервера: удержание, предел, отказ записи.

Standalone-прогон:  python tests/quick/test_trail.py
Поведение «отказ попал в след» и «секрет не попал» проверяют сценарии `observability.yaml` по
живому серверу. Здесь — то, что сценарием не выразить: прополка старых файлов, предел на файл,
неполное объявление и сломанная запись, которая обязана потерять НАБЛЮДЕНИЕ, а не вызов.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.observability import Trail  # noqa: E402

_checks = 0
_fails: list[str] = []


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


class _Result:
    """Ответ инструмента в том виде, в каком его видит след: без Pydantic, чтобы мерить писатель."""

    def __init__(self, status="success", data=None, error=None, facts=()):
        self.status, self.data, self.error, self.facts = status, data, error, list(facts)


def _trail(tmp: Path, **kw) -> Trail:
    return Trail(tmp, keep_files=kw.pop("keep_files", 3), max_bytes=kw.pop("max_bytes", 10_000),
                 record_args=kw.pop("record_args", True))


print("== запись и её форма ==")
tmp = Path(tempfile.mkdtemp(prefix="vpm-trail-"))
t = _trail(tmp)
t.write("fs_read_file", {"path": "a.txt", "token": "sk-1"}, _Result(status="success", data={"content": "x"}))
rows = list(tmp.glob("trail-*.jsonl"))
ok(len(rows) == 1, "первая запись создала файл прогона")
import json  # noqa: E402
entry = json.loads(rows[0].read_text(encoding="utf-8").splitlines()[0])
ok(entry["tool"] == "fs_read_file" and entry["ok"] is True, "инструмент и исход записаны")
ok(entry["args"]["token"] == "***REDACTED***", "секрет в аргументах маскируется тем же санитизатором")
ok(entry["args"]["path"] == "a.txt", "остальные аргументы сохраняются — иначе воспроизводить нечем")
ok(entry["step"] == 1, "порядок вызова записан: без него последовательность не восстановить")

print("\n== удержание и предел ==")
keep = Path(tempfile.mkdtemp(prefix="vpm-trail-keep-"))
for n in range(5):
    stale = keep / f"trail-2026010{n}-000000.jsonl"
    stale.write_text("{}\n", encoding="utf-8")
t2 = _trail(keep, keep_files=3)
t2.write("tools/list", {}, _Result())
ok(len(list(keep.glob("trail-*.jsonl"))) == 3, "старые файлы прополоты до объявленного числа")

capped = Path(tempfile.mkdtemp(prefix="vpm-trail-cap-"))
t3 = _trail(capped, max_bytes=1)
t3.write("first", {}, _Result())
t3.write("second", {}, _Result())
written = (capped / f"trail-{t3.run}.jsonl").read_text(encoding="utf-8").splitlines()
ok(len(written) == 1, "после предела запись прекращается, а не растёт бесконечно")

print("\n== выключенный и сломанный ==")
off = Trail(None)
off.write("fs_read_file", {}, _Result())
ok(not off.enabled, "без каталога след выключен и ничего не пишет")
ok(not Trail.from_declaration({"trail": {"enabled": True, "dir": "x"}}, ROOT).enabled,
   "неполное объявление гасит след, а не дописывается значением из кода")
ok(not Trail.from_declaration({}, ROOT).enabled, "нет объявления — нет следа")

broken = _trail(Path("/proc/нельзя-сюда-писать"))
broken.write("fs_read_file", {"path": "a"}, _Result())
ok(not broken.enabled, "отказ записи гасит след")
broken.write("fs_read_file", {"path": "b"}, _Result())
ok(True, "второй вызов после отказа НЕ падает: ломается наблюдение, а не работа владельца")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
