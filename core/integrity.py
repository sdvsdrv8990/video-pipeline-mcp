"""core/integrity.py — подпись артефактов и идентичность инстанса.

Запись без подписи или с чужой подписью не применяется: второй такой же сервер, другой агент и
ручная правка файла перестают быть невидимыми. Модуль ничего не решает о доступе — он даёт подпись
и проверку, политику применяет вызывающий. Без криптобиблиотеки подпись честно недоступна, а не
подменяется самодельным хешем, который выглядел бы как защита.
"""

import hashlib
import json
import stat
from pathlib import Path
from typing import Any

from core.paths import Files

try:  # Подпись — опциональная зависимость: без неё честно объявляем себя недоступной.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.exceptions import InvalidSignature
    SIGNING_AVAILABLE = True
except ImportError:  # pragma: no cover — среда без cryptography
    SIGNING_AVAILABLE = False

KEY_FILE = "instance_key.pem"
MACHINE_ID_FILE = "/etc/machine-id"
SECRET_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0600




def machine_fingerprint(workspace: Path) -> str:
    """Отпечаток окружения: machine-id + путь рабочей области.

    `/etc/machine-id` читается БЕЗ root (в отличие от DMI-серийников), поэтому не конфликтует
    с инвариантом «сервер не root». Отпечаток — не секрет и им не притворяется.
    """
    raw = ""
    p = Path(MACHINE_ID_FILE)
    if p.exists():
        try:
            raw = p.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
    return hashlib.sha256(f"{raw}|{Path(workspace).resolve()}".encode()).hexdigest()[:32]


class InstanceIdentity:
    """Личность инстанса: ключ подписи + отпечаток машины."""

    def __init__(self, key_dir: Path, workspace: Path):
        self.key_path = Path(key_dir) / KEY_FILE
        self.workspace = Path(workspace)
        # Any: тип ключа существует только когда cryptography установлена (SIGNING_AVAILABLE).
        self._private: Any = None

    # ─── ключи ───

    def ensure_key(self) -> bool:
        """Выпустить ключ подписи при первом старте. False — подпись недоступна (нет библиотеки)."""
        if not SIGNING_AVAILABLE:
            return False
        if not self.key_path.exists():  # проба — чтобы не выпускать ключ на каждом старте; гонку держит create
            Files(self.key_path.parent).create(self.key_path.name, Ed25519PrivateKey.generate().private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()), mode=SECRET_MODE)
        # Ключ — тот, что лёг на диск: в гонке двух процессов он у обоих один, ключ проигравшего не живёт в памяти.
        self._private = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        return True

    @property
    def instance_id(self) -> str:
        """Публичный идентификатор инстанса — отпечаток открытого ключа."""
        if self._private is None:
            return ""
        pub = self._private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        return hashlib.sha256(pub).hexdigest()[:32]

    # ─── подпись ───

    @staticmethod
    def _canonical(payload: dict) -> bytes:
        """Каноническая форма: подпись не должна зависеть от порядка ключей и отступов."""
        return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")

    def seal(self, payload: dict, session_id: str = "") -> dict:
        """Вернуть конверт целостности для содержимого: кто, где и чем подписал."""
        if self._private is None:
            return {}
        body = self._canonical(payload)
        return {
            "instance_id": self.instance_id,
            "machine": machine_fingerprint(self.workspace),
            "session_id": session_id,
            "alg": "ed25519",
            "sig": self._private.sign(body).hex(),
        }

    def verify(self, payload: dict, seal: dict) -> str:
        """Проверить конверт. Возвращает код отказа или "" если запись наша.

        Коды: `FOREIGN_WRITE` — подписи нет или она чужая; `MACHINE_MISMATCH` — подпись наша,
        но отпечаток машины другой (переезд/восстановление → workspace read-only до `--re-adopt`).
        """
        if self._private is None:
            return ""            # подпись недоступна в этой сборке — не притворяемся, что проверили
        if not seal or not seal.get("sig"):
            return "FOREIGN_WRITE"
        if seal.get("instance_id") != self.instance_id:
            return "FOREIGN_WRITE"
        try:
            pub: Ed25519PublicKey = self._private.public_key()
            pub.verify(bytes.fromhex(seal["sig"]), self._canonical(payload))
        except (InvalidSignature, ValueError):
            return "FOREIGN_WRITE"
        if seal.get("machine") != machine_fingerprint(self.workspace):
            return "MACHINE_MISMATCH"
        return ""


def chain_hash(previous_hash: str, entry: dict) -> str:
    """Хэш-цепочка журнала: каждая запись зависит от предыдущей.

    Вырезание или подмена строки задним числом рвёт цепочку — это видно проверкой,
    а не доверием к файлу.
    """
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{previous_hash}|{body}".encode()).hexdigest()
