"""core/environment.py — двери среды сервера: переменные, которыми его настраивают снаружи, одним перечнем.

Справка `server.py --help` печатается из него; полноту держит событие `tests/quick/test_environment.py`: каждое имя
`MCP_*` в коде сервера — дверь перечня или объявленное «не дверь», и у каждой двери есть читатель. Здесь же — среда
процесса, которого порождает сервер (`child_environ`), и аргументы его порождения (`child_spawn`): одно решение для всех
мест порождения.
"""

import os
from collections.abc import Mapping
from typing import Literal

DOORS = {
    "MCP_HOST": "адрес слушателя; умолчание — у флага --host",
    "MCP_PORT": "порт слушателя; умолчание — у флага --port",
    "MCP_WORKSPACE": "рабочая область — данные каналов; по умолчанию workspace/ рядом с кодом",
    "MCP_CONFIG": "каталог деклараций; по умолчанию config/ рядом с кодом",
    "MCP_HOME": "дом инстанса — личность, ключ шифрования, хранилище заданий; по умолчанию родитель каталога деклараций",
    "MCP_LOGS": "корень журналов (след и журнал фактов); по умолчанию каталог журналов пользователя ОС",
    "MCP_AUTH_TOKEN": "ключ доступа к серверу; без него — отпечаток из .env, а при первом старте сервер выпускает ключ сам",
    "MCP_ALLOWED_ORIGINS": "браузерные источники сверх петли, через запятую",
    "MCP_ALLOWED_HOSTS": "имена, которыми вправе называться сервер, сверх петли и туннеля, через запятую",
    "MCP_ALLOW_NO_AUTH": "1 — калитка без ключа для локальной разработки; в тестах не используется",
    "MCP_TUNNEL_TOKEN": "токен туннеля (режим токена); процессу туннеля уходит средой, а не аргументом",
    "MCP_RUNNER_TOKEN": "токен раннера локальных моделей: его выпускает и передаёт раннеру супервизор, не владелец",
    "MCP_RUNNER_READY_FD": "канал знака готовности раннера (дескриптор, на Windows хэндл): его открывает супервизор, не владелец",
}

# Имена вида MCP_*, которые из среды не читаются.
NOT_DOORS = {
    "MCP_AUTH_TOKEN_SHA256": "ключ файла .env — отпечаток ключа доступа",
}

# Двери-секреты: ни один ребёнок сервера их не наследует — pip исполняет чужой код сборки, а раннер и туннель получают
# свой секрет от того, кто его выпускает.
SECRETS = frozenset({"MCP_AUTH_TOKEN", "MCP_TUNNEL_TOKEN", "MCP_RUNNER_TOKEN"})


def epilog() -> str:
    """Таблица дверей для `--help`: имя и что меняет."""
    width = max(map(len, DOORS))
    return "двери среды:\n" + "\n".join(f"  {name:<{width}}  {what}" for name, what in DOORS.items())


def child_spawn(errors: Literal["strict", "replace"], extra: Mapping[str, str] | None = None,
                environ: Mapping[str, str] = os.environ) -> dict:
    """Аргументы порождения ребёнка — среда узла и вывод. `errors` — выбор места по назначению вывода, умолчания нет:
    вывод для человека или журнала — replace, текст utf-8 (не кодировкой локали: на Windows она cp1252) с заменой битых
    байт; разбираемый вывод — strict, байты: их декодирует `child_text` в потоке вызывающего, и битый байт отказывает
    громко (на Windows `communicate` декодирует в потоке-читателе, где отказ гаснет вместе с выводом). `environ` — база
    среды, если место уже сняло из неё своё."""
    env = child_environ(extra, environ)
    return {"env": env} if errors == "strict" else {"env": env, "encoding": "utf-8", "errors": errors}


def child_environ(extra: Mapping[str, str] | None = None, environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Среда процесса, которого порождает сервер: его среда без дверей-секретов, в UTF-8 Mode (Python-ребёнок пишет в
    канал не кодировкой локали; явный выбор владельца не перекрывается), затем `extra` — свой секрет ребёнку кладёт тот,
    кто его выпускает."""
    среда = {k: v for k, v in environ.items() if k not in SECRETS}
    среда.setdefault("PYTHONUTF8", "1")
    return среда | dict(extra or {})


def child_text(data: bytes, errors: Literal["strict", "replace"]) -> str:
    """Вывод ребёнка, полученный байтами, — текстом utf-8 в потоке вызывающего; переводы строк — как в текстовом режиме."""
    return data.decode("utf-8", errors).replace("\r\n", "\n").replace("\r", "\n")


class ChildSaid(Exception):
    """Слова ребёнка об отказе — причина (`raise … from`) для журнала сервера, а не текст ответа: в них пути хоста.
    Слово — последняя строка stderr: ею бинарь называет, что помешало."""

    def __init__(self, stderr: str):
        super().__init__(stderr.strip().rsplit("\n", 1)[-1] or "причина не названа")
