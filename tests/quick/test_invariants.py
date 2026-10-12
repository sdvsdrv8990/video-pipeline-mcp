"""
tests/quick/test_invariants.py — сторож межфайловых инвариантов проверяется на временных каталогах.

Проверяет: каждая проверка ЛОВИТ своё нарушение и МОЛЧИТ на чистом входе. Ложные срабатывания,
стоившие разбора при написании сторожа (параметры функций, `with ... as`, `except ... as`,
упоминание бинаря в комментарии), закреплены как регрессия.
"""

import sys

import tempfile

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT / "scripts" / "guards"))

sys.path.insert(0, str(ROOT))

from invariants import (
    НетУлики,
    axis_undeclared,
    codes_exempt_from_scenarios,
    codes_outside_registry,
    codes_without_emitter,
    dead_recovery_in_engine,
    declared_but_unscripted,
    default_instead_of_declaration,
    enum_without_values,
    facts_exempt_from_observation,
    facts_outside_registry,
    facts_without_emitter,
    facts_without_observer,
    observation_incomplete,
    knob_without_reader,
    resources_off_inventory,
    scenario_calls_unknown_tool,
    dispatch_by_value,
    mirrored_declaration,
    declaration_loaded_privately,
    module_without_reader,
    muted_refusal,
    record_field_mismatch,
    unfinished_in_server,
    unscripted_declaration_stale,
)
_checks = 0

_fails = []

def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")

def make(files: dict[str, str]) -> Path:
    """Временный корень: сторожу пути приходят параметром, поэтому мусорить в репозитории не нужно."""
    root = Path(tempfile.mkdtemp(prefix="vpm-inv-"))
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root

def отказ(вызов, дерево: Path) -> str:
    """Причина отказа судить, либо пусто. Молчание оси и её отказ — РАЗНЫЕ исходы, и ассерт,
    принимающий пустой список за оба, не судит ни одного."""
    try:
        вызов(дерево)
    except НетУлики as нет:
        return str(нет)
    return ""

print("\n== код отказа мимо реестра ==")

# Коды отказов в проекте ASCII-заглавные — фикстур обязан быть таким же, иначе он
# проверяет не сторожа, а собственную опечатку.
CODE_SRC = 'from x import ZoneError\n\n\ndef f():\n    raise ZoneError("MY_CODE", "текст")\n'

ok(not codes_outside_registry(make({"core/z.py": CODE_SRC, "config/server_reactions.yaml": "MY_CODE:\n  class: ai_recoverable\n"}),
                              known={"MY_CODE"}),
   "код объявлен и в реестре, и в списке известных — молчим")

ok(len(codes_outside_registry(make({"core/z.py": CODE_SRC, "config/server_reactions.yaml": "OTHER_CODE:\n  class: ai_recoverable\n"}),
                              known={"MY_CODE"})) == 1,
   "код брошен, но в реестре его нет — клиент получит его без класса и рекавери")

ok(len(codes_outside_registry(make({"core/z.py": CODE_SRC, "config/server_reactions.yaml": "MY_CODE:\n  class: ai_recoverable\n"}),
                              known=set())) == 1,
   "код в реестре, но выпал из списка известных — контракт предупредит на боевом пути")

ok(not codes_outside_registry(make({"core/x.py": 'raise ToolError("НЕТ_КОДА")\n'}), known=set()),
   "реестра реакций нет — сверять не с чем: «улики нет», а не трейс вместо вердикта")

print("\n== enum без перечня значений ==")

SCHEMA_OK = "sheets:\n  - name: S\n    columns:\n      - { name: c, type: enum, enum: ['A', 'B'] }\n"

SCHEMA_BAD = "sheets:\n  - name: S\n    columns:\n      - { name: c, type: enum }\n"

ok(not enum_without_values(make({"config/templates/tables/x.schema.yaml": SCHEMA_OK})),
   "enum с перечнем значений — молчим")

ok(len(enum_without_values(make({"config/templates/tables/x.schema.yaml": SCHEMA_BAD}))) == 1,
   "enum без значений — находка: проверить запись нечем")

print("\n== объявление ресурсов против инвентаря ==")

INV = '{"montage_render_scene": {}, "media_generate": {}}'

RES_OK = "default: inline\nclasses:\n  inline: {offload: false}\n  gpu: {offload: true, max_concurrent: 1}\ntools:\n  media_generate: gpu\n"

RES_GHOST = "default: inline\nclasses:\n  inline: {offload: false}\ntools:\n  montage_render_sceen: inline\n"

INV_PATH = "tests/quick/tools_inventory.golden.json"

# Связи внутри файла (класс не объявлен, опечатка в default, снятие с цикла без предела) судит схема у двери
# деклараций при загрузке — случаи в tests/quick/test_declarations.py; здесь — только декларация против инвентаря.

ok(not resources_off_inventory(make({"config/resources.yaml": RES_OK, INV_PATH: INV})),
   "имена сходятся с инвентарём — молчим")

ok(len(resources_off_inventory(make({"config/resources.yaml": RES_GHOST, INV_PATH: INV}))) == 1,
   "инструмент переименован — строка указывает в пустоту, снятие с цикла молча не действует")

ok(not resources_off_inventory(make({"config/x.yaml": "y: 1"})),
   "объявления нет вовсе — не выдумываем нарушение")

print("\n== ветвление по значению вместо таблицы ==")

THREE = ('def f(kind):\n'
         '    if kind == "a":\n        return 1\n'
         '    elif kind == "b":\n        return 2\n'
         '    elif kind == "c":\n        return 3\n'
         '    return 0\n')

TWO = 'def f(kind):\n    if kind == "a":\n        return 1\n    elif kind == "b":\n        return 2\n    return 0\n'

MIXED = ('def f(a, b, c):\n'
         '    if a == "x":\n        return 1\n'
         '    elif b == "y":\n        return 2\n'
         '    elif c == "z":\n        return 3\n'
         '    return 0\n')

