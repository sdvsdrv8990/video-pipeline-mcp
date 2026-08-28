"""
tests/quick/test_trail.py — след вызовов сервера: удержание, предел, отказ записи.

Здесь же синтезатор `scripts/guards/reproduce.py` — он читает ту же запись, и разъехавшийся
формат ломает обе половины сразу; проверять их порознь значит не заметить расхождения.

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

sys.path.insert(0, str(ROOT / "scripts" / "guards"))
from reproduce import _entries, _fill, artefacts, as_steps, depends, pick, promote, render  # noqa: E402

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


print("\n== синтез: запись → объявление ==")
import yaml as _yaml  # noqa: E402

REC = Path(tempfile.mkdtemp(prefix="vpm-rep-")) / "trail-x.jsonl"
REC.write_text("\n".join([
    json.dumps({"scenario": "__run__", "ok": True, "total": 1}),
    "не-json",
    json.dumps({"tool": "fs_create_file", "args": {"path": "a.txt"}, "ok": True, "facts": ["FileCreated"]}),
    json.dumps({"tool": "fs_read_file", "args": {"path": "нет.txt"}, "ok": False, "code": "FILE_NOT_FOUND"}),
]) + "\n", encoding="utf-8")

rows = _entries(REC)
ok(len(rows) == 2, "итог прогона и битая строка в шаги не попадают — воспроизводят вызовы, а не сводку")
ok(pick(rows, None, None) == 1, "берётся ПОСЛЕДНИЙ отказ: свежий интереснее старого")
ok(pick(rows, "fs_read_file", "FILE_NOT_FOUND") == 1, "отбор по инструменту и коду находит его же")
try:
    pick(rows, "fs_read_file", "ЧУЖОЙ_КОД")
    ok(False, "отказа под условия нет — обязан быть громкий отказ, а не пустой сценарий")
except SystemExit:
    ok(True, "отказа под условия нет — синтезатор говорит это, а не рожает пустышку")

steps = as_steps(rows)
ok(steps[0]["expect"] == {"ok": True, "facts": ["FileCreated"]}, "успех проверяется фактами")
ok(steps[1]["expect"] == {"ok": False, "code": "FILE_NOT_FOUND"},
   "отказ проверяется кодом — «просто упало» харнесс не принимает")

text = render("rep_проба", "почему", steps)
parsed = _yaml.safe_load(text)
ok(isinstance(parsed, list) and parsed[0]["scenario"] == "rep_проба",
   "порождённое объявление разбирается как YAML, а не только выглядит им")
ok(parsed[0]["when"][1]["call"] == "fs_read_file"
   and parsed[0]["when"][1]["expect"]["code"] == "FILE_NOT_FOUND",
   "шаг отказа доезжает до объявления вместе со своим кодом")
ok(parsed[0]["when"][0]["with"] == {"path": "a.txt"}, "аргументы доезжают — без них воспроизводить нечем")


print("\n== уровень решает форму шага ==")
PERIM = {"level": "perimeter", "tool": "", "rpc": "", "ok": False, "code": "RPC_-32002",
         "args": {"Host": "evil.example", "Content-Type": "application/json", "X-Api-Key": "***REDACTED***"}}
IDENT = {"level": "identity", "tool": "", "rpc": "tools/list", "ok": False, "code": "RPC_-32001",
         "args": {"Host": "127.0.0.1", "Authorization": "***REDACTED***"}}

perim_step = as_steps([PERIM])[0]
ok("call" not in perim_step and perim_step["rpc"] == "tools/list",
   "до диспетчера инструмента НЕТ — шаг конвертный, а не вызов")
ok(perim_step["headers"] == {"Host": "evil.example", "Content-Type": "application/json"},
   "воспроизводят заголовки, которыми отказ вызван")
ok("X-Api-Key" not in perim_step["headers"],
   "замаскированный ключ в шаг не переносится — подставлять маску вместо ключа значит врать")

ident_step = as_steps([IDENT])[0]
ok(ident_step.get("token") == "" and ident_step["rpc"] == "tools/list",
   "ключа в записи нет по построению — отказ воспроизводится ОТСУТСТВИЕМ ключа, а не выдумкой")

REC2 = Path(tempfile.mkdtemp(prefix="vpm-lvl-")) / "trail-y.jsonl"
REC2.write_text(json.dumps(PERIM) + "\n", encoding="utf-8")
ok(len(_entries(REC2)) == 1,
   "строка без имени инструмента доезжает до отбора — ради неё вторая точка записи и ставилась")

text = render("rep_периметр", "почему", [perim_step])
back = _yaml.safe_load(text)[0]["when"][0]
ok(back["rpc"] == "tools/list" and back["headers"]["Host"] == "evil.example",
   "конвертный шаг разбирается как YAML вместе с заголовками")

SOCK = {"level": "socket", "tool": "", "rpc": "", "ok": False, "code": "SOCKET_HELD",
        "args": {"held": 12, "threshold": 8}}
ok(pick([SOCK, PERIM], None, None) == 1,
   "удержание соединений пропускается при выборе отказа — запросом его не повторить")
try:
    as_steps([SOCK])
    ok(False, "шаг из наблюдения нижнего слоя строиться не должен")
except SystemExit as exc:
    ok("уровнем выше" in str(exc),
       "отказ строить шаг называет причину: улика нижнего слоя не воспроизводится запросом")

print("\n== повышение до карты ==")
OBS = _yaml.safe_load((ROOT / "tests" / "harness" / "observations.yaml").read_text(encoding="utf-8"))


def _made(path, tool="fs_create_file"):
    return {"tool": tool, "args": {"path": path, "content": "x"}, "ok": True, "facts": ["FileCreated"]}


pair = [_made("реш/альфа.txt"), _made("реш/бета.txt")]
found = artefacts(pair, OBS)
ok([a["name"] for a in found] == ["реш/альфа.txt", "реш/бета.txt"],
   "предмет наблюдения берётся по объявленному адресу, а не угадывается из имени инструмента")
ok(not artefacts([{"tool": "fs_read_file", "args": {}, "ok": False, "code": "FILE_NOT_FOUND"}], OBS),
   "отказ предметом не становится: наблюдать нечего")
ok(not artefacts([{"tool": "media_generate", "args": {}, "ok": True, "facts": ["MediaGenerated"]}], OBS),
   "необъявленный факт пропускается — карту на нём не построишь")

lattice = _yaml.safe_load(promote(pair, "m_проба", "почему"))
ok(sorted(lattice["states"]) == ["s_0", "s_0_1", "s_1", "s_empty"],
   "два независимых предмета дают решётку из четырёх состояний, а не линию из трёх")
ok(any(t["from"] == "s_empty" and t["to"] == "s_1" for t in lattice["transitions"]),
   "в решётке есть порядок, которого в записи НЕ БЫЛО — ради него всё и строится")
ok(lattice["states"]["s_empty"]["check"][0]["expect"]["ok"] is False
   and lattice["states"]["s_0"]["check"][0]["expect"]["ok"] is True,
   "предикат спрашивает КАЖДЫЙ предмет — и тот, что есть, и тот, которого ещё нет")

print("\n== составное имя предмета ==")
_sheet = {"tool": "excel_add_sheet", "args": {"path": "кн.xlsx", "sheet": "Л"},
          "data": {"path": "кн.xlsx", "sheet": "Л"}, "ok": True, "facts": ["SheetAdded"]}
_item = artefacts([_sheet], OBS)[0]
ok(_item["name"] == "Л", "предмет — имя листа, а не книга: у листа своё существование")
ok(_fill("${args.path}", _item) == "кн.xlsx",
   "второй адрес берётся из ЗАПИСИ вызова — иначе лист в паре «книга + имя» не спросить")
ok(_fill("${identity}", _item) == "Л" and _fill("META", _item) == "META",
   "имя предмета и литерал различаются: подстановка только по объявленному адресу")
try:
    _fill("${args.нетключа}", _item)
    ok(False, "адрес мимо записи обязан назвать себя, а не подставить пустоту")
except SystemExit as exc:
    ok("нетключа" in str(exc), "отказ называет НЕДОСТАЮЩИЙ адрес, а не «что-то не так»")
_lat = _yaml.safe_load(promote([_sheet], "m_лист", "почему"))
ok(_lat["states"]["s_0"]["check"][0]["with"] == {"path": "кн.xlsx", "sheet": "Л"},
   "в карту уехали ОБА значения — составное имя доехало до предиката")

print("\n== объявленная ненаблюдаемость ==")
ok(not artefacts([{"tool": "table_append", "args": {"table": "т", "sheet": "л"}, "ok": True,
                   "facts": ["RowAppended"]}], OBS),
   "факт, объявленный ненаблюдаемым, предметом карты не становится — но и забытым не считается")

chain = [_made("дом/файл.txt"),
         {"tool": "fs_create_file", "args": {"path": "дом/файл.txt/вложенный.txt", "content": "y"},
          "ok": True, "facts": ["FileCreated"]}]
ok(depends(artefacts(chain, OBS)[0], artefacts(chain, OBS)[1]),
   "имя раннего названо в аргументах позднего — это зависимость, переставлять нельзя")
linear = _yaml.safe_load(promote(chain, "m_цепь", "почему"))
ok(sorted(linear["states"]) == ["s_0", "s_0_1", "s_empty"],
   "зависимые вызовы не порождают невозможный порядок: решётка сужается до цепи")

try:
    promote([{"tool": "media_generate", "args": {}, "ok": True, "facts": ["MediaGenerated"]}], "m", "п")
    ok(False, "без наблюдаемого предмета карта строиться не должна")
except SystemExit as exc:
    ok("MediaGenerated" in str(exc),
       "недостающее наблюдение называется ПО ИМЕНИ — иначе неполная карта сойдёт за полную")

try:
    promote([_made(f"много/{n}.txt") for n in range(5)], "m", "п")
    ok(False, "за пределом решётка обязана отказать, а не родить нечитаемое")
except SystemExit as exc:
    ok("минимизируй" in str(exc), "отказ за пределом называет, что делать дальше")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
