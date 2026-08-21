"""tests/quick/test_comment_guard.py — регрессия на дыры самого сторожа текста.

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

# Префикс перед кавычками не снимает правило (ruff D301 сам советует `r` при слэшах).
# `f`/`F` — не докстринг для самого Python (голое выражение-строка), и это был отдельный канал.
for prefix in ('r', 'R', 'u', 'f', 'F', ''):
    src = 'def f():\n    %s"""Шапка.\n%s\n    """\n    return 1\n' % (prefix, JUNK)
    n = notes_of(src)
    ok(f"F125: текст с префиксом {prefix or '(без)'!r} виден сторожу", any("докстринг на" in x for x in n), n)

# Голая строка-выражение НЕ первой в теле — тоже текст в коде.
src = 'def f():\n    x = 1\n    """Эссе.\n%s\n    """\n    return x\n' % JUNK
ok("голая строка в середине тела — тоже текст", any("докстринг на" in x for x in notes_of(src)), notes_of(src))

# Закрывающая кавычка константы — не начало докстринга. В скобочной форме старый
# построчный разбор принимал её за открывающую и объявлял ДАННЫЕ докстрингом: маркер внутри
# данных становился ложным замечанием, а потолок такое замечание защищал как «принятый долг».
src = 'ШАБЛОН = (\n    """\n    прежде было так\n    """\n)\n'
ok("F123: маркер внутри строки-ДАННЫХ не считается замечанием", notes_of(src) == [], notes_of(src))
src = ('ШАБЛОН = (\n    """\n    текст данных\n    """\n)\n\n'
       'def f():\n    """Док.\n%s\n    """\n    return 1\n' % JUNK)
n = notes_of(src)
ok("F123: длинный докстринг ПОСЛЕ строки-константы всё равно найден", any("докстринг на" in x for x in n), n)

# `#` внутри строки-данных — не комментарий.
src = 'ДАННЫЕ = """\n' + "\n".join(f"# строка данных {i}" for i in range(8)) + '\n"""\n'
ok("F124: восемь `#` внутри строки-данных не считаются прогоном комментариев", notes_of(src) == [], notes_of(src))
src = "\n".join(f"# рассуждение {i}" for i in range(8)) + "\nx = 1\n"
ok("F124: восемь настоящих `#` подряд — замечание", any("подряд" in x for x in notes_of(src)), notes_of(src))

# Пересказ декларации прозой (`ключ: значение`) — главный класс мусора.
src = ('class C:\n    """Роль.\n\n    Attributes:\n        a: первое поле\n        b: второе поле\n'
       '        c: третье поле\n        d: четвёртое поле\n    """\n    a: int\n')
ok("F126: блок из четырёх «ключ: значение» пойман", any("пересказ декларации" in x for x in notes_of(src)),
   notes_of(src))
src = 'def f():\n    """Роль.\n\n    Возврат: число.\n    """\n    return 1\n'
ok("F126: одна строка «ключ: значение» замечанием не стала", notes_of(src) == [], notes_of(src))

# Цели замера = всё, что под git, а не фиксированный список.
_globs = ["*.py", "*.yaml", "*.yml", "*.toml", "*.sh", ".gitignore", ".env.example"]
tracked = {p for p in subprocess.run(["git", "-C", str(ROOT), "ls-files", *_globs],
                                     capture_output=True, text=True, check=True).stdout.split() if p}
ok("F122: замер покрывает ровно объявленные цели под git", set(G.collect()) == tracked,
   sorted(tracked ^ set(G.collect()))[:5])
ok("цели включают декларации и CI, а не только Python",
   any(p.endswith((".yaml", ".yml")) for p in tracked) and "pyproject.toml" in tracked)

# Не-Python цель: комментарий = строка целиком, значение с решёткой внутри — данные.
_yaml_src = "# Порог поднят (S24, F128).\nkey: значение\n"
ok("координата задачи поймана в декларации",
   any("координата задачи" in n for n in G.review("проба.yaml", _yaml_src)), G.review("проба.yaml", _yaml_src))
_yaml_ok = 'шаблон: "код D12 приходит от клиента"  # хвост после значения не разбираем\nkey: 1\n'
ok("решётка после значения в декларации не считается комментарием",
   G.review("проба.yaml", _yaml_ok) == [], G.review("проба.yaml", _yaml_ok))
ok("не-Python цель не падает разбором Python", G.review("проба.yml", "a: [1, 2\n") == [],
   G.review("проба.yml", "a: [1, 2\n"))

# Путь вне репозитория — отчёт, а не сырой ValueError.
with tempfile.TemporaryDirectory() as tmp:
    alien = Path(tmp) / "чужой.py"
    alien.write_text('def f():\n    """Док.\n%s\n    """\n' % JUNK, encoding="utf-8")
    try:
        out = G.collect([str(alien)])
        ok("F127: файл вне репозитория просканирован без падения", any(out.values()), out)
    except ValueError as e:
        ok("F127: файл вне репозитория просканирован без падения", False, f"{type(e).__name__}: {e}")

# Сторож не ловит сам себя — таблица шаблонов внутри него это КОД, а не текст.
guard_src = (ROOT / "scripts" / "comment_guard.py").read_text(encoding="utf-8")
ok("F120: сторож на себе самом чист", G.review("scripts/comment_guard.py", guard_src) == [],
   G.review("scripts/comment_guard.py", guard_src))

# ─── Координата задачи в тексте: ловится по классам, но НЕ ценой ложных срабатываний ───
# Строки-образцы держим здесь, в КОДЕ теста: те же образцы в комментарии сделали бы файл
# нарушителем собственного правила.
for _case, _src in (
        ("метка находки", "# D12/F106: спека требует валидировать заголовок.\nx = 1\n"),
        ("сессия", "# Reconcile (S24): пакет откачен целиком.\nx = 1\n"),
        ("сессия с буквой", 'def f():\n    """Адрес (S18-g): цепочка предков."""\n    return 1\n'),
        ("фаза плана", "# Ф2: регистрируем узел в реестре связей.\nx = 1\n"),
        ("мутация", "# Ассерт зелёный при вырезанном откате (мутация M113 это вскрыла).\nx = 1\n"),
        ("метка в шапке модуля", '"""проба.py — роль модуля (G17)."""\nx = 1\n')):
    ok(f"координата задачи поймана: {_case}",
       any("координата задачи" in n for n in notes_of(_src)), notes_of(_src))

for _case, _src in (
        ("адрес ячейки Excel", "# Диапазон `A2:D9` и ссылка `META!B2` считаются целиком.\nx = 1\n"),
        ("имя правила линтера", "# ruff D301 сам советует `r` при обратных слэшах.\nx = 1\n"),
        ("строковая константа", 'КОД = "D12"\nЕЩЁ = "S24"\n'),
        ("буквы внутри слова", "# Формула LOG10(x) и директива RUF100 — не координаты.\nx = 1\n"),
        ("шестнадцатеричный цвет", "# Заливка D9E1F2 берётся из стиля книги.\nx = 1\n")):
    ok(f"ложного срабатывания нет: {_case}", notes_of(_src) == [], notes_of(_src))

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
