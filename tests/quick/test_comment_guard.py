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
sys.path.insert(0, str(ROOT / "scripts" / "guards"))

import comment_guard as G

_checks, _fails = 0, []


def ok(name, cond, detail=""):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(name)
    # Форму строки РАЗБИРАЕТ цикл `what_if.py`, сравнивая цвет проверки до и после правки:
    # свой диалект не ломает набор, но молча выносит все его проверки из сравнения.
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  → {detail}" if detail else ""))


def notes_of(src):
    return G.review("проба.py", src)


JUNK = "\n".join(["    Строка разбора задачи номер %d." % i for i in range(20)])

# Префикс перед кавычками не снимает правило (ruff D301 сам советует `r` при слэшах).
# `f`/`F` — не докстринг для самого Python (голое выражение-строка), и это был отдельный канал.
for prefix in ('r', 'R', 'u', 'f', 'F', ''):
    src = 'def f():\n    %s"""Шапка.\n%s\n    """\n    return 1\n' % (prefix, JUNK)
    n = notes_of(src)
    ok(f"текст с префиксом{prefix or '(без)'!r} виден сторожу", any("докстринг на" in x for x in n), n)

# Голая строка-выражение НЕ первой в теле — тоже текст в коде.
src = 'def f():\n    x = 1\n    """Эссе.\n%s\n    """\n    return x\n' % JUNK
ok("голая строка в середине тела — тоже текст", any("докстринг на" in x for x in notes_of(src)), notes_of(src))

# Закрывающая кавычка константы — не начало докстринга. В скобочной форме старый
# построчный разбор принимал её за открывающую и объявлял ДАННЫЕ докстрингом: маркер внутри
# данных становился ложным замечанием, а потолок такое замечание защищал как «принятый долг».
src = 'ШАБЛОН = (\n    """\n    прежде было так\n    """\n)\n'
ok("маркер внутри строки-ДАННЫХ не считается замечанием", notes_of(src) == [], notes_of(src))
src = ('ШАБЛОН = (\n    """\n    текст данных\n    """\n)\n\n'
       'def f():\n    """Док.\n%s\n    """\n    return 1\n' % JUNK)
n = notes_of(src)
ok("длинный докстринг ПОСЛЕ строки-константы всё равно найден", any("докстринг на" in x for x in n), n)

# `#` внутри строки-данных — не комментарий.
src = 'ДАННЫЕ = """\n' + "\n".join(f"# строка данных {i}" for i in range(8)) + '\n"""\n'
ok("восемь `#` внутри строки-данных не считаются прогоном комментариев", notes_of(src) == [], notes_of(src))
src = "\n".join(f"# рассуждение {i}" for i in range(8)) + "\nx = 1\n"
ok("восемь настоящих `#` подряд — замечание", any("подряд" in x for x in notes_of(src)), notes_of(src))

# Пересказ декларации прозой (`ключ: значение`) — главный класс мусора.
src = ('class C:\n    """Роль.\n\n    Attributes:\n        a: первое поле\n        b: второе поле\n'
       '        c: третье поле\n        d: четвёртое поле\n    """\n    a: int\n')
ok("блок из четырёх «ключ: значение» пойман", any("пересказ декларации" in x for x in notes_of(src)),
   notes_of(src))
src = 'def f():\n    """Роль.\n\n    Возврат: число.\n    """\n    return 1\n'
ok("одна строка «ключ: значение» замечанием не стала", notes_of(src) == [], notes_of(src))

# Цели замера = всё, что под git, а не фиксированный список. Глобы берутся У СТОРОЖА: копия
# здесь разошлась с ним молча и покраснела только на первом `.tsx` в дереве.
_globs = G.TARGET_GLOBS
# `-z` тем же способом, что и сторож: без него git ЭКРАНИРУЕТ не-ASCII путь, и сравнение
# множеств краснеет на первом же кириллическом имени файла.
tracked = {p for p in subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", *_globs],
                                     capture_output=True, text=True, check=True).stdout.split("\0") if p}