ok(len(dispatch_by_value(make({"core/z.py": THREE}))) == 1,
   "три ветки по одному значению — это таблица, а не выбор")

ok(not dispatch_by_value(make({"core/z.py": TWO})),
   "две ветки — выбор, а не таблица: молчим")

ok(not dispatch_by_value(make({"core/z.py": MIXED})),
   "ветки по РАЗНЫМ именам — не диспетчеризация, молчим")

THREE_INTS = ('def f(code):\n'
              '    if code == 200:\n        return 1\n'
              '    elif code == 404:\n        return 2\n'
              '    elif code == 500:\n        return 3\n'
              '    return 0\n')

THREE_CONSTS = ('SET, GET, DEL = "set", "get", "del"\n'
                'def f(kind):\n'
                '    if kind == SET:\n        return 1\n'
                '    elif kind == GET:\n        return 2\n'
                '    elif kind == DEL:\n        return 3\n'
                '    return 0\n')

ok(len(dispatch_by_value(make({"core/z.py": THREE_INTS}))) == 1,
   "три ветки по числам — та же таблица: тип значения диспетчеризацию не отменяет")

ok(len(dispatch_by_value(make({"core/z.py": THREE_CONSTS}))) == 1,
   "именованная константа вместо литерала — случай всё равно добавляется правкой КОДА")

print("\n== объявление сценария против описи и реестра ==")

REGISTRY_TWO = ("ALIVE_CODE:\n  class: ai_recoverable\n"
                "ORPHAN_CODE:\n  class: ai_recoverable\n")

EMITTER = 'def f():\n    raise Err("ALIVE_CODE", "текст")\n'

INVENTORY_TWO = '{"инстр_а": {}, "инстр_б": {}}'

COVERS_ALL = ('- scenario: s\n  why: w\n  when:\n'
              '    - call: инстр_а\n      expect: {ok: true}\n'
              '    - call: инстр_б\n      expect: {ok: false, code: ALIVE_CODE}\n')

ok(len(scenario_calls_unknown_tool(make({
    "tests/quick/tools_inventory.golden.json": INVENTORY_TWO,
    "tests/scenarios/a.yaml": '- scenario: s\n  why: w\n  when:\n    - call: снесённый\n      expect: {ok: true}\n',
}))) == 1, "сценарий зовёт инструмент мимо описи — переименование оставило объявление в пустоту")

ok(not scenario_calls_unknown_tool(make({
    "tests/quick/tools_inventory.golden.json": INVENTORY_TWO,
    "tests/scenarios/a.yaml": ('- scenario: s\n  why: w\n  when:\n    - call: нет_такого\n'
                              '      expect: {ok: false, code: TOOL_NOT_FOUND}\n'),
})), "несуществующее имя, когда ждут TOOL_NOT_FOUND, — предмет проверки, а не опечатка")

ok(len(codes_without_emitter(make({"config/server_reactions.yaml": REGISTRY_TWO,
                                   "core/z.py": EMITTER}))) == 1,
   "код объявлен клиенту, а бросить его некому — обещание без держателя")

ok(not codes_without_emitter(make({"config/server_reactions.yaml": "ALIVE_CODE:\n  class: ai_recoverable\n",
                                   "core/z.py": EMITTER})),
   "каждый объявленный код кто-то бросает — молчим")

ok(len(codes_without_emitter(make({"config/server_reactions.yaml": REGISTRY_TWO,
                                   "core/z.py": EMITTER,
                                   "core/contracts/error_detail.py": 'KNOWN = ("ORPHAN_CODE",)\n'}))) == 1,
   "код назван ТОЛЬКО в перечне контракта — он объявляет коды, а не бросает: находка остаётся")

PACKAGES_LIVE = (ROOT / "config" / "packages.yaml").read_text(encoding="utf-8")


ORPHAN_IN_PACKAGES = PACKAGES_LIVE.replace("PACKAGE_INDEX_FAILED", "ORPHAN_CODE")


def с_пакетами(packages: str, reader: str = "scripts/cost.py", registry: str = REGISTRY_TWO) -> Path:
    """Корень с packages.yaml и модулем `reader`, который читает её слот (`PACKAGES`)."""
    return make({"config/server_reactions.yaml": registry, "core/z.py": EMITTER, "config/packages.yaml": packages,
                 "tests/quick/tools_inventory.golden.json": INVENTORY_TWO, "tests/scenarios/a.yaml": COVERS_ALL,
                 reader: "from core.declaration_schemas import PACKAGES\n"})


ok(not codes_without_emitter(с_пакетами(ORPHAN_IN_PACKAGES)),
   "код отдаёт декларация полем-кодом схемы (index.fetch.codes.failed), слот читает скрипт — обещание держат: молчим")
ok(len(codes_without_emitter(с_пакетами(ORPHAN_IN_PACKAGES, reader="docs/cost.md"))) == 1,
   "слот декларации не читает ни сервер, ни скрипт — её код бросить некому: находка остаётся")
ok(len(codes_without_emitter(с_пакетами(PACKAGES_LIVE.replace("hosts: []", "hosts: [ORPHAN_CODE]"),
                                       registry=REGISTRY_TWO + "PACKAGE_INDEX_FAILED:\n  class: server_recoverable\n"))) == 1,
   "та же строка в поле без метки кода (hosts) — не код: суд по типу поля, а не по виду строки, находка остаётся")
ok("packages.yaml" in отказ(codes_without_emitter, с_пакетами(PACKAGES_LIVE)),
   "код поля-кода декларации не объявлен в реестре рядом — дверь отказывает, ось не судит и называет файл")
ok("packages.yaml" in отказ(codes_without_emitter, с_пакетами(PACKAGES_LIVE.replace("attempts: 6", "attempts: 0"))),
   "декларация не проходит свою схему — ось отказывает судить и называет файл, а не молчит")
ok(not declared_but_unscripted(с_пакетами(ORPHAN_IN_PACKAGES)),
   "код декларации, которую читает только скрипт, сценарий сервера не достанет — в «сервер бросает» его нет")
