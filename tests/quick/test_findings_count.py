"""
tests/quick/test_findings_count.py — судья читаемости реестра проверяется без реестра.

Standalone-прогон:  python tests/quick/test_findings_count.py
Проверяет: что считается строкой реестра и что закрытием, пол ловит ужавшийся разбор и молчит на
выросшем, отсутствие улики (нет реестра, нет пола, ноль строк) объявляется отдельно от «чисто»,
и разбор у реестра ОДИН — второй читатель дал бы второй вердикт.
"""
import io
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
GUARDS = ROOT / "scripts" / "guards"
sys.path.insert(0, str(GUARDS))

def _load(path: Path, name: str):
    """Компиляция ИЗ ИСХОДНИКА: кэш байткода признаёт свежим .pyc при том же размере и секунде."""
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


sys.dont_write_bytecode = True
fc = _load(GUARDS / "findings_count.py", "findings_count")
sys.modules["findings_count"] = fc            # соседу достаётся ТОТ ЖЕ свежий разбор, а не свой кэш
inv = _load(GUARDS / "invariants.py", "invariants")

_checks = 0
_fails = []


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
    print(f"  {'✓' if cond else '✗'} {msg}")


def run(registry: str | None, floor: str | None, *argv) -> tuple[int, str, str]:
    """Прогон судьи на подставленных файлах: настоящий реестр не трогается."""
    tmp = Path(tempfile.mkdtemp(prefix="vpm_findings_"))
    fc.REGISTRY = tmp / "02_findings.md"
    fc.FLOOR = tmp / "floor.txt"
    if registry is not None:
        fc.REGISTRY.write_text(registry, encoding="utf-8")
    if floor is not None:
        fc.FLOOR.write_text(floor, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    argv_backup = sys.argv
    sys.argv = ["findings_count.py", *argv]
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = fc.main()
    except Exception as exc:                      # падение судьи — это провал проверки, а не прогона
        code, err = -1, io.StringIO(f"судья упал: {type(exc).__name__}: {exc}\n")
    finally:
        sys.argv = argv_backup
    return code, out.getvalue(), err.getvalue()


HEAD = "| ID | severity | описание |\n|---|---|---|\n"


def main() -> int:
    print("§1 что считается строкой реестра")
    rows = fc.scan(HEAD + "| F1 | 🔴 | открытая |\n")
    ok(rows == {"F1": False}, f"обычная строка разобрана как открытая: {rows}")
    ok(fc.scan("| ~~F2~~ | 🟠 | закрыта зачёркиванием |") == {"F2": True},
       "зачёркнутый идентификатор — закрытие даже без галочки")
    ok(fc.scan("| **F3** | ✅ | жирный |") == {"F3": True},
       "жирный идентификатор виден, галочка прочитана")
    ok(fc.scan("| F4 | 🟢 | подтверждена мутацией |") == {"F4": True},
       "🟢 в колонке severity — тоже закрытие: реестр помечает им подтверждённые")
    ok(fc.scan("| F5 | 🔴→🟢 | стрелка перехода |") == {"F5": True},
       "переход в закрытое читается по итоговому знаку, а не по начальному")

    print("§2 что строкой реестра НЕ считается")
    ok(fc.scan("| F43 реестр реакций | мутация | тест | 🔴 краснеет ✅ |") == {},
       "строка таблицы мутаций не находка: в ячейке не только идентификатор")
    ok(fc.scan("Находка F99 упомянута прозой — и это не строка таблицы.") == {},
       "упоминание вне таблицы не считается")
    ok(fc.scan("| G7 | 🔴 | чужая серия |") == {}, "серия G реестром находок не считается")
    ok(fc.scan(HEAD) == {}, "шапка и разделитель строками не считаются")

    print("§3 повтор идентификатора: первая строка каноническая")
    twice = fc.scan("| F6 | 🔴 | канон: открыта |\n| F6 | ✅ | переформулировка ниже |")
    ok(twice == {"F6": False}, f"нижняя таблица не закрывает находку за канон: {twice}")
    ok(fc.scan("| F7 | ✅ | канон: закрыта |\n| F7 | 🔴 | переформулировка |") == {"F7": True},
       "и наоборот: канон закрыт — нижняя строка его не открывает")

    print("§4 пол ловит ужавшийся разбор")
    code, out, err = run(HEAD + "| F1 | 🔴 | одна |", "5", "--check")
    ok(code == 1, f"разобрано меньше пола — отказ (код {code})")
    ok("сломался разбор" in err, "названа ПРИЧИНА: не «закрыли находки», а сломался разбор")
    ok("status_off_registry" in err, "названо, кто ещё слепнет вместе со счётом")
    code, out, err = run(HEAD + "| F1 | 🔴 | одна |\n| F2 | ✅ | две |", "2", "--check")
    ok(code == 0, f"ровно пол — не отказ (код {code})")
    ok("читается машиной" in out, "молчание сказано вслух, а не пустым выводом")
    code, out, err = run(HEAD + "| F1 | 🔴 | a |\n| F2 | 🔴 | b |\n| F3 | 🔴 | c |", "2", "--check")
    ok(code == 0, "реестр вырос — отказа нет: находки не удаляются, только добавляются")

    print("§5 «улики нет» — отдельный исход, а не чистый результат")
    code, out, err = run(None, "2", "--check")
    ok(code == 2, f"реестра нет — код 2, а не 0 и не 1 (получено {code})")
    code, out, err = run(HEAD, "2", "--check")
    ok(code == 2, f"ни одной строки не разобрано — код 2 (получено {code})")
    ok("замер не состоялся" in err, "сказано, что замера не было, а не что реестр пуст")
    code, out, err = run(HEAD + "| F1 | 🔴 | одна |", None, "--check")
    ok(code == 2, f"файла пола нет — храповик молча не выключается (код {code})")
    ok("пол" in err.lower(), "названо, чего не хватает")
    code, out, err = run(HEAD + "| F1 | 🔴 | одна |", "не число", "--check")
    ok(code == 2, f"пол испорчен — код 2, а не питонов трейс (код {code})")

    print("§6 --bless поднимает пол и не притворяется проверкой")
    tmp = Path(tempfile.mkdtemp(prefix="vpm_findings_"))
    fc.REGISTRY, fc.FLOOR = tmp / "r.md", tmp / "floor.txt"
    fc.REGISTRY.write_text(HEAD + "| F1 | 🔴 | a |\n| F2 | 🔴 | b |", encoding="utf-8")
    fc.FLOOR.write_text("1\n", encoding="utf-8")
    argv_backup = sys.argv
    sys.argv = ["findings_count.py", "--check", "--bless"]
    with redirect_stdout(io.StringIO()):
        code = fc.main()
    sys.argv = argv_backup
    ok(code == 0 and fc.FLOOR.read_text(encoding="utf-8").strip() == "2",
       f"пол поднят до замера: {fc.FLOOR.read_text(encoding='utf-8').strip()}")
    code, out, err = run(HEAD + "| F1 | 🔴 | a |", "9", "--bless")
    ok(code == 0 and "9" not in (out + err),
       "--bless без --check не молчит и не судит по старому полу")

    print("§7 у реестра ОДИН разбор")
    ok(not hasattr(inv, "REGISTRY_ROW"),
       "в invariants нет второго разбора строк реестра: два читателя дают два вердикта")
    real = (ROOT / "docs" / "roadmap" / "02_findings.md").read_text(encoding="utf-8")
    mine = fc.scan(real)
    theirs = inv._registry_status(ROOT)
    ok(set(mine) == set(theirs), f"оба читателя видят один набор строк: {len(mine)} и {len(theirs)}")
    clash = sorted(f for f in set(mine) & set(theirs)
                   if ("закрыт" if mine[f] else "открыт") != theirs[f])
    ok(not clash, f"вердикт совпадает по каждой находке; расходятся: {clash}")

    print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
    for fail in _fails:
        print(f"  ✗ {fail}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
