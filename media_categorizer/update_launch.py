"""Start an independent installer/restarted app outside the old frozen runtime."""
import os
import sys
from contextlib import contextmanager


@contextmanager
def external_dll_search():
    """Do not make Windows helpers load DLLs from the PyInstaller extraction directory."""
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        yield
        return
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    size = kernel.GetDllDirectoryW(0, None)
    buffer = ctypes.create_unicode_buffer(size+1)
    kernel.GetDllDirectoryW(len(buffer), buffer)
    if not kernel.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.SetDllDirectoryW(buffer.value or None)


def independent_environment():
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith('_PYI_') and key.upper() != '_MEIPASS2'}
    # Public PyInstaller restart protocol; security validation stays enabled.
    environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return environment


def installer_command(path):
    escaped = str(path).replace("'", "''")
    pids = [os.getpid()]
    if getattr(sys, 'frozen', False):
        pids.append(os.getppid())  # Wait for the onefile bootloader to release the EXE too.
    waiting = ','.join(map(str, pids))
    return (f"Wait-Process -Id {waiting} -ErrorAction SilentlyContinue; "
            f"Start-Process -FilePath '{escaped}' -ArgumentList '/SILENT','/NORESTART','/RESTARTAPP=1'")