ok(len(declared_but_unscripted(с_пакетами(ORPHAN_IN_PACKAGES, reader="core/cost.py"))) == 1,
   "ту же декларацию читает код сервера — её код сервер бросает, и сценария на него нет: находка")

ok(not declared_but_unscripted(make({"config/server_reactions.yaml": REGISTRY_TWO,
                                     "core/z.py": EMITTER,
                                     "tests/quick/tools_inventory.golden.json": INVENTORY_TWO,
                                     "tests/scenarios/a.yaml": COVERS_ALL})),
   "оба инструмента позваны, живой код ожидается — сирота считается соседним сторожем, молчим")

ok(len(declared_but_unscripted(make({"config/server_reactions.yaml": REGISTRY_TWO,
                                     "core/z.py": EMITTER,
                                     "tests/quick/tools_inventory.golden.json": INVENTORY_TWO,
                                     "tests/scenarios/a.yaml": COVERS_ALL.replace("инстр_б", "инстр_а")}))) == 1,
   "инструмент из описи не зовёт ни один сценарий — слепота выросла молча")

ok(len(declared_but_unscripted(make({"config/server_reactions.yaml": REGISTRY_TWO,
                                     "core/z.py": EMITTER,
                                     "tests/quick/tools_inventory.golden.json": INVENTORY_TWO,
                                     "tests/scenarios/a.yaml": COVERS_ALL.replace("ALIVE_CODE", "FOREIGN_CODE")}))) == 1,
   "бросаемый код не ждёт ни один сценарий — новый код отказа без объявления")


def с_несценарным(unscripted: str, scenario: str = COVERS_ALL.replace("ALIVE_CODE", "FOREIGN_CODE")) -> Path:
    """Корень, где ALIVE_CODE бросают, а сценарий его не ждёт, плюс объявление несценарности."""
    return make({"config/server_reactions.yaml": REGISTRY_TWO, "core/z.py": EMITTER,
                 "tests/quick/tools_inventory.golden.json": INVENTORY_TWO, "tests/scenarios/a.yaml": scenario,
                 "tests/harness/unscripted.yaml": unscripted})


EXEMPT_ALIVE = "ALIVE_CODE:\n  why: рождает только падение, место z.py:2\n"

ok(not declared_but_unscripted(с_несценарным(EXEMPT_ALIVE))
   and len(codes_exempt_from_scenarios(с_несценарным(EXEMPT_ALIVE))) == 1
   and not unscripted_declaration_stale(с_несценарным(EXEMPT_ALIVE)),
   "код объявлен несценарным с причиной — уходит из «не покрыто» и считается своей осью-храповиком")
ok(len(declared_but_unscripted(с_несценарным("ALIVE_CODE:\n  why: ''\n"))) == 1
   and "без причины" in " ".join(unscripted_declaration_stale(с_несценарным("ALIVE_CODE:\n  why: ''\n"))),
   "объявление без причины не освобождает: код остаётся непокрытым, жёсткая ось называет пустой `why`")
ok("сценарий его ждёт" in " ".join(unscripted_declaration_stale(с_несценарным(EXEMPT_ALIVE, scenario=COVERS_ALL))),
   "код объявлен несценарным, а сценарий его уже ждёт — объявление устарело, жёсткая ось его называет")
ok("не бросает" in " ".join(unscripted_declaration_stale(с_несценарным("ORPHAN_CODE:\n  why: падение\n"))),
   "несценарным объявлен код, которого сервер не бросает — освобождать не от чего, жёсткая ось его называет")

print("\n== рецепт движка, который клиент не увидит ==")

REG_REC = ("BAD_PROFILE:\n  class: ai_recoverable\n  recovery:\n    suggested_tool: table_get_row\n"
           "NO_TOOL:\n  class: ai_recoverable\n  recovery:\n    reason: почини строку\n")

SAME = 'def f():\n    raise ZoneError("BAD_PROFILE", "текст", "почему", "table_get_row")\n'

OTHER = 'def f():\n    raise ZoneError("BAD_PROFILE", "текст", "почему", "table_set")\n'

KWARG = 'def f():\n    raise ZoneError("BAD_PROFILE", "текст", suggested_tool="table_set")\n'

SILENT = 'def f():\n    raise ZoneError("NO_TOOL", "текст", "почему", "media_models")\n'

NO_RECIPE = 'def f():\n    raise ZoneError("BAD_PROFILE", "текст")\n'

ok(not dead_recovery_in_engine(make({"config/server_reactions.yaml": REG_REC, "core/z.py": SAME})),
   "совет движка совпал с реестром — клиент получит то же самое, молчим")

ok(len(dead_recovery_in_engine(make({"config/server_reactions.yaml": REG_REC, "core/z.py": OTHER}))) == 1,
   "движок советует другой инструмент — до клиента доедет реестровый, текст в коде мёртв")

ok(len(dead_recovery_in_engine(make({"config/server_reactions.yaml": REG_REC, "core/z.py": KWARG}))) == 1,
   "именованный `suggested_tool` — та же форма: обе в ходу, и обе обязаны ловиться")

ok(len(dead_recovery_in_engine(make({"config/server_reactions.yaml": REG_REC, "core/z.py": SILENT}))) == 1,
   "у реестра рецепт без инструмента — совет движка пропадает, и это тоже находка")

ok(not dead_recovery_in_engine(make({"config/server_reactions.yaml": REG_REC, "core/z.py": NO_RECIPE})),
   "движок не советует ничего — перекрывать нечего, молчим")

ok(not dead_recovery_in_engine(make({"config/server_reactions.yaml": REG_REC,
                                     "core/z.py": 'def f():\n    raise ZoneError("ЧУЖОЙ_КОД", "т", "п", "table_set")\n'})),
   "код вне реестра — рецепт движка ЕДИНСТВЕННЫЙ и доезжает: обвинять нельзя")

print("\n== копия объявления в коде ==")

