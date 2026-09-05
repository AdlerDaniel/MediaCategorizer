"""Filesystem operations independent of Qt; destinations are never overwritten."""
import os
import shutil
import stat
import tempfile
from pathlib import Path


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


def perform_file_operation(kind, source, target, progress=None):
    source, target = Path(source), Path(target)
    if kind not in {"copy", "move"}:
        raise ValueError(f"Неизвестная операция: {kind}")
    if not source.is_file():
        raise FileNotFoundError(f"Исходный файл не найден: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(target):
        raise FileExistsError(f"Файл назначения уже существует: {target}")
    total = source.stat().st_size
    if kind == "move" and _same_device(source, target.parent):
        rename_no_replace(source, target)
        if progress:
            progress(total, total)
        return target

    # Only this uniquely created staging file may be removed on failure.
    fd, temporary_name = tempfile.mkstemp(prefix=".mediacategorizer-", suffix=".part", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as dst, source.open("rb") as src:
            copied = 0
            while chunk := src.read(16 * 1024 * 1024):
                dst.write(chunk)
                copied += len(chunk)
                if progress:
                    progress(copied, total)
            dst.flush()
            os.fsync(dst.fileno())
        shutil.copystat(source, temporary)
        rename_no_replace(temporary, target)
    finally:
        # Never unlink target: another application may have created that path.
        try:
            temporary.unlink(missing_ok=True)
        except PermissionError:
            # copystat may have copied a Windows read-only attribute to our temp.
            temporary.chmod(stat.S_IWRITE)
            temporary.unlink()

    if kind == "move":
        try:
            source.unlink()
        except OSError as exc:
            raise PublishedMoveError(
                f"Копия сохранена: {target}\nНе удалось удалить источник: {source}\n"
                "Оба файла оставлены для восстановления."
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
