"""
tests/quick/test_trail.py — след вызовов сервера: удержание, предел, отказ записи.

Здесь же синтезатор `scripts/guards/reproduce.py` — он читает ту же запись, и разъехавшийся
формат ломает обе половины сразу; проверять их порознь значит не заметить расхождения.

Standalone-прогон:  python tests/quick/test_trail.py
Поведение «отказ попал в след» и «секрет не попал» проверяют сценарии `observability.yaml` по
живому серверу. Здесь — то, что сценарием не выразить: прополка старых файлов, предел на файл,
неполное объявление и сломанная запись, которая обязана потерять НАБЛЮДЕНИЕ, а не вызов.
"""
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.observability import Trail  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts" / "guards"))
from reproduce import (_entries, _fill, artefacts, as_steps, depends, exemptions,  # noqa: E402
                       pick, promote, render)

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
ok(not artefacts([{"tool": "media_generate", "args": {}, "ok": True, "facts": ["ФактБезОбъявления"]}], OBS),
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

print("\n== предмет-содержимое и пачка предметов ==")
_set = {"tool": "table_set", "args": {"table": "видео/в1", "sheet": "META", "row_id": "VID_1"},
        "data": {"queued": {"action": "set"}}, "ok": True, "facts": ["RowSet"]}
_q = _yaml.safe_load(promote([_set], "m_очередь", "почему"))
_step = _q["states"]["s_0"]["check"][0]
ok(_step["call"] == "json_read_queue" and _step["with"] == {"table": "видео/в1"},
   "правка спрашивается у ОЧЕРЕДИ: в таблице её ещё нет, и спрашивать таблицу бессмысленно")
ok(_step["expect"]["data_contains"]["row_ids"] == "VID_1",
   "имя предмета подставлено в ОЖИДАНИЕ — иначе одно правило проверяло бы не ту строку")
ok(_yaml.safe_load(promote([_set], "m_о2", "п"))["states"]["s_empty"]["check"][0]["expect"]["data_absent"]["row_ids"] == "VID_1",
   "состояние ДО правки выражено отрицанием: без него «ещё нет» неотличимо от «ответ пуст»")

_media = {"tool": "media_generate", "args": {"table": "видео/в1"},
          "data": {"files": ["готово/а.png", "готово/б.png"]}, "ok": True, "facts": ["MediaGenerated"]}
_items = artefacts([_media], OBS)
ok([i["name"] for i in _items] == ["готово/а.png", "готово/б.png"],
   "один вызов родил ДВА предмета: `*` разворачивает список, а не берёт первый элемент")
ok(len(_yaml.safe_load(promote([_media], "m_медиа", "п"))["states"]) == 4,
   "два независимых предмета из одного вызова дают решётку, а не линию")

print("\n== объявленная ненаблюдаемость ==")
ok(not artefacts([{"tool": "json_push_to_queue", "args": {"table": "т"}, "ok": True,
                   "facts": ["QueuePushed"]}], OBS),
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
    promote([{"tool": "media_generate", "args": {}, "ok": True, "facts": ["ФактБезОбъявления"]}], "m", "п")
    ok(False, "без наблюдаемого предмета карта строиться не должна")
except SystemExit as exc:
    ok("ФактБезОбъявления" in str(exc),
       "недостающее наблюдение называется ПО ИМЕНИ — иначе неполная карта сойдёт за полную")

try:
    promote([_made(f"много/{n}.txt") for n in range(5)], "m", "п")
    ok(False, "за пределом решётка обязана отказать, а не родить нечитаемое")
except SystemExit as exc:
    ok("минимизируй" in str(exc), "отказ за пределом называет, что делать дальше")

print("\n== вердикт по послаблениям: что опроверг живой прогон ==")
FIRED = [{"tool": "excel_read_range", "scenario": "res_refusals", "code": "INTERNAL_ERROR", "facts": []},
         {"tool": "fs_read_file", "scenario": "res_refusals", "code": "", "facts": ["FileRead"]}]
stale, untested = exemptions(FIRED, {"INTERNAL_ERROR"}, {"FileRead"})
ok(len(stale) == 1 and "INTERNAL_ERROR" in stale[0] and "excel_read_range" in stale[0],
   "код объявлен непокрытым, а в бою выстрелил — послабление устарело, и названы инструмент и сценарий")
ok(not untested, "освобождённый факт в записи появился — послабление боем проверено, молчим")
stale2, untested2 = exemptions(FIRED, {"AUTH_FAILED"}, {"ColumnMoved"})
ok(not stale2, "выстреливший код не объявлен непокрытым — это не послабление, а обычный отказ")
ok(len(untested2) == 1 and "ColumnMoved" in untested2[0],
   "освобождённый факт не появился НИ РАЗУ — послабление не проверено ничем")
empty_stale, empty_untested = exemptions([], {"AUTH_FAILED"}, {"ColumnMoved"})
ok(not empty_stale and len(empty_untested) == 1,
   "записи нет вовсе: устаревших не выдумываем, а непроверенным остаётся каждый освобождённый")
TRAIL = [{"tool": "excel_read_range", "scenario": "trail-20260829-134007", "code": "COLUMN_NOT_FOUND",
          "facts": []}]
IN_SCENARIO = [{"tool": "excel_delete_column", "scenario": "ed5_delete_column_under_formula",
                "code": "COLUMN_NOT_FOUND", "facts": ["ColumnDeleted"]}]
ok(len(exemptions(TRAIL, {"COLUMN_NOT_FOUND"}, set())[0]) == 1,
   "след СЕРВЕРА — улика о бое: там отказ случился вне всякого объявления")
ok(not exemptions(IN_SCENARIO, {"COLUMN_NOT_FOUND"}, set(), frozenset(),
                  {"ed5_delete_column_under_formula"})[0],
   "отказ внутри объявленного сценария уликой о бое не считается: прогон был КРАСНЫМ, а не боевым")
ok(not exemptions(IN_SCENARIO, {"COLUMN_NOT_FOUND"}, {"ColumnDeleted"}, frozenset(),
                  {"ed5_delete_column_under_formula"})[1],
   "факты берутся из ВСЕХ записей, даже красных: увиденное однажды увидено")
ok(not exemptions([{"tool": "excel_read_range", "scenario": "res_refusals", "code": "INTERNAL_ERROR",
                    "facts": []}], {"INTERNAL_ERROR"}, set(),
                  {("res_refusals", "excel_read_range")})[0],
   "шаг объявлен известной дырой (`open: F#`) — требовать на неё сценарий значит требовать желаемым нежелаемое")

print("\n== подпись вывода и её разбор по ключу ==")
sys.path.insert(0, str(ROOT / "scripts" / "guards"))
import _stamp  # noqa: E402  источник улики: подпись пишется им, читается производителем
from reproduce import resolve_stamp  # noqa: E402

tree = Path(tempfile.mkdtemp(prefix="vpm-подпись-"))
key, text = _stamp.sign("проба", "УЛИКА", "ось слепа к core — ruff обязан покраснеть",
                        intent="форма-правки", expected="red", actual="red",
                        cmd="ruff check core/advice.py", root=tree)
ok(text.splitlines()[0].startswith(f"⟦vpm {key} УЛИКА⟧"),
   "первая строка подписи говорит РОЛЬ и ключ до всякого разбора")
ok("род=проба" in text and "ждали=red" in text and "запись=" in text,
   "вторая строка несёт ключи для дешёвого сбора улик")
ok(len(_stamp.find(key, tree)) == 1, "запись легла в журнал подписей и находится по ключу")

try:
    _stamp.sign("выдумка", "УЛИКА", "x", root=tree)
    ok(False, "незнакомый род отвергается, а не пишется молча")
except ValueError:
    ok(True, "незнакомый род отвергается, а не пишется молча")
try:
    _stamp.sign("проба", "ВЫДУМКА", "x", root=tree)
    ok(False, "незнакомая роль отвергается, а не пишется молча")
except ValueError:
    ok(True, "незнакомая роль отвергается, а не пишется молча")

out = io.StringIO()
with redirect_stdout(out):
    code = resolve_stamp(key, tree)
resolved = out.getvalue()
ok(code == 0 and "форма-правки" in resolved and "ruff check core/advice.py" in resolved,
   "ключ поднимает намерение и команду повтора — дознание заново не нужно")
out = io.StringIO()
with redirect_stdout(out):
    code = resolve_stamp("0000", tree)
ok(code == 1 and "записи нет" in out.getvalue(),
   "чужой ключ назван прямо — отсутствие улики не выдаётся за пустую улику")
ok(not list((tree / "tests" / ".journal").glob("trail-*.jsonl"))
   and not list((tree / "tests" / ".journal").glob("scenarios-*.jsonl")),
   "журнал подписей своей формы — вход производителя сценариев им не отравляется")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
