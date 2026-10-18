"""Права файла или каталога глазами теста на любой ОС — оракул, независимый от узла сервера `core/owner_only`.

На POSIX — биты режима. На Windows биты режима меняют только флаг «только чтение», поэтому «только владелец» там судит
DACL: защищён от наследования (`D:P`) и без записей, кроме владельца процесса и SYSTEM. Его читает PowerShell `Get-Acl`,
и такой файл называется 0o600, каталог — 0o700: проверки POSIX и Windows сравнивают одно и то же.
"""

import functools
import os
import stat
import subprocess
import sys
from pathlib import Path


_SYSTEM = "S-1-5-18"


def mode(p: Path) -> int:
    """Права пути: на POSIX — биты режима; на Windows доступ «только владелец» — 0o600 у файла, 0o700 у каталога."""
    if os.name == "nt":
        acl = _powershell(f"$a = Get-Acl -LiteralPath '{p}'; 'protected=' + $a.AreAccessRulesProtected; "
                          "$a.Access | ForEach-Object { $_.IdentityReference.Translate("
                          "[System.Security.Principal.SecurityIdentifier]).Value }")
        if _owner_only_dacl(acl):
            return 0o700 if p.is_dir() else 0o600
        # Улика вердикта — в вывод набора: иначе отчёт показывает только биты режима (на Windows всегда 0o666).
        print(f"  · DACL {p}: {acl!r} · пользователь {_user_sid()}", file=sys.stderr)
    return stat.S_IMODE(p.stat().st_mode)


def _owner_only_dacl(acl: str) -> bool:
    """Защищён от наследования, записи — только владелец процесса и SYSTEM. SID — полные: строка SDDL сокращает известные
    (встроенный администратор `-500` — `LA`, как у раннера GitHub), и сравнение по ней отвечало «нет» верному DACL."""
    lines = [line.strip() for line in acl.splitlines() if line.strip()]
    if not lines or lines[0] != "protected=True":
        return False
    sids = set(lines[1:])
    return bool(sids) and sids <= {_user_sid(), _SYSTEM}


@functools.cache
def _user_sid() -> str:
    return _powershell("[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value")


def _powershell(command: str) -> str:
    """Ответ PowerShell; пустой (ошибки Get-Acl не завершают процесс) — с его stderr, чтобы отказ был виден."""
    done = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, check=True)
    return done.stdout.strip() or f"(пусто; stderr: {done.stderr.strip()[:300]})"
