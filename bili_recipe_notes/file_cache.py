"""Bounded, process-local reads shared by library scans and mobile indexing.

Every lookup checks file identity and timestamps. Cached JSON is copied before
returning it so preparing a mobile payload cannot modify another reader's data.
Failures are never cached, and no cache files are written into recipe folders.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FileFingerprint = tuple[int, int, int, int, int]
_UNPARSED = object()


def fingerprint_is_stable(fingerprint: FileFingerprint | None, *, settle_seconds: float = 2) -> bool:
    # Some filesystems report identical nanosecond timestamps for successive
    # writes within one clock tick. Fresh files must settle before stat alone
    # can identify a reusable snapshot. Normal 10-second scans still hit cache.
    return fingerprint is None or time.time_ns() - max(fingerprint[3:]) > settle_seconds * 1_000_000_000


@dataclass
class _Entry:
    fingerprint: FileFingerprint
    data: bytes | None = None
    parsed: Any = _UNPARSED
    digest: str | None = None


class FileReadCache:
    def __init__(self, *, max_entries: int = 16_384, max_bytes: int = 64 * 1024 * 1024, settle_seconds: float = 2) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.settle_seconds = settle_seconds
        self._entries: OrderedDict[Path, _Entry] = OrderedDict()
        self._lock = threading.RLock()
        self._bytes = 0

    @staticmethod
    def fingerprint(path: str | Path) -> FileFingerprint | None:
        try:
            stat = Path(path).stat()
        except FileNotFoundError:
            return None
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def _entry(self, path: Path, fingerprint: FileFingerprint) -> _Entry:
        existing = self._entries.get(path)
        if existing is not None and existing.fingerprint == fingerprint and fingerprint_is_stable(fingerprint, settle_seconds=self.settle_seconds):
            self._entries.move_to_end(path)
            return existing
        if existing is not None:
            self._bytes -= len(existing.data or b"")
        entry = _Entry(fingerprint)
        self._entries[path] = entry
        self._entries.move_to_end(path)
        return entry

    def _trim(self) -> None:
        while self._entries and (len(self._entries) > self.max_entries or self._bytes > self.max_bytes):
            _, entry = self._entries.popitem(last=False)
            self._bytes -= len(entry.data or b"")

    def _load(self, path: Path) -> _Entry:
        for _ in range(3):
            fingerprint = self.fingerprint(path)
            if fingerprint is None:
                previous = self._entries.pop(path, None)
                if previous is not None:
                    self._bytes -= len(previous.data or b"")
                raise FileNotFoundError(path)
            entry = self._entry(path, fingerprint)
            if entry.data is not None:
                return entry
            data = path.read_bytes()
            if self.fingerprint(path) != fingerprint:
                continue
            entry.data = data
            self._bytes += len(data)
            self._trim()
            return entry
        raise OSError(f"File changed while reading: {path}")

    def read_json(self, path: str | Path) -> Any:
        with self._lock:
            entry = self._load(Path(path).absolute())
            if entry.parsed is _UNPARSED:
                entry.parsed = json.loads(entry.data.decode("utf-8"))
            return deepcopy(entry.parsed)

    def read_text(self, path: str | Path) -> str:
        with self._lock:
            return self._load(Path(path).absolute()).data.decode("utf-8")

    def sha256(self, path: str | Path) -> str:
        target = Path(path).absolute()
        with self._lock:
            for _ in range(3):
                fingerprint = self.fingerprint(target)
                if fingerprint is None:
                    raise FileNotFoundError(target)
                entry = self._entry(target, fingerprint)
                if entry.digest is not None:
                    return entry.digest
                digest = hashlib.sha256()
                if entry.data is not None:
                    digest.update(entry.data)
                else:
                    with target.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                if self.fingerprint(target) != fingerprint:
                    continue
                entry.digest = digest.hexdigest()
                self._trim()
                return entry.digest
        raise OSError(f"File changed while hashing: {target}")


file_read_cache = FileReadCache()
read_json_cached = file_read_cache.read_json
read_text_cached = file_read_cache.read_text
file_sha256 = file_read_cache.sha256
file_fingerprint = file_read_cache.fingerprint
clear_file_cache = file_read_cache.clear
