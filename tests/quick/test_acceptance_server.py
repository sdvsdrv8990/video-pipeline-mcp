"""
tests/quick/test_acceptance_server.py — приёмка правки в дереве сервера, обе стороны.

Standalone-прогон:  python tests/quick/test_acceptance_server.py
Проверяет `scripts/guards/acceptance_server.py` на временных деревьях: шапка модуля, объявленный
каталог, роды верхнего уровня, словарь пустых имён, звёздный импорт и стирающий алиас — каждая
проверка ловит своё нарушение и молчит на чистом; отчёт `ruff` о чистоте находкой не считается.
"""
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
GUARDS = ROOT / "scripts" / "guards"
sys.path.insert(0, str(GUARDS))
sys.dont_write_bytecode = True


def _load(path: Path, name: str):
    """Компиляция ИЗ ИСХОДНИКА: кэш байткода признаёт свежим .pyc при том же размере и секунде."""
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


общее = _load(GUARDS / "_acceptance.py", "_acceptance")
sys.modules["_acceptance"] = общее
приёмка = _load(GUARDS / "acceptance_server.py", "acceptance_server")

_checks = 0
_fails = []

ШАПКА = '"""core/движок/узел.py — роль узла."""\n'
ОБЪЯВЛЕНИЕ = """каталоги:
  "core/*":
    роль: бизнес-логика ядра
    можно: [класс, функция, константа]
    нужна_шапка: true
имена:
  запрещённые: [utils, helpers, data]
"""


def ok(cond, msg, detail=None):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
    print(f"  {'✓' if cond else '✗'} {msg}")
    if detail:
        print(f"      · {detail}")


def дерево(файлы: dict[str, str], объявление: str = ОБЪЯВЛЕНИЕ) -> Path:
    """Временный репозиторий: сторож берёт цели у git, значит и дерево обязано быть под git."""
    корень = Path(tempfile.mkdtemp(prefix="vpm_accsrv_"))
    for путь, текст in файлы.items():
        цель = корень / путь
        цель.parent.mkdir(parents=True, exist_ok=True)
        цель.write_text(текст, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=корень, check=True)
    subprocess.run(["git", "add", "-A"], cwd=корень, check=True, capture_output=True)
    (корень / "structure.yaml").write_text(объявление, encoding="utf-8")
    приёмка.STRUCTURE = корень / "structure.yaml"
    return корень


def main() -> int:
    print("\n=== П5: файл объявляет свою зону шапкой ===")
    чистое = {"core/движок/узел.py": ШАПКА + "КОНСТАНТА = 1\n"}
    ok(not приёмка.header(root=дерево(чистое)), "чистое дерево: шапка называет файл — молчим")
    ok(any("не называет его самого" in note
           for note in приёмка.header(root=дерево({"core/движок/узел.py": "КОНСТАНТА = 1\n"}))),
       "файл без шапки: зоны ответственности у него нет, и это сказано прямо")
    ok(any("не называет его самого" in note for note in приёмка.header(
           root=дерево({"core/движок/узел.py": '"""Роль без имени файла."""\n'}))),
       "шапка есть, но себя не называет — объявление не о нём")
    ok(not приёмка.header(root=дерево({"core/движок/__init__.py": '"""core/движок — пакет."""\n'})),
       "у пакета зону объявляет имя ПАКЕТА, а не `__init__`: обвинять его было бы враньём")

    print("\n=== П6: структура дерева и роды верхнего уровня ===")
    ok(not приёмка.place(root=дерево(чистое)), "чистое дерево: файл в объявленном каталоге")
    ok(any("каталог не объявлен" in note
           for note in приёмка.place(root=дерево({"склад/быстро.py": ШАПКА}))),
       "файл в необъявленном каталоге: правило «что здесь можно» к нему не применяется вовсе")
    ok(any("определён род «тест»" in note for note in приёмка.place(
           root=дерево({"core/движок/узел.py": ШАПКА + "def test_быстро():\n    pass\n"}))),
       "тест внутри ядра: род каталогу не разрешён")
    ok(not приёмка.place(root=дерево({"core/движок/узел.py": ШАПКА + "class Узел:\n    pass\n"})),
       "класс в ядре — ровно то, за что каталог отвечает")

    print("\n=== П7: имена ===")
    ok(not приёмка.naming(root=дерево(чистое)), "чистое дерево: имена честные")
    ok(any("не говорит ни о чём" in note and "utils" in note
           for note in приёмка.naming(root=дерево({"core/движок/utils.py": ШАПКА}))),
       "файл `utils` — имя ни о чём: словарь объявлен, а не выдуман на месте")
    ok(any("звёздный импорт" in note for note in приёмка.naming(
           root=дерево({"core/движок/узел.py": ШАПКА + "from os.path import *\n"}))),
       "звёздный импорт: что именно пришло, не видно ни человеку, ни поиску")
    ok(any("имя стёрто на входе" in note for note in приёмка.naming(
           root=дерево({"core/движок/узел.py": ШАПКА + "import subprocess as s\n"}))),
       "однобуквенный алиас стирает имя на входе")
    ok(any("не говорит ни о чём" in note and "data" in note for note in приёмка.naming(
           root=дерево({"core/движок/узел.py": ШАПКА + "def data():\n    pass\n"}))),
       "функция с пустым именем названа так же, как файл")

    print("\n=== П11 и границы: чужие вердикты сводятся честно ===")
    ok(any("вердикт ruff" in note
           for note in приёмка.errors(root=дерево({"core/движок/узел.py": ШАПКА + "import os\n"}))),
       "неиспользованный импорт приезжает вердиктом ruff, а не своим разбором")
    ok(not приёмка.errors(root=дерево(чистое)),
       "на чистом дереве отчёт ruff о чистоте находкой не считается")
    зона = дерево({"core/движок/узел.py": ШАПКА, "server.py": ШАПКА})
    (зона / "лишний.py").write_text(ШАПКА, encoding="utf-8")
    ok(any("вне зоны задачи" in note for note in общее.outside(("core/*",), root=зона)),
       "тронутое вне зоны задачи названо и здесь — механизм границ общий у обеих приёмок")

    print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
    for fail in _fails:
        print(f"  ✗ {fail}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
