"""
scripts/installer_tui.py — выбор роли машины при установке: что здесь будет стоять.

## Назначение
Первый экран установки. Спрашивает РОЛЬ машины (не компоненты) и объявляет выбор в
`config/install.yaml`: стоит ли здесь сервер MCP и какой конец туннеля здесь работает.
Читает объявление `core/deploy`, поднимает по нему `run.sh`. Заменяет `install_role.sh`.

## Границы
- только stdlib: экран рисуется ДО создания `.venv`, ставить в этот момент нечего;
- yaml пишется строками, а не библиотекой, по той же причине;
- решает пользователь: скрипт ничего не устанавливает и не судит, он объявляет выбор;
- нет терминала (CI, конвейер) — работает `VPM_INSTALL_CHOICE`, экран не поднимается.
"""

import curses
import locale
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "install.yaml"

ЗАГОЛОВОК = "VIDEO PIPELINE MCP — УСТАНОВКА"
ШИРИНА = 62

# Роль машины → что объявляется. Порядок строк здесь И ЕСТЬ порядок пунктов меню.
# `both` пользователю не показывается — он живёт только правым столбцом, как техническое значение.
РОЛИ = (
    ("local", "Local", ("MCP-сервер + локальный конец туннеля", "Рабочая машина"),
     (True, "client")),
    ("server", "Server", ("MCP-сервер + серверный конец туннеля", "Серверная машина"),
     (True, "server")),
    ("tunnel", "Tunnel", ("Только туннель", "Без MCP-сервера"),
     None),  # конец туннеля спрашивает второй экран
)

# Второй экран роли Tunnel: где находится ВТОРОЙ конец.
КОНЦЫ = (
    ("remote", "Удалённый сервер", ("Подключение к туннелю на другой машине",), (False, "client")),
    ("here", "Эта машина", ("Оба конца туннеля работают локально",), (False, "both")),
)

ИМЕНА_КОНЦОВ = {"client": "Tunnel Client", "server": "Tunnel Server",
                "both": "Tunnel Client + Tunnel Server", "none": "без туннеля"}

ВЫБОРЫ = {"local": ("роль", "local"), "server": ("роль", "server"),
          "tunnel-remote": ("конец", "remote"), "tunnel-here": ("конец", "here")}

# Прежний словарь ролей: чтобы старые неинтерактивные прогоны не начали молча ставить другое.
СТАРЫЕ = {"local": (True, "client"), "server": (False, "server"), "both": (True, "both")}


class Отменено(Exception):
    """Пользователь вышел: Esc на первом экране или Ctrl+C."""


def строка_(окно, y: int, x: int, текст: str, атрибут: int = 0) -> None:
    """Печать, не роняющая экран на узком терминале и на последней клетке строки."""
    try:
        окно.addstr(y, x, текст, атрибут)
    except curses.error:
        pass


def рамка(окно, строки: list[tuple[str, int]], подвал: str) -> None:
    окно.erase()
    высота, ширина = окно.getmaxyx()
    w = min(ШИРИНА, max(20, ширина - 1))
    строка_(окно, 0, 0, "╭" + "─" * (w - 2) + "╮")
    строка_(окно, 1, 0, "│" + ЗАГОЛОВОК.center(w - 2) + "│", curses.A_BOLD)
    строка_(окно, 2, 0, "├" + "─" * (w - 2) + "┤")
    y = 3
    for текст, атрибут in строки:
        if y >= высота - 4:
            break
        строка_(окно, y, 0, "│" + " " * (w - 2) + "│")
        строка_(окно, y, 2, текст[: w - 4], атрибут)
        y += 1
    строка_(окно, y, 0, "├" + "─" * (w - 2) + "┤")
    строка_(окно, y + 1, 0, "│" + подвал.center(w - 2) + "│", curses.A_DIM)
    строка_(окно, y + 2, 0, "╰" + "─" * (w - 2) + "╯")
    окно.refresh()


