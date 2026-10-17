"""Права файла или каталога глазами теста на любой ОС — оракул, независимый от узла сервера `core/owner_only`.

На POSIX — биты режима. На Windows биты режима меняют только флаг «только чтение», поэтому «только владелец» там судит
DACL: защищён от наследования (`D:P`) и без записей, кроме владельца процесса и SYSTEM. Его читает PowerShell `Get-Acl`,
и такой файл называется 0o600, каталог — 0o700: проверки POSIX и Windows сравнивают одно и то же.
"""

import functools
import os
import re
import stat
import subprocess
from pathlib import Path


def mode(p: Path) -> int:
    """Права пути: на POSIX — биты режима; на Windows доступ «только владелец» — 0o600 у файла, 0o700 у каталога."""
    if os.name == "nt" and _owner_only_dacl(p):
        return 0o700 if p.is_dir() else 0o600
    return stat.S_IMODE(p.stat().st_mode)


def _owner_only_dacl(p: Path) -> bool:
    sddl = _powershell(f"(Get-Acl -LiteralPath '{p}').Sddl")
    found = re.search(r"D:([A-Z]*)((?:\([^)]*\))+)", sddl)
    if not found or "P" not in found.group(1):
        return False
    return all(ace.split(";")[5] in (_user_sid(), "SY") for ace in re.findall(r"\(([^)]*)\)", found.group(2)))


@functools.cache
def _user_sid() -> str:
    return _powershell("[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value")


def _powershell(command: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True,
                          check=True).stdout.strip()
