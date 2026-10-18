"""core/formulas.py — язык формул книги: имена столбцов в декларации, адреса A1 в книге.

Декларация пишет формулу именами столбцов (`=likes/views*100`); Excel и LibreOffice знают только адреса, и имя в ячейке —
`#NAME?`, которое запасное значение IFERROR прячет в 0. Узел разбирает формулу токенизатором openpyxl (он лексер, синтаксис
не судит — порядок операторов и скобки судит здесь `_syntax`), судит имена по листам книги и собирает адреса строки:
имя — ячейка той же строки, `Лист.столбец` — ячейка листа-записи или весь столбец листа-списка внутри агрегата. Язык
(функции, агрегаты, свои макросы) — `config/formulas.yaml`.
"""

import re
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import NamedTuple

from openpyxl.formula.tokenizer import Token, Tokenizer, TokenizerError
from openpyxl.utils.cell import get_column_letter, quote_sheetname
from openpyxl.xml.constants import MAX_ROW

from core.declaration import Declared, declarations_root
from core.declaration_schemas import FORMULAS, FormulaLanguageDecl

HEADER_ROW = 1                  # заголовки листа книги (ExcelEngine._headers); данные — со следующей строки
DATA_ROW = HEADER_ROW + 1
_ARG = re.compile(r"\{(\d+)\}")
# Токен, после которого инфиксный оператор законен: у него есть левый операнд.
_LEFT = {(Token.OPERAND, s) for s in (Token.RANGE, Token.NUMBER, Token.TEXT, Token.LOGICAL, Token.ERROR)} | {
    (Token.FUNC, Token.CLOSE), (Token.PAREN, Token.CLOSE), (Token.ARRAY, Token.CLOSE), (Token.OP_POST, "")}


class Sheet(NamedTuple):
    """Лист для языка формул: столбцы по порядку (буква — место в листе) и запись ли он (одна строка данных)."""

    columns: tuple[str, ...]
    record: bool


class Slip(NamedTuple):
    """Изъян формулы декларации: правило и что ждали — для отказа двери с путём столбца."""

    rule: str
    expected: str


class Language(NamedTuple):
    """Язык формул из `config/formulas.yaml`: разрешённые функции, агрегаты, свои макросы."""

    functions: frozenset[str]
    aggregates: frozenset[str]
    macros: Mapping[str, str]


def declared_language(error_cls) -> tuple[Declared[FormulaLanguageDecl], Language]:
    """Язык формул из `config/formulas.yaml` и его дверь — для отказа связью «формула книги ↔ язык» (`link_refused`)."""
    door = Declared(declarations_root() / FORMULAS.file, error_cls, "языка формул", schema=FORMULAS.schema)
    model = door.model
    return door, Language(frozenset(model.functions), frozenset(model.aggregates), MappingProxyType(dict(model.macros)))


class _MacroError(ValueError):
    """Макрос не разворачивается: число аргументов, незакрытый вызов или макрос в самом себе."""


def slips(formula: str, sheet: str, sheets: Mapping[str, Sheet], language: Language | None = None) -> list[Slip]:
    """Изъяны формулы листа `sheet`. Без языка — только имена и синтаксис (схема книги языка не знает); с языком —
    ещё функции, макросы и ссылка на лист-список вне агрегата (судит дверь книги, связь двух деклараций)."""
    try:
        tokens = Tokenizer(_expanded(formula, language.macros) if language else formula).items
    except (TokenizerError, _MacroError) as error:
        return [Slip("formula_syntax", str(error))]
    found = _syntax(tokens)
    for token, inside in _walk(tokens):
        if language and token.type == Token.FUNC and token.subtype == Token.OPEN:
            name = token.value[:-1].upper()
            if name not in language.functions:
                found.append(Slip("formula_function", f"функция {name} не объявлена в config/formulas.yaml"))
        elif token.type == Token.OPERAND and token.subtype == Token.RANGE:
            found += _name_slips(token.value, sheet, sheets, language, inside)
    return found


def compiled(formula: str, sheet: str, sheets: Mapping[str, Sheet], row: int, language: Language) -> str:
    """Формула строки `row` листа `sheet` адресами A1. Зовётся после суда (`slips` пуст): изъян здесь — ошибка узла."""
    if gaps := slips(formula, sheet, sheets, language):
        raise ValueError(f"формула не прошла суд: {gaps}")
    tokens = Tokenizer(_expanded(formula, language.macros)).items
    for token, inside in _walk(tokens):
        if token.type == Token.OPERAND and token.subtype == Token.RANGE:
            token.value = _address(token.value, sheet, sheets, row, bool(language.aggregates & set(inside)))
    return "=" + "".join(token.value for token in tokens)