DECL_ONE = "ip_blocklist:\n  ban_duration_hours: 24\n"

DECL_TWICE = "ip_blocklist:\n  ban_duration_hours: 24\nother:\n  ban_duration_hours: 48\n"

DECL_SHORT = "limits:\n  ttl: 24\n"

DECL_LIST = "injection:\n  bad_patterns:\n    - забудь всё\n    - ты теперь\n"

ok(len(mirrored_declaration(make({"config/f.yaml": DECL_ONE,
                                  "core/z.py": "ban_duration_hours = 24\n"}))) == 1,
   "имя и значение совпали с единственным объявлением — второй источник того же факта")

ok(len(mirrored_declaration(make({"config/f.yaml": DECL_ONE,
                                  "core/z.py": "def f(ban_duration_hours: int = 24):\n    return ban_duration_hours\n"}))) == 1,
   "дефолт параметра — та же копия: правка декларации его не тронет")

ok(len(mirrored_declaration(make({"config/f.yaml": DECL_ONE,
                                  "core/z.py": "DEFAULT_BAN_DURATION_HOURS = 24\n"}))) == 1,
   "приставка DEFAULT_ не отменяет копию: запасное значение обязано совпадать с объявлением")

ok(len(mirrored_declaration(make({"config/f.yaml": DECL_LIST,
                                  "core/z.py": 'bad_patterns = ["ты теперь", "забудь всё"]\n'}))) == 1,
   "список ловится по составу, а не по порядку — перестановка не прячет копию")

ok(not mirrored_declaration(make({"config/f.yaml": DECL_ONE,
                                  "core/z.py": "ban_duration_hours = 48\n"})),
   "то же имя, другое значение — код НЕ копия, а своё решение: обвинять нельзя")

ok(not mirrored_declaration(make({"config/f.yaml": DECL_ONE, "core/z.py": "retry_after = 24\n"})),
   "то же значение под другим именем — совпадение чисел, а не второй источник")

ok(not mirrored_declaration(make({"config/f.yaml": DECL_TWICE,
                                  "core/z.py": "ban_duration_hours = 24\n"})),
   "ключ называет в декларациях два разных значения — с чем именно совпало, неизвестно")

ok(not mirrored_declaration(make({"config/f.yaml": DECL_SHORT, "core/z.py": "ttl = 24\n"})),
   "короткое имя совпадает по случайности — порог длины держит сторожа точным")

ok(not mirrored_declaration(make({"config/f.yaml": "flags:\n  enabled: true\n",
                                  "core/z.py": "enabled = True\n"})),
   "булево значение объявлено везде — уликой оно быть не может")

ok(len(mirrored_declaration(make({"config/f.yaml": DECL_ONE,
                                  "core/z.py": "ban_duration_hours = 24.0\n"}))) == 1,
   "`24` в декларации и `24.0` в коде — одно значение, разный тип его не прячет")

ok(not mirrored_declaration(make({"config/f.yaml": DECL_ONE, "core/z.py": 'ban_duration_hours = "24"\n'})),
   "строка против числа — не то же значение: сравнение не приводит типы вслепую")

ok(len(mirrored_declaration(make({"config/f.yaml": DECL_LIST,
                                  "core/z.py": 'DEFAULT_BAD_PATTERNS = frozenset({"ты теперь", "забудь"})\n'}))) == 1,
   "запасной перечень разошёлся с объявлением — та половина, что сработает при отказе загрузки")

ok(not mirrored_declaration(make({"config/f.yaml": DECL_LIST,
                                  "core/z.py": 'bad_patterns = ["ты теперь", "чужое"]\n'})),
   "без пометки DEFAULT_ расхождение — не копия, а своё решение: молчим")

ok(len(mirrored_declaration(make({"config/f.yaml": DECL_LIST,
                                  "core/z.py": 'DEFAULT_BAD_PATTERNS = frozenset({"ты теперь", "забудь всё"})\n'}))) == 1,
   "запасной перечень СОВПАЛ — это копия, и она разойдётся при следующей правке декларации")

ok(len(mirrored_declaration(make({"config/f.yaml": DECL_ONE,
                                  "core/z.py": 'x = cfg.get("ban_duration_hours", 24)\n'}))) == 1,
   "`.get(ключ, дефолт)` — копия, у которой имя приходит СТРОКОЙ: три прежние формы её не видели")

ok(not mirrored_declaration(make({"config/f.yaml": DECL_ONE,
                                  "core/z.py": 'x = cfg.get("ban_duration_hours", 48)\n'})),
   "дефолт `.get` разошёлся с объявлением и не помечен запасным — своё решение, не копия")

ok(len(mirrored_declaration(make({"config/f.yaml": "usage:\n  default_unit: call\n",
                                  "core/z.py": 'x = cfg.get("default_unit", "call")\n'}))) == 1,
   "`default_unit` объявлено САМО — имя читается буквально, а не как запасное для ключа `unit`")

ok(len(mirrored_declaration(make({"config/f.yaml": DECL_TWICE,
                                  "core/z.py": 'x = cfg.get("ip_blocklist", {}).get("ban_duration_hours", 24)\n'}))) == 1,
   "имя названо в декларациях дважды, но ПУТЬ чтения один — по имени это отпускали, по пути обвиняем")

ok(len(mirrored_declaration(make({"config/f.yaml": DECL_TWICE,
                                  "core/z.py": 'sec = cfg.get("ip_blocklist") or {}\nx = sec.get("ban_duration_hours", 24)\n'}))) == 1,
   "раздел добыт в переменную — путь всё равно собирается, иначе сторож слеп к обычной форме чтения")

ok(not mirrored_declaration(make({"config/f.yaml": DECL_TWICE,
                                  "core/z.py": 'x = cfg.get("ip_blocklist", {}).get("ban_duration_hours", 48)\n'})),
   "путь тот же, значение другое — код решает сам, копией это не является")

