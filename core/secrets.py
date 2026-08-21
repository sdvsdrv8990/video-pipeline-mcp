"""
core/secrets.py — ключи провайдеров: на уровне канала, зашифрованы, ИИ недоступны.

## Назначение
Ключ — свойство КАНАЛА, а не сервера, поэтому файл ключей лежит в самом канале:
`<канал>/.secrets/provider_keys.enc`. Каталог `.secrets` объявлен закрытым в единой точке путей
(`core/paths`), поэтому ни один инструмент до него не дотягивается — ни чтением, ни листингом,
ни поиском, ни записью. Наружу отдаётся только отпечаток, значение не показывается никогда.

## Границы
Шифрование на диске защищает от инструментов сервера и от чужих глаз в кадре, но не от того, у
кого есть shell на этой машине: ключ шифрования лежит рядом (`instance_secret.key`, `0600`, вне
рабочей области и вне git). Для модели, работающей через MCP, граница жёсткая; владелец, у
которого shell есть, и так владеет ключами.
"""

import hashlib
import json
import os
import stat
import time
from pathlib import Path

from core.paths import safe_resolve
from core.contracts import ContractError

try: # Шифрование — опциональная зависимость: без неё честно объявляем себя недоступным.
    from cryptography.fernet import Fernet, InvalidToken
    ENCRYPTION_AVAILABLE = True
except ImportError:  # pragma: no cover — среда без cryptography
    ENCRYPTION_AVAILABLE = False

SECRETS_DIR = ".secrets"
KEYS_FILE = "provider_keys.enc"
BOX_KEY_FILE = "instance_secret.key"          # ключ шифрования: вне workspace, вне git (*.key)
SECRET_MODE = stat.S_IRUSR | stat.S_IWUSR     # 0600
FINGERPRINT_LEN = 12


class SecretError(ContractError):
    """Отказ работы с ключами (код из server_reactions.yaml)."""


def fingerprint(value: str) -> str:
    """Отпечаток ключа: показывать можно, восстановить нельзя."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:FINGERPRINT_LEN]


class ChannelSecrets:
    """Ключи провайдеров одного рабочего пространства, по каналам."""

    def __init__(self, workspace_path: str | Path, home_path: str | Path):
        self.workspace = Path(workspace_path)
        self.home = Path(home_path)           # где лежит ключ шифрования (рядом с .env)

    # ═══ Ключ шифрования ═══

    def _box(self):
        if not ENCRYPTION_AVAILABLE:
            raise SecretError(
                "SECRET_ENCRYPTION_UNAVAILABLE", "Шифрование недоступно: нет библиотеки cryptography.",
                reason="Поставь зависимости сервера (pip install -e .) — хранить ключи провайдеров "
                       "в открытом виде сервер не станет.")
        path = self.home / BOX_KEY_FILE
        if not path.exists():
            path.write_bytes(Fernet.generate_key())
            os.chmod(path, SECRET_MODE)
        os.chmod(path, SECRET_MODE)           # права чинятся при каждом обращении, а не однажды
        return Fernet(path.read_bytes().strip())

    # ═══ Файл канала ═══

    def file_for(self, channel: str) -> Path:
        """Путь к файлу ключей канала. Единственное место, где закрытый каталог открывается."""
        rel = f"{str(channel).strip('/')}/{SECRETS_DIR}/{KEYS_FILE}" if str(channel).strip("/") \
            else f"{SECRETS_DIR}/{KEYS_FILE}"
        return safe_resolve(rel, self.workspace, allow_secrets=True)

    def owner_of(self, path: str) -> str:
        """Чей ключ действует для адреса: ближайший канал ВВЕРХ по дереву, у которого он есть.

        Тем же способом резолвится `.templates/` — видео берёт ключ своего канала, и не нужно
        состояние «канал открыт»: адрес известен всегда.
        """
        node = str(path or "").strip("/")
        while True:
            if self.file_for(node).is_file():
                return node
            if not node:
                return ""
            node = node.rpartition("/")[0]

    def _read(self, channel: str) -> dict:
        path = self.file_for(channel)
        if not path.is_file():
            return {}
        try:
            raw = self._box().decrypt(path.read_bytes())
        except InvalidToken as e:
            raise SecretError(
                "SECRET_UNREADABLE", f"Файл ключей канала не расшифровывается: {channel}",
                reason=("Файл зашифрован другим ключом инстанса — например, восстановлен из чужого "
                        "бэкапа или потерян instance_secret.key. Ключи придётся внести заново "
                        "(scripts/set_provider_key.py); прочитать их уже нечем.")) from e
        return json.loads(raw.decode("utf-8"))

    def _write(self, channel: str, data: dict) -> Path:
        path = self.file_for(channel)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, stat.S_IRWXU)   # 0700: каталог секретов не для чужих глаз
        blob = self._box().encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(blob)
        os.chmod(tmp, SECRET_MODE)
        tmp.replace(path)                     # атомарно: половины файла ключей не бывает
        return path

    # ═══ Операции ═══

    def set(self, channel: str, provider: str, value: str) -> dict:
        """Положить ключ провайдера, заменив прежний. Значение шифруется до записи."""
        name = str(provider).strip()
        if not name:
            raise SecretError("VALIDATION_ERROR", "Не назван провайдер, которому принадлежит ключ.",
                              reason="Укажи имя провайдера — то же, что стоит в строке канала.")
        if not str(value).strip():
            raise SecretError("VALIDATION_ERROR", "Пустой ключ не сохраняется.",
                              reason="Пустая строка выглядела бы как «ключ есть», а вызов падал бы "
                                     "у провайдера — это хуже, чем честное отсутствие ключа.")
        data = self._read(channel)
        previous = data.get(name) or {}
        data[name] = {"value": value, "fingerprint": fingerprint(value),
                      "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "replaced": previous.get("fingerprint", "")}
        self._write(channel, data)
        return {"provider": name, "fingerprint": data[name]["fingerprint"],
                "updated": data[name]["updated"], "replaced": data[name]["replaced"]}

    def get(self, channel: str, provider: str) -> str:
        """Значение ключа — ТОЛЬКО для исходящего вызова провайдера. В ответ клиенту не уходит."""
        entry = self._read(channel).get(str(provider).strip()) or {}
        return str(entry.get("value") or "")

    def status(self, channel: str, provider: str = "") -> list[dict]:
        """Что известно о ключах БЕЗ значений: имя, отпечаток, когда обновлён."""
        data = self._read(channel)
        names = [provider] if provider else sorted(data)
        out = []
        for name in names:
            entry = data.get(name) or {}
            out.append({"provider": name, "present": bool(entry.get("value")),
                        "fingerprint": entry.get("fingerprint", ""),
                        "updated": entry.get("updated", "")})
        return out

    def remove(self, channel: str, provider: str) -> bool:
        """Убрать ключ провайдера (владелец, вручную)."""
        data = self._read(channel)
        if str(provider).strip() not in data:
            return False
        data.pop(str(provider).strip())
        self._write(channel, data)
        return True


def redact(text: str, secrets: list[str]) -> str:
    """Вычистить значения ключей из произвольного текста (последний рубеж перед выводом).

    Нужен не вместо дисциплины «значение никуда не кладём», а после неё: сообщение об ошибке
    от провайдера может процитировать присланный ключ, и тогда он уедет клиенту в тексте отказа.
    """
    out = str(text)
    for value in secrets:
        if value and len(value) >= 8:
            out = out.replace(value, f"<ключ скрыт {fingerprint(value)}>")
    return out
