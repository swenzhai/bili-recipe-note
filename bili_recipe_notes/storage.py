from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class StorageError(RuntimeError):
    """Base error for durable local storage operations."""


class CorruptDataError(StorageError):
    """Raised when a persisted JSON document cannot be trusted."""


class FileLockTimeout(StorageError):
    """Raised when another process keeps a storage file locked too long."""


def backup_path(path: str | Path) -> Path:
    target = Path(path)
    return target.with_name(f"{target.name}.bak")


def _write_replacement(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except (AttributeError, OSError):
            return
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        finally:
            os.close(directory_fd)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: str | Path, data: bytes, *, backup: bool = True) -> Path:
    """Atomically replace a file and retain its previous contents as ``.bak``."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup and target.exists():
        try:
            previous = target.read_bytes()
        except OSError as exc:
            raise StorageError(f"Cannot back up {target}: {exc}") from exc
        _write_replacement(backup_path(target), previous)
    try:
        _write_replacement(target, data)
    except OSError as exc:
        raise StorageError(f"Cannot write {target}: {exc}") from exc
    return target


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    backup: bool = True,
) -> Path:
    return atomic_write_bytes(path, text.encode(encoding), backup=backup)


def atomic_write_json(path: str | Path, value: Any, *, backup: bool = True) -> Path:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    return atomic_write_text(path, text, backup=backup)


def read_json(path: str | Path, *, expected_type: type | tuple[type, ...] | None = None) -> Any:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"Cannot read {source}: {exc}") from exc
    try:
        value = json.loads(text)
    except (UnicodeError, json.JSONDecodeError) as exc:
        recovery = backup_path(source)
        hint = f" A backup is available at {recovery}." if recovery.exists() else ""
        raise CorruptDataError(f"Invalid JSON in {source}: {exc}.{hint}") from exc
    if expected_type is not None and not isinstance(value, expected_type):
        expected = (
            ", ".join(item.__name__ for item in expected_type)
            if isinstance(expected_type, tuple)
            else expected_type.__name__
        )
        raise CorruptDataError(
            f"Invalid JSON structure in {source}: expected {expected}, got {type(value).__name__}."
        )
    return value


@contextmanager
def file_lock(
    path: str | Path,
    *,
    timeout: float = 10.0,
    stale_after: float = 120.0,
) -> Iterator[None]:
    """Serialize read-modify-write operations using an adjacent lock file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f"{target.name}.lock")
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > stale_after:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise FileLockTimeout(f"Timed out waiting for storage lock: {lock_path}")
            time.sleep(0.05)
    try:
        os.write(descriptor, f"pid={os.getpid()} created={time.time()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)