def _walk(tokens: list[Token]) -> Iterator[tuple[Token, tuple[str, ...]]]:
    """Токены с функциями, внутри которых они стоят (снаружи внутрь); скобка выражения и массива — без имени."""
    stack: list[str] = []
    for token in tokens:
        if token.subtype == Token.CLOSE and stack:
            stack.pop()
        yield token, tuple(stack)
        if token.subtype == Token.OPEN:
            stack.append(token.value[:-1].upper() if token.type == Token.FUNC else "")


def _name_slips(name: str, sheet: str, sheets: Mapping[str, Sheet], language: Language | None,
                inside: tuple[str, ...]) -> list[Slip]:
    where, _, column = name.rpartition(".")
    if not where:
        return [] if name in sheets[sheet].columns else [Slip("formula_name", f"`{name}` — не столбец листа {sheet}")]
    if where not in sheets:
        return [Slip("formula_sheet", f"`{where}` — не лист книги")]
    if column not in sheets[where].columns:
        return [Slip("formula_name", f"`{column}` — не столбец листа {where}")]
    if language and not sheets[where].record and not language.aggregates & set(inside):
        return [Slip("formula_row", f"`{name}` — лист-список вне агрегата ({', '.join(sorted(language.aggregates))}): "
                                    f"какая строка — не сказано")]
    return []


def _address(name: str, sheet: str, sheets: Mapping[str, Sheet], row: int, aggregate: bool) -> str:
    where, _, column = name.rpartition(".")
    if not where:
        return f"{_letter(sheets[sheet], column)}{row}"
    letter, title = _letter(sheets[where], column), quote_sheetname(where)
    if sheets[where].record and not aggregate:
        return f"{title}!${letter}${DATA_ROW}"
    return f"{title}!${letter}${DATA_ROW}:${letter}${MAX_ROW}"


def _letter(sheet: Sheet, column: str) -> str:
    return get_column_letter(sheet.columns.index(column) + 1)


def _syntax(tokens: list[Token]) -> list[Slip]:
    """Порядок, которого лексер не судит: инфиксный оператор с левым операндом, скобки закрыты."""
    found: list[Slip] = []
    depth, before = 0, None
    for token in tokens:
        if token.type == Token.WSPACE:
            continue
        if token.type == Token.OP_IN and (before is None or (before.type, before.subtype) not in _LEFT):
            found.append(Slip("formula_syntax", f"оператор {token.value} без левого операнда"))
        depth += 1 if token.subtype == Token.OPEN else -1 if token.subtype == Token.CLOSE else 0
        before = token
    if depth:
        found.append(Slip("formula_syntax", "скобки не закрыты" if depth > 0 else "лишняя закрывающая скобка"))
    return found


def _expanded(formula: str, macros: Mapping[str, str]) -> str:
    """Свои функции декларации — выражениями Excel: вызов `ИМЯ(арг, …)` заменяется телом с {1}, {2}, … в скобках."""
    known = {name.upper(): body for name, body in macros.items()}
    for _ in range(len(known) * 8 + 1):       # вложенность макросов конечна; больше — макрос зовёт сам себя
        tokens = Tokenizer(formula).items
        call = next((i for i, t in enumerate(tokens)
                     if t.type == Token.FUNC and t.subtype == Token.OPEN and t.value[:-1].upper() in known), None)
        if call is None:
            return formula
        name = tokens[call].value[:-1].upper()
        args, end = _arguments(tokens, call)
        try:
            body = _ARG.sub(lambda m: "(" + args[int(m[1]) - 1] + ")", known[name])
        except IndexError:
            raise _MacroError(f"макрос {name}: аргументов {len(args)}, тело ждёт больше") from None
        formula = "=" + "".join(t.value for t in tokens[:call]) + body + "".join(t.value for t in tokens[end + 1:])
    raise _MacroError("макрос разворачивается сам в себя")


def _arguments(tokens: list[Token], call: int) -> tuple[list[str], int]:
    """Аргументы вызова, открытого токеном `call`, текстом, и место закрывающей скобки."""
    args: list[str] = []
    current: list[str] = []
    depth = 0
    for at in range(call + 1, len(tokens)):
        token = tokens[at]
        if token.subtype == Token.CLOSE and depth == 0:
            return ([*args, "".join(current)] if current or args else []), at
        if token.type == Token.SEP and token.subtype == Token.ARG and depth == 0:
            args.append("".join(current))
            current = []
            continue
        depth += 1 if token.subtype == Token.OPEN else -1 if token.subtype == Token.CLOSE else 0
        current.append(token.value)
    raise _MacroError("вызов макроса не закрыт")
