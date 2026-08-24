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
    codes_outside_registry, enum_without_values, resources_off_inventory, skips_without_ci,
    status_off_registry, used_before_declared,
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

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