ok(not mirrored_declaration(make({"config/f.yaml": "a:\n  s:\n    ttl: 24\nb:\n  s:\n    ttl: 24\n",
                                  "core/z.py": 'x = cfg.get("s", {}).get("ttl", 24)\n'})),
   "суффикс пути попал в два объявления — с чем совпало, неизвестно: обвинять нельзя")

ok(not mirrored_declaration(make({"config/f.yaml": DECL_TWICE,
                                  "core/z.py": 'def f(spec):\n    return spec.get("ban_duration_hours", 24)\n'})),
   "раздел приехал параметром — путь не выводится, а имя многозначно: это остаток слепой зоны")

ok(len(mirrored_declaration(make({"config/f.yaml": DECL_ONE,
                                  "core/z.py": 'x = cfg.get("ip_blocklist", {}).get("ban_duration_hours", 24)\n'}))) == 1,
   "оба правила указывают на одну строку — нота одна: двойной счёт задрал бы потолок на пустом месте")

print("\n== своя загрузка декларации мимо общей двери ==")

DECL_FILE = {"config/uniqueness.yaml": "ngram:\n  size: 4\n"}

ok(len(declaration_loaded_privately(make({**DECL_FILE,
        "core/z.py": 'import yaml\ndata = yaml.safe_load(open("config/uniqueness.yaml").read())\n'}))) == 1,
   "модуль называет декларацию И разбирает YAML сам — у отказа заводится своя политика")

ok(not declaration_loaded_privately(make({**DECL_FILE,
        "core/z.py": 'from core.declaration import Declaration\nd = Declaration("config/uniqueness.yaml", E, "s", "h").data\n'})),
   "чтение через общую дверь не обвиняется — политика отказа одна на всех")

ok(not declaration_loaded_privately(make({**DECL_FILE,
        "core/z.py": 'import yaml\ndata = yaml.safe_load(запрос_ии)\n'})),
   "разбор YAML, пришедшего НЕ из декларации, — не наша ось: имя файла модуль не называет")

ok(not declaration_loaded_privately(make({"core/z.py": 'import yaml\nyaml.safe_load("config/uniqueness.yaml")\n'})),
   "деклараций на дереве нет вовсе — судить не по чему, молчим")

print("\n== модуль без единого читателя ==")

ok(len(module_without_reader(make({"core/lonely.py": "x = 1\n"}))) == 1,
   "модуль без импортирующих и без объявления — мёртвая половина")

ok(not module_without_reader(make({"core/lonely.py": "x = 1\n",
                                   "server.py": "from core.lonely import x\n"})),
   "абсолютный импорт — читатель найден")

ok(not module_without_reader(make({"core/pkg/__init__.py": "from .lonely import x\n",
                                   "core/pkg/lonely.py": "x = 1\n"})),
   "относительный импорт из своего пакета — читатель найден, а не пропущен")

ok(not module_without_reader(make({"core/img/onnx_bg.py": "class OnnxBGRemoval: pass\n",
                                   "config/providers.yaml": "by_provider:\n  L: img.onnx_bg:OnnxBGRemoval\n"})),
   "модуль жив ОБЪЯВЛЕНИЕМ — иначе вся декларативная архитектура числилась бы мёртвой")

ok(not module_without_reader(make({"core/pkg/__init__.py": "\n", "core/runner/__main__.py": "x = 1\n"})),
   "пакет и точка входа грузятся по построению — читателя с них не спрашиваем")

ok(len(module_without_reader(make({"core/lonely.py": "x = 1\n",
                                   "config/providers.yaml": "by_provider:\n  L: img.other:Other\n"}))) == 1,
   "чужая строка в конфиге читателем не становится")

ok(not module_without_reader(make({"docs/x.md": "text\n"})),
   "ни core/, ни tools/ в дереве нет — событие не наше, молчим")

print("\n== факт против реестра типов, наблюдателя и эмиттера ==")

FACT_REG = 'KNOWN_FACT_TYPES = {\n    "FileCreated", "FileWritten", "FolderCreated",\n}\n'

EMIT = 'from core.contracts import Fact\nFact(type="FileCreated", data={})\n'

OBSERVE = "FileCreated:\n  identity: args.path\n  observe: fs_read_file\n"

ok(len(facts_outside_registry(make({"core/contracts/fact.py": FACT_REG,
                                    "tools/x/__init__.py": 'from core.contracts import Fact\n'
                                                           'Fact(type="Unlisted", data={})\n'}))) == 1,
   "факт шлётся, а в реестре типов его нет — предупреждение модели слышно только в рантайме")

ok(not facts_outside_registry(make({"core/contracts/fact.py": FACT_REG, "tools/x/__init__.py": EMIT})),
   "факт из реестра не обвиняется")

ok(not facts_outside_registry(make({
       "core/contracts/fact.py": FACT_REG,
       "tools/x/__init__.py": 'from core.contracts import Fact\n'
                              'Fact(type="FolderCreated" if c["kind"] == "folder" else "FileCreated",'
                              ' data={})\n'})),
   "у тернарника читаются ВЕТВИ, а не условие: иначе `kind`/`folder` попали бы в факты")

ok(not facts_outside_registry(make({"tools/x/__init__.py": EMIT})),
   "реестра типов нет — сверять не с чем, молчим")

ok(len(facts_without_observer(make({"tools/x/__init__.py": EMIT}))) == 1,
   "факт шлётся, а решения о наблюдении нет — ни правила, ни объявленной ненаблюдаемости")

ok(not facts_without_observer(make({
       "tools/x/__init__.py": EMIT,
       "tests/harness/observations.yaml": "FileCreated: {observes: нечего, why: чтение}\n"})),
   "объявленная ненаблюдаемость — тоже РЕШЕНИЕ: забытым факт после неё не считается")

ok(not facts_without_observer(make({"tools/x/__init__.py": EMIT,
                                    "tests/harness/observations.yaml": OBSERVE})),
   "строка наблюдения есть — долг закрыт объявлением, без нового скрипта")

ok(not facts_without_observer(make({"core/contracts/fact.py": FACT_REG})),
   "не шлётся ни один факт — событие не наше")

