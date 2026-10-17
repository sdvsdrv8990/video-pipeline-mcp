"""core/owner_only.py — доступ «только владелец» на любой ОС: ключи, токены, каталог секретов (FS-07, SA-04).

На POSIX это биты режима: 0o600 у файла, 0o700 у каталога. На Windows биты режима меняют только флаг «только чтение»
(CPython `os.rst`, `chmod`), а файл наследует DACL каталога — под `C:\\` он открыт всем вошедшим пользователям. Поэтому
«только владелец» там — защищённый DACL: владелец процесса и SYSTEM, без унаследованных записей. Файл получает его в
момент создания (`CreateFileW` с дескриптором безопасности), а не после: открытый до смены прав чужой дескриптор
остался бы годным.
"""

import os
import stat
import sys
from pathlib import Path

OWNER_FILE = stat.S_IRUSR | stat.S_IWUSR      # 0o600
OWNER_DIR = stat.S_IRWXU                      # 0o700


def private(mode: int) -> bool:
    """Режим без битов группы и прочих — доступ только владельцу."""
    return not mode & (stat.S_IRWXG | stat.S_IRWXO)


def create(path: Path, mode: int) -> int:
    """Новый файл для записи, созданный исключительно (`O_EXCL`); «только владелец» — уже в момент появления имени.
    Итоговый режим POSIX ставит вызывающий: файл рождается 0o600 и только потом открывается шире."""
    if sys.platform == "win32" and private(mode):
        return _nt_create(path)
    # O_BINARY есть только на Windows: без него дескриптор в текстовом режиме CRT, запись переводит \n в \r\n.
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), OWNER_FILE)


def restrict(path: Path, mode: int) -> None:
    """Права существующего файла или каталога по режиму; на Windows «только владелец» — защищённый DACL."""
    os.chmod(path, mode)
    if sys.platform == "win32" and private(mode):
        _nt_protect(path)


def make_dir(path: Path) -> None:
    """Каталог «только владелец» с рождения (родители — обычные); уже есть — права чинятся. На Windows `mode` у mkdir
    до исправления CVE-2024-4030 не действует, поэтому каталог создаётся сразу с защищённым DACL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            _nt_make_dir(path)
        else:
            path.mkdir(mode=OWNER_DIR)
    except FileExistsError:
        restrict(path, OWNER_DIR)


if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Константы Win32 SDK: winnt.h, fileapi.h, sddl.h, accctrl.h.
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1                                   # TOKEN_INFORMATION_CLASS.TokenUser
    _SDDL_REVISION_1 = 1
    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _CREATE_NEW = 1
    _FILE_ATTRIBUTE_NORMAL = 0x80
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    _kernel32.CreateDirectoryW.restype = wintypes.BOOL
    _advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD)]
    _advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG)]
    _advapi32.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                                    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    _advapi32.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                                                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD

    def _check(ok: object) -> None:
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def _user_sid() -> str:
        """SID владельца процесса строкой — из маркера доступа процесса (TokenUser)."""
        token = wintypes.HANDLE()
        _check(_advapi32.OpenProcessToken(_kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)))
        try:
            size = wintypes.DWORD()
            _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))   # первый вызов — размер
            buffer = ctypes.create_string_buffer(size.value)
            _check(_advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, size, ctypes.byref(size)))
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]   # TOKEN_USER.User.Sid — первое поле
            text = ctypes.c_void_p()
            _check(_advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)))
            try:
                return ctypes.wstring_at(text.value or 0)
            finally:
                _kernel32.LocalFree(text)
        finally:
            _kernel32.CloseHandle(token)

    def _descriptor(directory: bool) -> ctypes.c_void_p:
        """Дескриптор «только владелец»: защищённый DACL (P) — владелец процесса и SYSTEM с полным доступом; у каталога
        записи наследуют вложенные (OICI). Освобождает вызывающий — LocalFree."""
        inherit = "OICI" if directory else ""
        sddl = "D:P" + "".join(f"(A;{inherit};FA;;;{who})" for who in (_user_sid(), "SY"))
        descriptor = ctypes.c_void_p()
        _check(_advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None))
        return descriptor

    def _nt_create(path: Path) -> int:
        descriptor = _descriptor(directory=False)
        try:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor.value, False)
            handle = _kernel32.CreateFileW(str(path), _GENERIC_WRITE, 0, ctypes.byref(attributes), _CREATE_NEW,
                                           _FILE_ATTRIBUTE_NORMAL, None)
            if handle in (None, _INVALID_HANDLE):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _kernel32.LocalFree(descriptor)
        try:
            return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except BaseException:
            _kernel32.CloseHandle(handle)
            raise

    def _nt_make_dir(path: Path) -> None:
        descriptor = _descriptor(directory=True)
        try:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor.value, False)
            # ERROR_ALREADY_EXISTS → FileExistsError: WinError называет errno по коду Windows.
            _check(_kernel32.CreateDirectoryW(str(path), ctypes.byref(attributes)))
        finally:
            _kernel32.LocalFree(descriptor)

    def _nt_protect(path: Path) -> None:
        descriptor = _descriptor(directory=path.is_dir())
        try:
            present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
            _check(_advapi32.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl),
                                                       ctypes.byref(defaulted)))
            code = _advapi32.SetNamedSecurityInfoW(
                str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                None, None, dacl, None)
            if code:
                raise ctypes.WinError(code)
        finally:
            _kernel32.LocalFree(descriptor)