ok("замер покрывает ровно объявленные цели под git", set(G.collect()) == tracked,
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

# Снесённый файл git числит до индексации — обычное состояние дерева посреди правки.
_real_tracked = G._tracked
try:
    G._tracked = lambda: [ROOT / "server.py", ROOT / "core" / "снесённого-нет.py"]
    try:
        _out = G.collect()
        ok("git числит снесённое — сторож даёт вердикт, а не трейс", set(_out) == {"server.py"}, sorted(_out))
    except FileNotFoundError as e:
        ok("git числит снесённое — сторож даёт вердикт, а не трейс", False, f"FileNotFoundError: {e}")
    G._tracked = lambda: [ROOT / "core" / "снесённого-нет.py"]
    try:
        G.collect()
        ok("на диске не осталось НИ ОДНОЙ цели — это отказ, а не пустой чистый замер", False, "молчание")
    except SystemExit:
        ok("на диске не осталось НИ ОДНОЙ цели — это отказ, а не пустой чистый замер", True)
finally:
    G._tracked = _real_tracked

# Путь вне репозитория — отчёт, а не сырой ValueError.
with tempfile.TemporaryDirectory() as tmp:
    alien = Path(tmp) / "чужой.py"
    alien.write_text('def f():\n    """Док.\n%s\n    """\n' % JUNK, encoding="utf-8")
    try:
        out = G.collect([str(alien)])
        ok("файл вне репозитория просканирован без падения", any(out.values()), out)
    except ValueError as e:
        ok("файл вне репозитория просканирован без падения", False, f"{type(e).__name__}: {e}")

# Сторож не ловит сам себя — таблица шаблонов внутри него это КОД, а не текст.
guard_src = (ROOT / "scripts" / "guards" / "comment_guard.py").read_text(encoding="utf-8")
ok("сторож на себе самом чист", G.review("scripts/guards/comment_guard.py", guard_src) == [],
   G.review("scripts/guards/comment_guard.py", guard_src))

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

print("== Правило не зависит от стека: маркеры знает лексер, а не наш список ==")
# Ни один из этих языков в коде сторожа не назван — их приносит библиотека. Если бы список
# маркеров вели мы, каждый новый стек требовал бы правки, а до неё молчал бы «чисто».
for _name, _src in {
    "a.rs": "// D42: раст\nfn main() {}\n",
    "a.go": "// D42: го\nfunc main() {}\n",
    "a.css": "/* D42: стиль */\n.a { color: #fff; }\n",
    "a.sql": "-- D42: скуль\nSELECT 1;\n",
    "a.vue": "<!-- D42: разметка -->\n<template></template>\n",
}.items():
    ok(f"координата задачи поймана в {_name}", G.review(_name, _src) != [], G.review(_name, _src))

print("== Фронтенд: сторож видит комментарии TS/TSX, а не молчит на них ==")
_TSX = "studio/src/Card.tsx"
_tsx_tag = 'export const Card = () => {\n  // D42: временно, потом вынести\n  return null;\n};\n'
ok("координата задачи в `//` поймана", G.review(_TSX, _tsx_tag) != [], G.review(_TSX, _tsx_tag))
_tsx_block = "/*\n * D42: разбор задачи на много строк\n */\nexport const A = 1;\n"
ok("координата задачи в блоке `/* */` поймана", G.review(_TSX, _tsx_block) != [],
   G.review(_TSX, _tsx_block))
# Ложное срабатывание страшнее пропуска: храповик заморозил бы его как принятый долг.
_tsx_clean = 'const url = "https://x/y"; // адрес витрины\nconst re = /a\\/b/;\n'
ok("слэши в строке и регулярке не считаются комментарием", G.review(_TSX, _tsx_clean) == [],
   G.review(_TSX, _tsx_clean))
# Решётка в TSX комментарием НЕ является — иначе CSS-цвет читался бы как комментарий.
ok("решётка в TSX не принимается за комментарий",
   G.review(_TSX, 'const c = "#D42FFF";\n') == [], G.review(_TSX, 'const c = "#D42FFF";\n'))
# И обратно: в yaml маркером остаётся решётка, слэши — нет.
ok("в yaml слэши не стали комментарием", G.review("a.yaml", "// D42\nkey: 1\n") == [],
   G.review("a.yaml", "// D42\nkey: 1\n"))

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