ok(len(facts_without_emitter(make({"core/contracts/fact.py": FACT_REG,
                                   "tools/x/__init__.py": EMIT}))) == 2,
   "два типа обещаны реестром, а слать их некому")

ok(not facts_without_emitter(make({
       "core/contracts/fact.py": 'KNOWN_FACT_TYPES = {"FileWritten"}\n',
       "tools/x/__init__.py": '_created_result(path, size, False, "FileWritten")\n'})),
   "тип, уехавший в вызов параметром, шлётся — обвинять его значило бы краснеть на живом пути")

ok(len(facts_without_emitter(make({"core/contracts/fact.py": 'KNOWN_FACT_TYPES = {"FileCreated"}\n',
                                   "tools/x/__init__.py": EMIT,
                                   "tests/harness/observations.yaml": OBSERVE
                                       + "FileAppended:\n  identity: args.path\n"}))) == 1,
   "наблюдатель построен на факт, которого сервер не шлёт — карта на пустоте")

print("\n== объявление наблюдения: полное или никакое ==")

ok(len(facts_exempt_from_observation(make({
       "tests/harness/observations.yaml": "A: {observes: нечего, why: чтение}\nB: {observes: нечего, why: очередь}\n"}))) == 2,
   "объявленная ненаблюдаемость считается: без потолка это дверь мимо наблюдения")

ok(not facts_exempt_from_observation(make({"tests/harness/observations.yaml": OBSERVE})),
   "правило ненаблюдаемостью не считается — оно спрашивает реальность")

ok(len(observation_incomplete(make({
       "tests/harness/observations.yaml": "A: {observes: нечего}\n"}))) == 1,
   "«наблюдать нечего» без причины — это «потом разберусь», записанное как решение")

ok(len(observation_incomplete(make({
       "tests/harness/observations.yaml": "A:\n  identity: args.path\n  observe: fs_read_file\n"
                                          "  with: {path: x}\n  present: {ok: true}\n"}))) == 1,
   "правило без `absent` роняет воспроизводителя на живой записи, а до неё выглядит покрытием")

ok(not observation_incomplete(make({"tests/harness/observations.yaml":
       "A:\n  identity: args.path\n  observe: fs_read_file\n  with: {path: x}\n"
       "  present: {ok: true}\n  absent: {ok: false, code: E}\n"})),
   "полное правило не обвиняется")

print("\n== ручка объявлена, а читателя нет ==")

RECORD = "compensation:\n  record_sheet: DECISIONS\n  scene_column: scene_id\n  memory_file: p.md\n"

READS_TWO = 'a = cfg["record_sheet"]\nb = cfg["scene_column"]\n'

ok(len(knob_without_reader(make({"config/u.yaml": RECORD, "core/z.py": READS_TWO}))) == 1,
   "секцию читают полями, а эту ручку никто не называет — правка строки не меняет ничего")

ok(not knob_without_reader(make({"config/u.yaml": RECORD,
                                 "core/z.py": READS_TWO + 'c = cfg["memory_file"]\n'})),
   "ручку грузят — молчим")

ok(len(knob_without_reader(make({"config/u.yaml": RECORD, "tests/t.py": READS_TWO + 'c = cfg["memory_file"]\n',
                                 "core/z.py": READS_TWO}))) == 1,
   "читатель в наборе тестов не считается: поведение сервера от него не зависит")

ok(not knob_without_reader(make({"config/c.yaml": "codecs:\n  by_name:\n    h264: libx264\n    h265: libx265\n",
                                 "core/z.py": 'x = cfg["by_name"].get(name)\n'})),
   "карта: ключи приходят данными, соседей по имени не читают — обвинять нечего")

ok(not knob_without_reader(make({"config/u.yaml": RECORD, "config/o.yaml": "kinds:\n  - memory_file\n",
                                 "core/z.py": READS_TWO})),
   "ключ стоит в декларациях ещё и значением — это величина предметной области, не ручка")

ok(not knob_without_reader(make({"config/f.yaml": "filters:\n  params:\n    properties:\n      color:\n"
                                 "        type: string\n        title: Цвет\n        pattern: '^0x'\n",
                                 "core/z.py": 'x = s["type"]\ny = s["title"]\n'})),
   "фрагмент JSON-схемы: его проверяет библиотека целиком, по именам его не грузят")

ok(not knob_without_reader(make({"config/u.yaml": "sec:\n  record_sheet: D\n  scene_column: s\n  ttl: 5\n",
                                 "core/z.py": READS_TWO})),
   "короткое имя РУЧКИ совпадает по случайности — порог длины держит сторожа точным")

ok(not knob_without_reader(make({"core/z.py": READS_TWO})),
   "деклараций нет вовсе — ручек не бывает, а не «все мёртвые»")

ok(len(knob_without_reader(make({"config/u.yaml": RECORD,
                                 "core/z.py": READS_TWO + '# раньше читали cfg["memory_file"], убрали\n'}))) == 1,
   "имя в комментарии читателем не считается: комментария в дереве нет")

ok(len(knob_without_reader(make({"config/u.yaml": RECORD, "core/z.py": READS_TWO
                                 + 'def q():\n    """Ключ "memory_file" когда-то грузился."""\n'}))) == 1,
   "имя внутри докстринга — проза: значением узла стоит весь текст, а не ключ")

ok(not knob_without_reader(make({"config/u.yaml": RECORD,
                                 "core/z.py": READS_TWO + 'c = obj.memory_file\n'})),
   "обращение к полю — настоящий читатель наравне со строкой-ключом")

LOGGING = ("logging:\n  log_all_requests: false\n  log_suspicious: true\n"
           "  log_blocked: true\n  log_file: p.log\n")

ok(len(knob_without_reader(make({"config/f.yaml": LOGGING,
                                 "core/z.py": 'a = cfg["log_suspicious"]\n'}))) == 3,
   "запись судится по форме ключей: три мёртвые ручки видны, хотя читают меньше половины секции")

