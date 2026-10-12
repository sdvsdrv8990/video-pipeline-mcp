"""core/auth.py — ключ доступа: сервер выдаёт его себе сам.

Значения ключа сервер не знает — на диске только отпечаток, поэтому потерянный ключ не
восстанавливают, а выпускают заново. Имена заголовков продиктованы клиентом, а не нашим вкусом:
за пределами его списка ключ просто не доедет.

Самовыпуск секрета — послабление цикла разработки и первый кандидат на снос.
"""

import hashlib
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Mapping

from core.paths import Files

TOKEN_VAR = "MCP_AUTH_TOKEN"          # значение: принимается из окружения, на диск НЕ пишется
DIGEST_VAR = "MCP_AUTH_TOKEN_SHA256"  # то, что реально лежит в .env
ENV_FILE = ".env"
# 32 байта энтропии в url-safe виде: длиннее любых практических таблиц перебора.
TOKEN_BYTES = 32
# Разрешение «только владелец» — общесистемная практика для файлов с секретами.
SECRET_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0600




def _read_env(env_path: Path) -> dict[str, str]:
    """Разбор `.env` (KEY=VALUE, без экспорта и подстановок — нам нужен один ключ)."""
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _write_digest(env_path: Path, digest: str) -> None:
    """Записать отпечаток ключа, СНЯВ прежнее открытое значение, и закрыть права.

    Плейнтекст-строка удаляется, а не остаётся соседкой: иначе миграция на хэш
    оставила бы ровно тот секрет, ради которого всё и затевалось.
    """
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    existing = re.sub(rf"^{TOKEN_VAR}=.*$\n?", "", existing, flags=re.MULTILINE)
    line = f"{DIGEST_VAR}={digest}"
    if re.search(rf"^{DIGEST_VAR}=.*$", existing, flags=re.MULTILINE):
        new = re.sub(rf"^{DIGEST_VAR}=.*$", line, existing, flags=re.MULTILINE)
    else:
        new = (existing.rstrip("\n") + "\n" if existing.strip() else "") + line + "\n"
    # Заменой слоя: .env правит и владелец руками — читатель не застанет половину, права владельца до первого байта.
    Files(env_path.parent).replace(env_path.name, new, mode=SECRET_MODE)


def token_digest(token: str) -> str:
    """Хэш ключа — то единственное, что сервер хранит и чем сравнивает. Недопустимые байты заголовка (суррогаты) хешируются
    как есть; у допустимой строки байты те же, что у строгого UTF-8."""
    return hashlib.sha256(token.encode("utf-8", "surrogateescape")).hexdigest() if token else ""


def ensure_digest(env_path: Path, env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Вернуть действующий отпечаток ключа, выпустив ключ при первом старте.

    Порядок: переменная окружения (значение, хэшируем на лету) → отпечаток в `.env` →
    открытый ключ в `.env` (миграция: хэшируем и стираем плейнтекст) → генерация.

    Returns:
        (digest, issued) — issued непусто ТОЛЬКО когда ключ выпущен прямо сейчас;
        это единственный момент, когда значение вообще существует в открытом виде.
    """
    env = os.environ if env is None else env
    from_env = (env.get(TOKEN_VAR) or "").strip()
    if from_env:
        return token_digest(from_env), ""
    file_vars = _read_env(env_path)
    stored = file_vars.get(DIGEST_VAR, "").strip()
    if stored:
        return stored, ""
    legacy = file_vars.get(TOKEN_VAR, "").strip()
    if legacy:
        _write_digest(env_path, token_digest(legacy))   # ключ прежний, открытая копия убрана
        return token_digest(legacy), ""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    _write_digest(env_path, token_digest(token))
    return token_digest(token), token


def rotate_token(env_path: Path) -> str:
    """Перевыпустить ключ. Возвращённое значение существует только здесь — на диск идёт хэш."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    _write_digest(env_path, token_digest(token))
    return token


def token_fingerprint(token: str) -> str:
    """Короткий отпечаток ключа по его значению (совпадает с началом хранимого хэша)."""
    return token_digest(token)[:12]


def digest_fingerprint(digest: str) -> str:
    """Отпечаток по хранимому хэшу — сервер знает только его, значения у него нет."""
    return (digest or "")[:12]


def token_file_mode(env_path: Path) -> int:
    """Права на файле с секретом — числом (для проверки инвариантом)."""
    return stat.S_IMODE(env_path.stat().st_mode)


def check_auth(headers: Mapping[str, str], digest: str) -> str:
    """Проверка входящего запроса по ХЭШУ ключа. Возвращает код отказа или "" при доступе.

    Сервер не хранит значение ключа: сравнивается `sha256` присланного с записанным
    отпечатком, постоянным временем. Принимаются оба заголовка из allowlist коннектора
    Claude AI Web: `Authorization: Bearer <ключ>` и `X-Api-Key: <ключ>`.
    """
    if not digest:
        # Пустой ожидаемый токен не делает сервер открытым: это ошибка конфигурации.
        return "AUTH_REQUIRED"
    lower = {k.lower(): v for k, v in headers.items()}
    presented = ""
    auth = lower.get("authorization", "")
    if auth.startswith("Bearer "):
        presented = auth[7:]
    elif lower.get("x-api-key"):
        presented = lower["x-api-key"]
    if not presented:
        return "AUTH_REQUIRED"
    return "" if presented_matches(presented, digest) else "AUTH_FAILED"


def presented_matches(presented: str, digest: str) -> bool:
    """Присланный секрет против отпечатка своего — постоянным временем на любом значении: сравниваются отпечатки (ASCII),
    а не строки — `compare_digest` на str с не-ASCII бросает TypeError, и чужой ключ ронял бы обработчик в 500."""
    return bool(presented and digest) and secrets.compare_digest(token_digest(presented).encode("ascii"),
                                                                 digest.encode("ascii"))
