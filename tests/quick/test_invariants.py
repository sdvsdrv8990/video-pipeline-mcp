"""
tests/quick/test_invariants.py — сторож межфайловых инвариантов проверяется на временных каталогах.

Standalone-прогон:  python tests/quick/test_invariants.py
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

from invariants import (  # noqa: E402
    codes_outside_registry, codes_without_emitter, dead_recovery_in_engine,
    declared_but_unscripted, default_instead_of_declaration, enum_without_values,
    facts_exempt_from_observation, facts_outside_registry, facts_without_emitter,
    facts_without_observer, observation_incomplete,
    guard_without_home, hooks_off_declaration, knob_without_reader, zone_declared_twice,
    resources_off_inventory, scenario_calls_unknown_tool, skips_without_ci,
    ci_jobs_off_docs, dispatch_by_value, guards_off_catalog, mirrored_declaration,
    module_without_reader,
    status_off_registry,
    suites_off_catalog,
    unfinished_in_server,
    used_before_declared,
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


CI_INSTALLS = """
jobs:
  test:
    steps:
      - run: sudo apt-get install -y ffmpeg
"""
CI_COMMENT_ONLY = """
jobs:
  test:
    steps:
      # ffmpeg тут только упомянут, но не ставится
      - run: sudo apt-get install -y libreoffice-calc
"""
CI_REQUIRED = """
jobs:
  test:
    steps:
      - run: python -m pytest
        env:
          VPM_FFMPEG_REQUIRED: "1"