ok(not knob_without_reader(make({"config/p.yaml": "fit:\n  dtype_alias:\n    float16: fp16\n    bfloat16: bf16\n",
                                 "core/z.py": 'x = m["float16"]\n'})),
   "ключ карты — величина предметной области, а не имя ручки из слов: молчим")

ok(not knob_without_reader(make({"config/e.yaml": "failures:\n  by_reason:\n    Encoder not found: x\n"
                                 "    missing_input_file: y\n",
                                 "core/z.py": 'x = f["Encoder not found"]\n'})),
   "среди ключей секции есть не-имя — секция карта целиком, обвинять соседей по ней нечего")

ok(not knob_without_reader(make({"config/u.yaml": RECORD, "core/z.py": "x = 1\n"})),
   "секцию не читают вовсе — улики нет, а не «все ручки мёртвые»")

print("\n== конфигурация, которой нет ==")

DECL = "limits:\n  max_size: 10\n"

NAMES = 'DECLARATION = "config/u.yaml"\n'

DOOR = ('from core.declaration import Declaration\n'
        'cfg = Declaration("config/u.yaml", ValueError, "", "").data\n')

READS_ABSENT = 'x = cfg["limits"].get("max_retries", 3)\n'

WRAPPED = ('from core.declaration import Declaration\n\n\n'
           'class Limits:\n'
           '    def __init__(self, path):\n'
           '        self._decl = Declaration(path, ValueError, "", "")\n\n'
           '    @property\n'
           '    def config(self):\n'
           '        return self._decl.data\n\n'
           '    @property\n'
           '    def limits(self):\n'
           '        return self.config.get("limits") or {}\n\n'
           '    def retries(self):\n'
           '        return self.limits.get("max_retries", 3)\n')


def ручки_мимо(code: str, decl: str = DECL) -> int:
    return len(default_instead_of_declaration(make({"config/u.yaml": decl, "core/z.py": code})))


ok(ручки_мимо(DOOR + READS_ABSENT) == 1,
   "у значения из двери спрашивают строку, которой в декларации нет — значение живёт в коде")
ok(ручки_мимо(DOOR + READS_ABSENT, "limits:\n  max_size: 10\n  max_retries: 3\n") == 0,
   "ключ объявлен — дефолт лишь страхует, правка конфига действует")
ok(ручки_мимо(WRAPPED) == 1,
   "дверь в атрибуте класса, данные через обёртку config, раздел через обёртку limits — тот же факт")
ok(ручки_мимо(DOOR + 'for name, section in cfg.items():\n    x = section.get("max_retries", 3)\n') == 1,
   "перебор разделов декларации — значение из двери, спуск прослежен")
ok(ручки_мимо('import core.declaration as door\ncfg = door.Declaration("config/u.yaml", ValueError, "", "").data\n'
              + READS_ABSENT) == 1,
   "дверь импортом модуля — полное имя то же")
ok(ручки_мимо(NAMES + READS_ABSENT) == 0,
   "модуль называет декларацию строкой, но приёмник не из двери — форма чтения уликой не служит")
ok(ручки_мимо(DOOR + 'import json\nvoice = json.loads(TEXT)\nx = voice.get("limits", {}).get("max_retries", 3)\n') == 0,
   "ответ поставщика с ключом-тёзкой декларации — внешние данные, не конфигурация (catalog.py:256)")
ok(ручки_мимо('class Declaration:\n    data = {"limits": {}}\n\n\ncfg = Declaration().data\n' + READS_ABSENT) == 0,
   "свой класс с именем Declaration — не дверь: имя не черта, черта — полное имя импорта")
ok(ручки_мимо(DOOR + 'x = cfg["limits"].get("nested", {})\n') == 0,
   "дефолт не литерал — это спуск по декларации, а не ручка")
ok(not default_instead_of_declaration(make({"core/z.py": DOOR + READS_ABSENT})),
   "деклараций нет вовсе — улики нет, а не «весь конфиг мимо»")

print("\n== объявлено незавершённым ==")

ok(len(unfinished_in_server(make({"core/z.py": "def f():\n    raise NotImplementedError\n"}))) == 1,
   "стаб кричит — и прибавление такого крика становится решением, а не строкой между делом")

ok(len(unfinished_in_server(make({"core/z.py": 'def f():\n    raise NotImplementedError("позже")\n'}))) == 1,
   "форма с аргументом — тот же стаб: имя берётся из вызова, а не из голого узла")

ok(not unfinished_in_server(make({"tests/t.py": "def f():\n    raise NotImplementedError\n"})),
   "стаб в наборе тестов сервером не исполняется — не его зона")

ok(not unfinished_in_server(make({"core/z.py": 'def f():\n    raise ValueError("x")\n'})),
   "обычный отказ — не незавершённость: молчим")

print("\n== отказ погашен молча ==")

ok(len(muted_refusal(make({"core/z.py": "def f():\n    try:\n        g()\n    except Exception:\n        return {}\n"}))) == 1,
   "широкий except отдаёт пустоту вместо отказа — клиент считает её ответом")

ok(not muted_refusal(make({"core/z.py": "def f():\n    try:\n        g()\n    except OSError:\n        return {}\n"})),
   "узкий перехват назвал причину — это решение, а не глушение")

ok(not muted_refusal(make({"core/z.py": 'def f():\n    try:\n        g()\n    except Exception as e:\n        return err(str(e))\n'})),
   "пойманное названо и уехало отказом — обработчик говорит")

ok(not muted_refusal(make({"core/z.py": "def f():\n    try:\n        g()\n    except Exception:\n        raise\n"})),
   "исключение поднято дальше — гасить нечего")

ok(not muted_refusal(make({"core/z.py": "def f():\n    try:\n        g()\n    except Exception:\n        print('сбой')\n"})),
   "след оставлен — отказ не нем, даже если наверх ушла пустота")

