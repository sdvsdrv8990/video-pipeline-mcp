"""scripts/guards/_tokens.py — источник улики: поток токенов кода без текста.

Зовут: text_only.

Не судит и кода возврата не даёт. Отвечает на один вопрос: изменилось ли в файле что-то, кроме
текста, — комментарии и докстринги из потока исключены, поэтому их правка поток не двигает. Правка
докстринга способна унести соседние операторы, и ни линтер, ни набор этого не видят.

Вычитаются именно ДОКСТРИНГИ, а не все строки: докстринг стоит первым выражением тела, значение —
где угодно ещё, и подмена литерала в коде обязана остаться правкой кода.
"""

from __future__ import annotations

import ast
import io
import tokenize

ПРОПУСК = (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
           tokenize.COMMENT, tokenize.ENCODING, tokenize.ENDMARKER)


def _докстринги(текст: str) -> set[tuple[int, int]]:
    """Начала докстрингов: (строка, колонка) — по ним токен-строка отличается от строки-значения."""
    try:
        дерево = ast.parse(текст)
    except SyntaxError:
        return set()
    места = set()
    for узел in ast.walk(дерево):
        тело = getattr(узел, "body", None)
        if not isinstance(тело, list) or not тело:
            continue
        первое = тело[0]
        if (isinstance(первое, ast.Expr) and isinstance(первое.value, ast.Constant)
                and isinstance(первое.value.value, str)):
            места.add((первое.value.lineno, первое.value.col_offset))
    return места


def поток(текст: str) -> list[tuple[int, str]]:
    """Токены кода: без комментариев, докстрингов, пустых строк и отступов."""
    места = _докстринги(текст)
    собрано = []
    try:
        for т in tokenize.generate_tokens(io.StringIO(текст).readline):
            if т.type in ПРОПУСК:
                continue
            if т.type == tokenize.STRING and т.start in места:
                continue
            собрано.append((т.type, т.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    return собрано


def разошлось(было: str, стало: str) -> tuple[int, str]:
    """Сколько токенов кода разошлось и первое расхождение словами. `(0, "")` — правка текстовая."""
    a, b = поток(было), поток(стало)
    if a == b:
        return 0, ""
    for i, (левый, правый) in enumerate(zip(a, b)):
        if левый != правый:
            return abs(len(a) - len(b)) or 1, f"токен {i}: было {левый[1]!r}, стало {правый[1]!r}"
    ушли = [т[1] for т in (a[len(b):] if len(a) > len(b) else b[len(a):])]
    сторона = "исчезли" if len(a) > len(b) else "появились"
    return abs(len(a) - len(b)), f"{сторона} токены: {' '.join(ушли[:8])}"