"""
SUITE_SKIPS = 'import os, shutil\nif not shutil.which("ffmpeg"):\n    print("пропуск")\n'
SUITE_REQUIRED = (SUITE_SKIPS + 'if os.environ.get("VPM_FFMPEG_REQUIRED") == "1":\n    raise SystemExit(1)\n')

print("== пропуск набора без покрытия в CI ==")
ok(not skips_without_ci(make({"tests/test_a.py": SUITE_SKIPS, ".github/workflows/ci.yml": CI_INSTALLS})),
   "бинарь ставится командой джобы — молчим")
ok(not skips_without_ci(make({"tests/test_a.py": SUITE_REQUIRED, ".github/workflows/ci.yml": CI_REQUIRED})),
   "бинарь не ставится, но пропуск запрещён флагом — молчим")
ok(len(skips_without_ci(make({"tests/test_a.py": SUITE_SKIPS, ".github/workflows/ci.yml": CI_COMMENT_ONLY}))) == 1,
   "бинарь назван лишь В КОММЕНТАРИИ — это объяснение, а не механизм: находка")
ok(len(skips_without_ci(make({"tests/test_a.py": SUITE_SKIPS,
                              ".github/workflows/ci.yml": "jobs:\n  test:\n    steps:\n      - run: pytest\n"}))) == 1,
   "ни установки, ни флага — находка")

print("\n== имя используется до объявления ==")
ok(len(used_before_declared(make({"tests/test_b.py": "print(поздно)\nпоздно = 1\n"}))) == 1,
   "имя объявлено НИЖЕ места использования — находка")
CLEAN = '''import contextlib
import sys


def позже_объявленная():
    return ХВОСТ            # функция вправе смотреть на глобаль, объявленную ниже: тело ждёт вызова


def приёмник(self, name, *args, **kw):
    return (self, name, args, kw)


with contextlib.nullcontext(1) as первый, contextlib.nullcontext(первый) as второй:
    print(второй)

for элемент in (1, 2):
    print(элемент)

try:
    raise ValueError("x")
except ValueError as ошибка:
    print(ошибка)

ХВОСТ = [значение for значение in range(3)]
sys.exit(0)
'''
ok(not used_before_declared(make({"tests/test_c.py": CLEAN})),
   "параметры функций, `with ... as`, `for`, `except ... as` и взгляд функции вперёд — не находки")

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
RES_CLASS = "default: inline\nclasses:\n  inline: {offload: false}\ntools:\n  media_generate: gpuu\n"
RES_DEFAULT = "default: inlien\nclasses:\n  inline: {offload: false}\ntools: {}\n"
INV_PATH = "tests/quick/tools_inventory.golden.json"
ok(not resources_off_inventory(make({"config/resources.yaml": RES_OK, INV_PATH: INV})),
   "имена и классы сходятся — молчим")
ok(len(resources_off_inventory(make({"config/resources.yaml": RES_GHOST, INV_PATH: INV}))) == 1,
   "инструмент переименован — строка указывает в пустоту, снятие с цикла молча не действует")
ok(len(resources_off_inventory(make({"config/resources.yaml": RES_CLASS, INV_PATH: INV}))) == 1,
   "класс не объявлен — вызов тихо падает в default, а сервер снова морозится")
ok(len(resources_off_inventory(make({"config/resources.yaml": RES_DEFAULT, INV_PATH: INV}))) == 1,
   "опечатка в `default` — умолчание указывает в несуществующий класс")
ok(not resources_off_inventory(make({"config/x.yaml": "y: 1"})),
   "объявления нет вовсе — не выдумываем нарушение")
RES_NOLIMIT = "default: inline\nclasses:\n  inline: {offload: false}\n  gpu: {offload: true}\ntools:\n  media_generate: gpu\n"
ok(len(resources_off_inventory(make({"config/resources.yaml": RES_NOLIMIT, INV_PATH: INV}))) == 1,
   "класс снимается с цикла без предела — сто вызовов поднимут сто потоков")

print("\n== статус находки мимо реестра ==")
REG = "| ~~F1~~ | ✅ | закрыта |\n| F2 | 🟠 | открыта |\n"
ok(not status_off_registry(make({"docs/roadmap/02_findings.md": REG,
                                 "docs/roadmap/plan.md": "шаг зависит от F2 🟠 и ждёт"})),
   "план повторяет статус ВЕРНО — молчим")
ok(len(status_off_registry(make({"docs/roadmap/02_findings.md": REG,
                                 "docs/roadmap/plan.md": "шаг упирается в F1 🔴 — узкое место"}))) == 1,
   "план показывает закрытую находку открытой — копия разъехалась с хозяином")
ok(not status_off_registry(make({"docs/roadmap/02_findings.md": REG,
                                 "docs/roadmap/_sessions.md": "тогда F1 был 🔴 и блокировал"})),
   "журнал сессий хранит ПРЕЖНИЙ статус — это история, а не расхождение")
ok(not status_off_registry(make({"docs/roadmap/02_findings.md": REG,
                                 "docs/roadmap/plan.md": "смотри F1 и F2 — статуса тут нет"})),
   "упоминание находки без статуса — не утверждение, молчим")

print("\n== набор мимо каталога зон ==")
# Гейт кладётся в корень НАСТОЯЩИМ файлом: сторож обязан считать наборы тем же кодом, что и гейт,
# иначе списки разъедутся молча — а именно это он и стережёт.
GATE_SRC = (ROOT / "tests" / "test_suites.py").read_text(encoding="utf-8")
ZONE_ROW = "| Тест | Зона |\n|---|---|\n| `test_a.py` | зона A |\n"

ok(not suites_off_catalog(make({"tests/test_suites.py": GATE_SRC,
                                "tests/quick/test_a.py": "x = 1\n",
                                "tests/CATALOG.md": ZONE_ROW})),
   "набор объявлен строкой-зоной — молчим")
ok(len(suites_off_catalog(make({"tests/test_suites.py": GATE_SRC,
                                "tests/quick/test_a.py": "x = 1\n",
                                "tests/quick/test_b.py": "x = 1\n",
                                "tests/CATALOG.md": ZONE_ROW}))) == 1,
   "новый набор без зоны — находка: так тесты и плодятся")
ok(len(suites_off_catalog(make({"tests/test_suites.py": GATE_SRC,
                                "tests/quick/test_a.py": "x = 1\n",
                                "tests/quick/test_b.py": "x = 1\n",
                                "tests/CATALOG.md": ZONE_ROW + "\nрой уходит в `test_b.py` когда-нибудь\n"}))) == 1,
   "имя названо в ПРОЗЕ, а не строкой таблицы — зоной не считается (сторож смотрит на вещь, не на след)")
ok(len(suites_off_catalog(make({"tests/test_suites.py": GATE_SRC,
                                "tests/CATALOG.md": ZONE_ROW}))) == 1,
   "зона объявлена, а набора нет — расширять предлагается пустоту")
ok(not suites_off_catalog(make({"tests/quick/test_a.py": "x = 1\n", "tests/CATALOG.md": ZONE_ROW})),
   "гейта в корне нет — не выдумываем нарушение")
ok(not suites_off_catalog(make({"tests/test_suites.py": GATE_SRC,
                                "tests/sim_zone/test_c.py": "x = 1\n",
                                "tests/CATALOG.md": "| Набор | Зона |\n|---|---|\n| `sim_zone/` | зона C |\n"})),
   "набор-каталог объявлен ключом `dir/` — молчим")
ok(len(suites_off_catalog(make({"tests/test_suites.py": GATE_SRC,
                                "tests/quick/test_a.py": "x = 1\n",
                                "tests/CATALOG.md": ZONE_ROW + "| `снесённый/` | зона D |\n"}))) == 1,
   "зона-каталог объявлена, а каталога нет — снесённый набор числится живым")
ok(len(suites_off_catalog(make({"tests/test_suites.py": GATE_SRC,
                                "tests/quick/test_a.py": "x = 1\n",
                                "tests/снесённый/__pycache__/старое.pyc": "мусор",
                                "tests/CATALOG.md": ZONE_ROW + "| `снесённый/` | зона D |\n"}))) == 1,
   "каталог зоны жив мусором (`__pycache__`), наборов в нём нет — зона всё равно мертва")
ok(not suites_off_catalog(make({"tests/test_suites.py": GATE_SRC,
                                "tests/harness/test_helper.py": "x = 1\n",
                                "tests/CATALOG.md": "| Тест | Зона |\n|---|---|\n| `нет` | — |\n"})),
   "харнесс — библиотека, а не набор: гейт его пропускает, сторож обязан пропустить тоже")

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


print("\n== сторож мимо каталога зон ==")
GUARD_ROW = "| `alpha.py` | зона | улика | запас | никогда |\n"
ok(not guards_off_catalog(make({"scripts/guards/alpha.py": "x = 1\n",
                                "scripts/guards/CATALOG.md": GUARD_ROW})),
   "сторож объявлен строкой-зоной — молчим")
ok(len(guards_off_catalog(make({"scripts/guards/alpha.py": "x = 1\n",
                                "scripts/guards/beta.py": "y = 2\n",
                                "scripts/guards/CATALOG.md": GUARD_ROW}))) == 1,
   "новый скрипт без зоны — так сторожа и плодятся мимо запаса существующих")
ok(len(guards_off_catalog(make({"scripts/guards/CATALOG.md": GUARD_ROW}))) == 1,
   "зона объявлена, а сторожа нет — расширять предлагается пустоту")
ok(len(guards_off_catalog(make({"scripts/guards/alpha.py": "x = 1\n"}))) == 1,
   "каталога зон нет вовсе — правило «не плодить» держится дисциплиной, то есть не держится")
ok(not guards_off_catalog(make({"core/z.py": "x = 1\n"})),
   "каталога сторожей в дереве нет — событие не наше, молчим")
ok(not guards_off_catalog(make({"scripts/guards/alpha.py": "x = 1\n",
                                "scripts/guards/__init__.py": "\n",
                                "scripts/guards/CATALOG.md": GUARD_ROW})),
   "служебный `__init__.py` сторожем не считается и зоны не требует")

ok(not guards_off_catalog(make({"scripts/guards/потолок.txt": "3\n"})),
   "в каталоге сторожей нет ни одного скрипта — обвинять некого, каталог зон не нужен")

print("\n== число джоб гейта мимо ci.yml ==")
CI2 = "jobs:\n  lint:\n    runs-on: x\n  test:\n    runs-on: x\n"
REGISTRY = "| [`ход.md`](ход.md) | Журнал сплошного прохода |\n"
ok(len(ci_jobs_off_docs(make({".github/workflows/ci.yml": CI2,
                              "docs/roadmap/a.md": "гейт — **6 джоб**\n"}))) == 1,
   "документ называет число, которого в ci.yml нет — копия состарилась молча")
ok(not ci_jobs_off_docs(make({".github/workflows/ci.yml": CI2,
                              "docs/roadmap/a.md": "гейт — две джоб\n"})),
   "словом сказанное число тоже читается, и верное не обвиняется")
ok(not ci_jobs_off_docs(make({".github/workflows/ci.yml": CI2,
                              "docs/roadmap/README.md": REGISTRY,
                              "docs/roadmap/ход.md": "тогда было 8 джоб\n"})),
   "журнал по реестру ролей — запись о прошлом, её не переписывают")
ok(len(ci_jobs_off_docs(make({".github/workflows/ci.yml": CI2,
                              "docs/roadmap/ход.md": "тогда было 8 джоб\n"}))) == 1,
   "тот же файл БЕЗ строки в реестре журналом не считается — послабление не выдаётся по имени")
ok(not ci_jobs_off_docs(make({"docs/roadmap/a.md": "**6 джоб**\n"})),
   "ci.yml нет — сверять не с чем, молчим")

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

print("\n== хук против своего объявления ==")
DECL = ('{"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", '
        '"command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/vpm-x.py"}]}]}}\n')

ok(len(hooks_off_declaration(make({".claude/settings.json": DECL}))) == 1,
   "объявление есть, файла нет — событие приходит, запускать нечего")
ok(len(hooks_off_declaration(make({".claude/hooks/vpm-x.py": "x = 1\n"}))) == 1,
   "файл есть, объявления нет — код лежит, срабатывать ему не на чем")
ok(not hooks_off_declaration(make({".claude/settings.json": DECL,
                                   ".claude/hooks/vpm-x.py": "x = 1\n"})),
   "обе половины на месте — молчим")
ok(not hooks_off_declaration(make({"core/x.py": "x = 1\n"})),
   "ни хуков, ни объявления — событие не наше")
ok(len(hooks_off_declaration(make({".claude/settings.json": "{ сломано\n",
                                   ".claude/hooks/vpm-x.py": "x = 1\n"}))) == 1,
   "сломанный JSON назван ОДНОЙ причиной, а не обвинением каждого файла в дереве")

print("\n== одну зону объявили два хозяина ==")
HEAD_ROW = "| Тест | Зона |\n|---|---|\n"
ok(not zone_declared_twice(make({"tests/CATALOG.md": HEAD_ROW
                                 + "| `test_a.py` | зона про поиск |\n| `test_b.py` | зона про таблицы |\n"})),
   "у каждой строки своя территория — молчим")
ok(len(zone_declared_twice(make({"tests/CATALOG.md": HEAD_ROW
                                 + "| `test_a.py` | зона про поиск |\n| `test_b.py` | зона про поиск |\n"}))) == 1,
   "две строки объявили дословно одну зону — второй хозяин и есть размножение")
ok(len(zone_declared_twice(make({"tests/CATALOG.md": HEAD_ROW
                                 + "| `test_a.py` | сам сторож `scripts/guards/g.py`: отчёт |\n"
                                 + "| `test_b.py` | сам сторож `scripts/guards/g.py`: иначе сказано |\n"}))) == 1,
   "формулировки разные, а НАЗВАН один сторож — два дома у одной правды")
ok(not zone_declared_twice(make({"tests/CATALOG.md": HEAD_ROW
                                 + "| `test_a.py` | сам сторож `scripts/guards/g.py` |\n"
                                 + "| `test_b.py` | сам сторож `scripts/guards/o.py` |\n"})),
   "разные сторожа названы — территории не пересекаются")
ok(not zone_declared_twice(make({"scripts/guards/CATALOG.md": HEAD_ROW + "| `g.py` | своя зона |\n"})),
   "каталог сторожей судится тем же правилом и на одной строке молчит")
ok(not zone_declared_twice(make({"core/z.py": "x = 1\n"})),
   "каталогов нет вовсе — улики нет, а не «дублей нет»")

print("\n== сторож без набора-дома ==")
HOME = "| Тест | Зона |\n|---|---|\n| `test_g.py` | сам сторож `scripts/guards/g.py` |\n"
ok(len(guard_without_home(make({"scripts/guards/g.py": "x = 1\n", "tests/CATALOG.md": HOME}))) == 0,
   "дом объявлен зоной набора — цикл знает, чем сравнивать правку сторожа")
ok(len(guard_without_home(make({"scripts/guards/g.py": "x = 1\n", "scripts/guards/n.py": "y = 1\n",
                                "tests/CATALOG.md": HOME}))) == 1,
   "сторож есть, а набора-дома нет: его правку цикл сравнить не с чем")
TWO_HOMES = HOME + "| `test_o.py` | сам сторож `scripts/guards/o.py` |\n"
ok(len(guard_without_home(make({"scripts/guards/o.py": "x = 1\n", "tests/CATALOG.md": TWO_HOMES}))) == 1,
   "дом объявлен сторожу, которого на диске нет — карта цикла ведёт в пустоту")
ok(not guard_without_home(make({"scripts/guards/_src.py": "x = 1\n", "tests/CATALOG.md": HOME,
                                "scripts/guards/g.py": "x = 1\n"})),
   "источник улики (`_`) сторожем не считается: он не судит, дома ему не нужно")
ok(not guard_without_home(make({"scripts/guards/g.py": "x = 1\n"})),
   "каталога тестов нет вовсе — улики нет, а не «все сторожа бездомны»")

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
READS_ABSENT = 'x = cfg["limits"].get("max_retries", 3)\n'
ok(len(default_instead_of_declaration(make({"config/u.yaml": DECL,
                                            "core/z.py": NAMES + READS_ABSENT}))) == 1,
   "читают раздел декларации и спрашивают строку, которой в нём нет — значение живёт в коде")
ok(not default_instead_of_declaration(make({"config/u.yaml": "limits:\n  max_size: 10\n  max_retries: 3\n",
                                            "core/z.py": NAMES + READS_ABSENT})),
   "ключ объявлен — дефолт лишь страхует, правка конфига действует")
ok(not default_instead_of_declaration(make({"config/u.yaml": DECL, "core/z.py": READS_ABSENT})),
   "модуль не называет ни одной декларации — он их не грузит: это данные запроса, не конфигурация")
ok(not default_instead_of_declaration(make({"config/u.yaml": DECL,
                                            "core/z.py": NAMES + 'x = q["size"].get("min", 0)\n'})),
   "приёмник добыт НЕобъявленным ключом — раздел принадлежит запросу, а не декларации")
ok(not default_instead_of_declaration(make({"config/u.yaml": DECL,
                                            "core/z.py": NAMES + 'x = cfg["limits"].get("nested", {})\n'})),
   "дефолт не литерал — это спуск по декларации, а не ручка")
ok(not default_instead_of_declaration(make({"config/u.yaml": DECL,
                                            "core/z.py": '# грузим config/u.yaml\n' + READS_ABSENT})),
   "имя декларации в комментарии ничего не грузит — читателем модуль не становится")
ok(not default_instead_of_declaration(make({"core/z.py": NAMES + READS_ABSENT})),
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

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