ok(len(muted_refusal(make({"core/z.py": "import contextlib\n\n\ndef f():\n"
                                        "    with contextlib.suppress(Exception):\n        g()\n"}))) == 1,
   "`suppress(Exception)` — тот же немой перехват: узла `except` у него нет, а отказ гасится так же")

ok(not muted_refusal(make({"core/z.py": "import contextlib\n\n\ndef f():\n"
                                        "    with contextlib.suppress(OSError):\n        g()\n"})),
   "узкий `suppress(OSError)` — решение о конкретной причине, не улика")

print(f"\n{'='*50}")

print("== поле записи мимо объявления: промах немой по природе ==")

_REC = ('entry = {"scenario": s, "step": 1, "tool": t, "args": {}, "ok": True, "code": "",\n'
        '         "message": "", "reaction_class": "", "recovery": {}, "facts": [], "data": {},\n'
        '         "console": []}\n')

ok(not record_field_mismatch(make({"tests/harness/scenario.py": _REC})),
   "запись из объявленных полей молчит")

ok(len(record_field_mismatch(make({
    "tests/harness/scenario.py": _REC.replace('"console": []', '"console": [], "klass": ""')}))) == 1,
   "лишнее поле у писателя названо: форма разошлась, а читатель об этом не узнает")

ok(len(record_field_mismatch(make({
    "scripts/guards/reproduce.py": 'x = entry.get("class")\n'}))) == 1,
   "чтение записи именем ОБЪЯВЛЕНИЯ (`class`) поймано — у записи поле зовётся reaction_class")

ok(not record_field_mismatch(make({
    "scripts/guards/reproduce.py": 'x = entry.get("reaction_class")\n'})),
   "объявленным именем спрашивать можно")

ok(not record_field_mismatch(make({
    "scripts/guards/reproduce.py": 'x = item.get("observes")\n'})),
   "чужой словарь не запись: `.get` по объявлению не обвиняется")

ok(not record_field_mismatch(make({
    "tests/harness/scenario.py": 'observed = {"ok": True, "code": "", "klass": ""}\n'})),
   "ответ сервера записью не является — узнаётся по ПАРЕ ключей scenario+ok, а не по одному")

# Роды улики живут в ЕДИНОМ словаре, а не рядом со сценариями: подставное дерево обязано класть
# оба файла, иначе набор судит объявление, которого в нём нет.
УЛИКИ = """главные:
  редактирование: {про: изменили существующее, видно: Edit}
роды:
  правка-компонента:
    главная: редактирование
    про: тронут компонент
    когда: ['*.tsx']
сторожа: {}
"""

ЭКСПЕРТ = """сценарии:
- имя: проба
  улики: [{улика}]
  состояние: {состояние}
  больно: b
  ломается: l
  доказать: cmd
  журнал: {журнал}
{исполнитель}"""

ИСПОЛНИТЕЛЬ = """  исполнитель:
    набор: {набор}
    метка: {метка}
"""

print("\n== ось расписывает свой проход: молчание названо причиной ==")

ОСИ_ДЕКЛ = ("оси:\n  жёсткие:\n"
            "    - заголовок: немая\n      читатель: немая\n"
            "    - заголовок: говорящая\n      читатель: говорящая\n"
            "    - заголовок: сказала чисто\n      читатель: сказала_чисто\n"
            "    - заголовок: сказала и промолчала\n      читатель: сказала_и_промолчала\n"
            "    - заголовок: чистая\n      читатель: чистая\n"
            "    - заголовок: глубоко\n      читатель: глубоко\n")

ОСИ_КОД = (
    "def немая(root=1):\n"
    "    if not root:\n"
    "        return []\n"
    "    return ['нашла']\n"
    "def говорящая(root=1):\n"
    "    if not root:\n"
    "        raise НетУлики('источника нет')\n"
    "    return []\n"
    "def сказала_чисто(root=1):\n"
    "    if not root:\n"
    "        return чисто()\n"
    "    return ['нашла']\n"
    "def сказала_и_промолчала(root=1):\n"
    "    if not root:\n"
    "        raise НетУлики('источника нет')\n"
    "    if root == 2:\n"
    "        return []\n"
    "    return ['нашла']\n"
    "def чистая(root=1):\n"
    "    x = root\n"
    "    return []\n"
    "def глубоко(root=1):\n"
    "    for x in root:\n"
    "        try:\n"
    "            return []\n"
    "        except Exception:\n"
    "            pass\n"
    "    return ['нашла']\n"
    "def не_ось(root=1):\n"
    "    if not root:\n"
    "        return []\n"
    "    return ['нашла']\n")

def дерево_осей(код: str = ОСИ_КОД, декл: str = ОСИ_ДЕКЛ) -> Path:
    return make({"scripts/guards/invariants.py": код, "scripts/guards/evidence.yaml": декл})

ok(not axis_undeclared(дерево_осей()),
   "объявленные оси претензий не вызывают — сверяются оба списка, а не один")

ok("нет invariants.py" in отказ(axis_undeclared, make({"README.md": "x"})),
   "ось, судящая объявление осей, тоже не выдаёт отсутствие файлов за чистоту")

print("\n== ссылка сценария: неразобранная НЕ уезжает в команду текстом ==")

sys.path.insert(0, str(ROOT))

from tests.harness.scenario import _resolve, ScenarioError  # путь к корню

# добавлен строкой выше: подстановку ссылок судим у самой машины сценариев
try:
    _resolve("--root ${кириллица.path}", {"кириллица": {"path": "/tmp"}})
    ok(False, "ссылка не по форме имени — отказ объявления, а не путь-строка в команде")
except ScenarioError as сбой:
    ok("не разобрана" in str(сбой),
       "ссылка не по форме имени — отказ объявления, а не путь-строка в команде")

ok(_resolve("--root ${live.path}", {"live": {"path": "/tmp/дерево"}}) == "--root /tmp/дерево",
   "разобранная ссылка подставляется как была — правило не мешает обычной работе")

if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)

print("ВСЁ ЗЕЛЁНОЕ ✅")

