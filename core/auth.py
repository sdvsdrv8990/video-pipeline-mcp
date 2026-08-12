"""
core/auth.py — Ключ доступа: сервер выдаёт его себе сам (S1)

## Назначение
Токен не придумывает человек и не хранит в голове: сервер генерирует его при первом старте
(`secrets.token_urlsafe(32)`), кладёт в `.env` с правами `0600` и требует на каждом запросе.
Нет токена и нет явного разрешения работать без него — сервер **не стартует** (fail-closed).

## Границы
- `.env` лежит в корне репозитория, **вне `workspace/`**: файловые инструменты ограничены
  `workspace/` (`core/paths.safe_resolve`), поэтому до секрета они не дотягиваются по построению,
  а не по договорённости с моделью (`15 §3-тер`).
- Имена заголовков — из allowlist коннектора Claude AI Web (`authorization`, `x-api-key`),
  иначе клиент физически не сможет прислать ключ.
- Шифрования секрета «своими руками» здесь нет: ключ расшифровки пришлось бы хранить рядом.
  Защита — права файла (`0600`) и расположение вне рабочей области.
"""

import os
import re
import secrets
import stat
from pathlib import Path
from typing import Mapping

TOKEN_VAR = "MCP_AUTH_TOKEN"
ENV_FILE = ".env"
# 32 байта энтропии в url-safe виде: длиннее любых практических таблиц перебора.
TOKEN_BYTES = 32
# Разрешение «только владелец» — общесистемная практика для файлов с секретами.
SECRET_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0600


class AuthError(Exception):
    """Отказ конфигурации доступа (маппится вызывающим в код реестра реакций)."""

    def __init__(self, code: str, message: str, reason: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason


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


def _write_token(env_path: Path, token: str) -> None:
    """Записать/обновить токен в `.env`, не тронув остальные переменные, и закрыть права."""
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    line = f"{TOKEN_VAR}={token}"
    if re.search(rf"^{TOKEN_VAR}=.*$", existing, flags=re.MULTILINE):
        new = re.sub(rf"^{TOKEN_VAR}=.*$", line, existing, flags=re.MULTILINE)
    else:
        new = (existing.rstrip("\n") + "\n" if existing.strip() else "") + line + "\n"
    env_path.write_text(new, encoding="utf-8")
    os.chmod(env_path, SECRET_MODE)


def ensure_token(env_path: Path, env: Mapping[str, str] | None = None) -> tuple[str, bool]:
    """Вернуть действующий токен, сгенерировав его при первом старте.

    Порядок: переменная окружения → `.env` → генерация. Повторный старт токен НЕ меняет.

    Returns:
        (token, created) — created=True, если ключ выпущен прямо сейчас.
    """
    env = os.environ if env is None else env
    from_env = (env.get(TOKEN_VAR) or "").strip()
    if from_env:
        return from_env, False
    from_file = _read_env(env_path).get(TOKEN_VAR, "").strip()
    if from_file:
        return from_file, False
    token = secrets.token_urlsafe(TOKEN_BYTES)
    _write_token(env_path, token)
    return token, True


def rotate_token(env_path: Path) -> str:
    """Перевыпустить токен (старый перестаёт действовать сразу после перезапуска)."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    _write_token(env_path, token)
    return token


def token_file_mode(env_path: Path) -> int:
    """Права на файле с секретом — числом (для проверки инвариантом)."""
    return stat.S_IMODE(env_path.stat().st_mode)


def check_auth(headers: Mapping[str, str], token: str) -> str:
    """Проверка входящего запроса. Возвращает код отказа или "" если доступ разрешён.

    Принимаются оба заголовка из allowlist коннектора Claude AI Web:
    `Authorization: Bearer <token>` и `X-Api-Key: <token>`. Сравнение — постоянного времени.
    """
    if not token:
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
    # compare_digest на str падает TypeError, если есть не-ASCII: чужой токен с кириллицей
    # уронил бы обработчик в 500 вместо честного 401. Сравниваем байты.
    ok = secrets.compare_digest(presented.encode("utf-8"), token.encode("utf-8"))
    return "" if ok else "AUTH_FAILED"
