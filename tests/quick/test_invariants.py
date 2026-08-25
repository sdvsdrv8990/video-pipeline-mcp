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
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from invariants import (  # noqa: E402
    codes_outside_registry, codes_without_emitter, declared_but_unscripted, enum_without_values,
    resources_off_inventory, scenario_calls_unknown_tool, skips_without_ci,
    dispatch_by_value, status_off_registry, suites_off_catalog, used_before_declared,
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

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