def меню(окно, вопрос: str, пункты, назад: str) -> int | None:
    """Индекс выбранного пункта; None — Esc (назад/выход)."""
    курсор = 0
    while True:
        строки: list[tuple[str, int]] = [(вопрос, curses.A_BOLD), ("", 0)]
        for i, (_, метка, пояснения, _полезное) in enumerate(пункты):
            выбран = i == курсор
            строки.append((("> " if выбран else "  ") + метка,
                           curses.A_REVERSE if выбран else curses.A_BOLD))
            строки += [("      " + п, curses.A_DIM) for п in пояснения]
            строки.append(("", 0))
        рамка(окно, строки, f"↑ ↓ Выбор     Enter Продолжить     Esc {назад}")

        клавиша = окно.getch()
        if клавиша == curses.KEY_UP:
            курсор = (курсор - 1) % len(пункты)
        elif клавиша == curses.KEY_DOWN:
            курсор = (курсор + 1) % len(пункты)
        elif клавиша in (curses.KEY_ENTER, 10, 13):
            return курсор
        elif клавиша == 27:  # Esc
            return None


def _подтверждение(окно, титул: str, ставится: list[str], назначение: str) -> bool:
    строки: list[tuple[str, int]] = [(титул, curses.A_BOLD), ("", 0),
                                     ("Будет установлено:", 0), ("", 0)]
    строки += [("  ✓ " + что, 0) for что in ставится]
    строки += [("", 0), ("  " + назначение, curses.A_DIM), ("", 0)]
    рамка(окно, строки, "Enter Продолжить     Esc Назад")
    while True:
        клавиша = окно.getch()
        if клавиша in (curses.KEY_ENTER, 10, 13):
            return True
        if клавиша == 27:
            return False


def _экран(окно) -> tuple[bool, str]:
    curses.curs_set(0)
    окно.keypad(True)
    while True:
        i = меню(окно, "Как будет использоваться эта машина?", РОЛИ, "Выход")
        if i is None:
            raise Отменено
        _имя, метка, _пояснения, полезное = РОЛИ[i]

        if полезное is None:
            j = меню(окно, "Где находится второй конец туннеля?", КОНЦЫ, "Назад")
            if j is None:
                continue  # Esc второго экрана возвращает к выбору роли, а не выходит из установки
            полезное = КОНЦЫ[j][3]

        mcp, конец = полезное
        ставится = (["MCP Server"] if mcp else []) + [ИМЕНА_КОНЦОВ[конец]]
        назначение = ("Назначение: рабочая машина" if конец == "client"
                      else "Назначение: серверная машина") if mcp else \
            "MCP Server не устанавливается."
        if _подтверждение(окно, метка, ставится, назначение):
            return mcp, конец


def записать(mcp: bool, конец: str, path: Path = CONFIG) -> None:
    """Объявление установки. Пишется строками: библиотеки yaml на этом шаге может ещё не быть."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Объявление установки — пишет установщик, читает core/deploy. Секретов здесь не бывает.\n"
        f"mcp: {'true' if mcp else 'false'}\n"
        f"tunnel: {конец}\n",
        encoding="utf-8")


def из_среды() -> tuple[bool, str] | None:
    """Неинтерактивный путь для CI и прогонов. Пусто — поднимаем экран."""
    выбор = os.environ.get("VPM_INSTALL_CHOICE", "").strip()
    if выбор:
        if выбор not in ВЫБОРЫ:
            print(f"✗ неизвестный выбор '{выбор}' — допустимо: {', '.join(ВЫБОРЫ)}", file=sys.stderr)
            raise SystemExit(1)
        вид, имя = ВЫБОРЫ[выбор]
        таблица = РОЛИ if вид == "роль" else КОНЦЫ
        return next(строка[3] for строка in таблица if строка[0] == имя)

    старая = os.environ.get("VPM_INSTALL_ROLE", "").strip()
    if старая:
        if старая not in СТАРЫЕ:
            print(f"✗ неизвестная роль '{старая}' — допустимо: {', '.join(СТАРЫЕ)}", file=sys.stderr)
            raise SystemExit(1)
        return СТАРЫЕ[старая]
    return None


def main() -> int:
    locale.setlocale(locale.LC_ALL, "")
    выбор = из_среды()
    if выбор is None:
        if not sys.stdin.isatty():
            print("✗ нет терминала для меню — задай "
                  f"VPM_INSTALL_CHOICE={'|'.join(ВЫБОРЫ)}", file=sys.stderr)
            return 1
        try:
            выбор = curses.wrapper(_экран)  # wrapper возвращает терминал в норму и на исключении
        except (Отменено, KeyboardInterrupt):
            print("Установка отменена — ничего не объявлено.")
            return 1
    mcp, конец = выбор
    записать(mcp, конец)
    print(f"Объявлено: MCP {'ставится' if mcp else 'не ставится'}, "
          f"конец туннеля — {конец} → {CONFIG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
