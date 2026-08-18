"""tests/quick/test_comment_guard.py — регрессия на дыры самого сторожа текста (F120–F127).

## Назначение
Сторож стоит в гейте, поэтому его слепота = ложный зелёный на всём репозитории. Каждая
проверка — мутация: подсовываем текст, который сторож ОБЯЗАН увидеть, и текст, который он
обязан пропустить. Номера находок — в самих проверках.

## Границы
Только детектор (`review`/`collect`), без храповика: потолок проверяет джоба `comment-guard`.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import comment_guard as G

_checks, _fails = 0, []


def ok(name, cond, detail=""):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {('- ' + str(detail)) if detail else ''}")


def notes_of(src):
    return G.review("проба.py", src)


JUNK = "\n".join(["    Строка разбора задачи номер %d." % i for i in range(20)])

# F125: префикс перед кавычками не снимает правило (ruff D301 сам советует `r` при слэшах).
# `f`/`F` — не докстринг для самого Python (голое выражение-строка), и это был отдельный канал.
for prefix in ('r', 'R', 'u', 'f', 'F', ''):
    src = 'def f():\n    %s"""Шапка.\n%s\n    """\n    return 1\n' % (prefix, JUNK)
    n = notes_of(src)
    ok(f"F125: текст с префиксом {prefix or '(без)'!r} виден сторожу", any("докстринг на" in x for x in n), n)

# Голая строка-выражение НЕ первой в теле — тоже текст в коде.
src = 'def f():\n    x = 1\n    """Эссе.\n%s\n    """\n    return x\n' % JUNK
ok("голая строка в середине тела — тоже текст", any("докстринг на" in x for x in notes_of(src)), notes_of(src))

# F123: закрывающая кавычка константы — не начало докстринга. В скобочной форме старый
# построчный разбор принимал её за открывающую и объявлял ДАННЫЕ докстрингом: маркер внутри
# данных становился ложным замечанием, а потолок такое замечание защищал как «принятый долг».
src = 'ШАБЛОН = (\n    """\n    прежде было так\n    """\n)\n'
ok("F123: маркер внутри строки-ДАННЫХ не считается замечанием", notes_of(src) == [], notes_of(src))
src = ('ШАБЛОН = (\n    """\n    текст данных\n    """\n)\n\n'
       'def f():\n    """Док.\n%s\n    """\n    return 1\n' % JUNK)
n = notes_of(src)
ok("F123: длинный докстринг ПОСЛЕ строки-константы всё равно найден", any("докстринг на" in x for x in n), n)

# F124: `#` внутри строки-данных — не комментарий.
src = 'ДАННЫЕ = """\n' + "\n".join(f"# строка данных {i}" for i in range(8)) + '\n"""\n'
ok("F124: восемь `#` внутри строки-данных не считаются прогоном комментариев", notes_of(src) == [], notes_of(src))
src = "\n".join(f"# рассуждение {i}" for i in range(8)) + "\nx = 1\n"
ok("F124: восемь настоящих `#` подряд — замечание", any("подряд" in x for x in notes_of(src)), notes_of(src))

# F126: пересказ декларации прозой (`ключ: значение`) — главный класс мусора.
src = ('class C:\n    """Роль.\n\n    Attributes:\n        a: первое поле\n        b: второе поле\n'
       '        c: третье поле\n        d: четвёртое поле\n    """\n    a: int\n')
ok("F126: блок из четырёх «ключ: значение» пойман", any("пересказ декларации" in x for x in notes_of(src)),
   notes_of(src))
src = 'def f():\n    """Роль.\n\n    Возврат: число.\n    """\n    return 1\n'
ok("F126: одна строка «ключ: значение» замечанием не стала", notes_of(src) == [], notes_of(src))

# F122: цели замера = всё, что под git, а не фиксированный список.
tracked = {p for p in subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"],
                                     capture_output=True, text=True, check=True).stdout.split() if p}
ok("F122: замер покрывает ровно `git ls-files *.py`", set(G.collect()) == tracked,
   sorted(tracked ^ set(G.collect()))[:5])

# F127: путь вне репозитория — отчёт, а не сырой ValueError.
with tempfile.TemporaryDirectory() as tmp:
    alien = Path(tmp) / "чужой.py"
    alien.write_text('def f():\n    """Док.\n%s\n    """\n' % JUNK, encoding="utf-8")
    try:
        out = G.collect([str(alien)])
        ok("F127: файл вне репозитория просканирован без падения", any(out.values()), out)
    except ValueError as e:
        ok("F127: файл вне репозитория просканирован без падения", False, f"{type(e).__name__}: {e}")

# F120: сторож не ловит сам себя — таблица шаблонов внутри него это КОД, а не текст.
guard_src = (ROOT / "scripts" / "comment_guard.py").read_text(encoding="utf-8")
ok("F120: сторож на себе самом чист", G.review("scripts/comment_guard.py", guard_src) == [],
   G.review("scripts/comment_guard.py", guard_src))

# Слепое пятно обязано быть слышным: неразбираемый файл = замечание, а не тишина.
ok("неразбираемый файл даёт замечание, а не молчание", notes_of("def f(:\n  pass\n") != [],
   notes_of("def f(:\n  pass\n"))

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
