"""core/search/lines.py — файл потоком, строка за строкой: номер строки, совпадение слов без регистра, предел строки.

Один узел чтения содержимого по строкам: им ищет FsSearcher (слова в содержимом), им же читает fs_read_file по фильтру.
Строки — текстовый поток Python (`newline=None`: декодер знак не режет, переводы строк универсальные). В слове нет
перевода строки, значит совпадение лежит внутри одной строки: сравнение идёт строка за строкой, без арифметики стыков.
"""

import io
import unicodedata
from collections.abc import Generator, Iterable
from contextlib import closing
from pathlib import Path

from core.capacity import Capacity
from core.contracts import ContractError
from core.paths import open_shared

_WIDEST = 4    # байт на знак строки Python в худшем случае (PEP 393: строка хранит знаки по 1, 2 или 4 байта)


def line_limit() -> int:
    """Сколько знаков одной строки держать: память одного места работы `search` у узла ёмкости (ОЗУ процесса поровну
    на объявленные места поиска), в знаках худшей ширины строки Python."""
    return Capacity().memory_share("search") // _WIDEST


def canonical(text: str) -> str:
    """Форма для сравнения без регистра — каноническое совпадение Unicode D145, NFD(casefold(NFD(x))): «ß» и «SS»,
    NFD «й» и NFC «й» совпадают."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", text).casefold())


def numbered(path: Path, limit: int, *, name: str, errors: str) -> Generator[tuple[int, str], None, None]:
    """Строки файла с номерами от 1, без перевода строки. Строка длиннее `limit` знаков — отказ LINE_TOO_LONG с её
    номером, а не память общего процесса. `errors` — обработчик битых байт декодера (`codecs`): `replace` ставит U+FFFD,
    и две половины слова через битый байт не склеиваются; `strict` — UnicodeDecodeError, код отказа знает вызывающий."""
    with io.TextIOWrapper(open_shared(path), encoding="utf-8", errors=errors, newline=None) as f:
        for number, line in enumerate(iter(lambda: f.readline(limit + 1), ""), 1):
            if len(line) > limit and not line.endswith("\n"):
                raise ContractError("LINE_TOO_LONG", facts={"path": name, "line": number, "limit": limit})
            yield number, line.removesuffix("\n")


def contains_all(path: Path, words: Iterable[str], limit: int, *, name: str) -> bool:
    """Все слова есть в файле без учёта регистра: строка за строкой до строки, где нашлось последнее. Битые байты — U+FFFD:
    файл не текст в UTF-8 ищется, как GNU grep ищет по двоичному."""
    wanted = {canonical(word) for word in words}
    with closing(numbered(path, limit, name=name, errors="replace")) as lines:
        while wanted and (row := next(lines, None)) is not None:
            folded = canonical(row[1])
            wanted = {word for word in wanted if word not in folded}
    return not wanted
