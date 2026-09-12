"""Filesystem operations independent of Qt; destinations are never overwritten."""
import os
import shutil
import stat
import tempfile
from pathlib import Path
from contextlib import contextmanager


class PublishedMoveError(OSError):
    """Copy completed, but source removal failed; keep both files for recovery."""


def rename_no_replace(source: Path, target: Path):
    """Publish on the same filesystem, failing atomically if target exists."""
    source, target = Path(source), Path(target)
    if os.name == "nt":
        # Windows rename (unlike replace) refuses an existing destination.
        os.rename(source, target)
    else:
        os.link(source, target)
        source.unlink()


def _same_device(source, target_dir):
    return source.stat().st_dev == target_dir.stat().st_dev


def file_state(path):
    value = Path(path).stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


@contextmanager
def guarded_source(path, removable=False):
    """Windows share modes prevent writes/replacement until the handle closes."""
    if os.name != 'nt':
        with Path(path).open('rb') as stream:
            yield stream
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(Path(path).resolve()), 0x80000000 | (0x10000 if removable else 0),
                    1, None, 3, 0x08000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(descriptor, 'rb') as stream:
        yield stream


def remove_open_source(stream, path):
    if os.name != 'nt':
        Path(path).unlink()
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    remove = kernel.SetFileInformationByHandle
    remove.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    remove.restype = wintypes.BOOL
    disposition = ctypes.c_ubyte(1)
    if not remove(msvcrt.get_osfhandle(stream.fileno()), 4, ctypes.byref(disposition), 1):
        raise ctypes.WinError(ctypes.get_last_error())


def remove_unchanged_copy(path, expected):
    if expected is None:
        raise OSError('Нет данных для безопасной отмены. Копия сохранена.')
    with guarded_source(path, removable=True) as stream:
        if file_state(path) != expected:
            raise OSError('Копия изменена или заменена после копирования. Файл сохранён.')
        remove_open_source(stream, path)


def perform_file_operation(kind, source, target, progress=None, receipt=None):
    source, target = Path(source), Path(target)
    if kind not in {'copy', 'move'}:
        raise ValueError(f'Неизвестная операция: {kind}')
    if not source.is_file():
        raise FileNotFoundError(f'Исходный файл не найден: {source}')
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(target):
        raise FileExistsError(f'Файл назначения уже существует: {target}')
    total = source.stat().st_size
    if kind == 'move' and _same_device(source, target.parent):
        rename_no_replace(source, target)
        if progress:
            progress(total, total)
        return target

    # Hold the source through publication and delete using the same handle.
    with guarded_source(source, removable=kind == 'move') as src:
        expected = file_state(source)
        fd, name = tempfile.mkstemp(prefix='.mediacategorizer-', suffix='.part', dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as dst:
                copied = 0
                while chunk := src.read(16 * 1024 * 1024):
                    dst.write(chunk)
                    copied += len(chunk)
                    if progress:
                        progress(copied, total)
                dst.flush()
                os.fsync(dst.fileno())
            if file_state(source) != expected or copied != total:
                raise OSError('Источник изменился во время копирования. Исходный файл сохранён.')
            shutil.copystat(source, temporary)
            result_state = file_state(temporary)
            if file_state(source) != expected:
                raise OSError('Источник изменился. Исходный файл сохранён.')
            rename_no_replace(temporary, target)
            if receipt is not None:
                # NTFS name tunnelling may restore an older creation time on rename.
                published = file_state(target)
                receipt['result_state'] = published if published[:4] == result_state[:4] else result_state
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except PermissionError:
                temporary.chmod(stat.S_IWRITE)
                temporary.unlink()
        if kind == 'move':
            try:
                if file_state(source) != expected:
                    raise OSError('Источник изменился после копирования')
                remove_open_source(src, source)
            except OSError as exc:
                raise PublishedMoveError(
                    f'Копия сохранена: {target}\nНе удалось удалить источник: {source}\n'
                    'Оба файла оставлены для восстановления.'
                ) from exc
    if progress:
        progress(total, total)
    return target


def path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def same_path(left, right):
    if path_key(left) == path_key(right):
        return True
    try:
        return Path(left).samefile(right)
    except OSError:
        return False


class FileReservations:
    """Main-thread registry of sources and destinations of pending operations."""
    def __init__(self):
        self._operations = {}

    def is_busy(self, *paths):
        return any(same_path(path, reserved)
                   for path in paths
                   for items in self._operations.values()
                   for reserved in items)

    def acquire(self, operation_id, *paths):
        if operation_id in self._operations or self.is_busy(*paths):
            raise RuntimeError("Файл занят фоновой операцией. Дождитесь её завершения.")
        self._operations[operation_id] = tuple(Path(path) for path in paths)

    def release(self, operation_id):
        self._operations.pop(operation_id, None)
