from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from bili_recipe_notes.file_cache import FileReadCache


def test_cached_json_is_copied_and_unchanged_files_are_not_reread(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "recipe.json"
    path.write_text('{"steps":[{"title":"切配"}]}', encoding="utf-8")
    cache = FileReadCache(settle_seconds=0)
    reads = []
    original = Path.read_bytes

    def reading(target: Path) -> bytes:
        reads.append(target)
        return original(target)

    monkeypatch.setattr(Path, "read_bytes", reading)
    first = cache.read_json(path)
    first["steps"][0]["title"] = "changed"
    assert cache.read_json(path)["steps"][0]["title"] == "切配"
    assert cache.sha256(path) == hashlib.sha256(original(path)).hexdigest()
    assert reads == [path]


def test_same_size_atomic_replacement_invalidates_json_and_hash(tmp_path: Path) -> None:
    path = tmp_path / "recipe.json"
    path.write_text('{"v":1}', encoding="utf-8")
    before = path.stat()
    cache = FileReadCache()
    assert cache.read_json(path) == {"v": 1}
    digest = cache.sha256(path)
    replacement = tmp_path / "new.json"
    replacement.write_text('{"v":2}', encoding="utf-8")
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    replacement.replace(path)
    assert cache.read_json(path) == {"v": 2}
    assert cache.sha256(path) != digest


def test_fresh_files_are_reread_when_filesystem_timestamps_collide(tmp_path: Path) -> None:
    path = tmp_path / "recipe.json"
    cache = FileReadCache()
    for value in range(10):
        path.write_text('{"v":' + str(value) + '}', encoding="utf-8")
        assert cache.read_json(path) == {"v": value}


def test_deleted_and_invalid_files_do_not_return_cached_content(tmp_path: Path) -> None:
    path = tmp_path / "recipe.json"
    cache = FileReadCache()
    path.write_text('{"v":1}', encoding="utf-8")
    cache.read_json(path)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        cache.read_json(path)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        cache.read_json(path)
    path.write_text('{"v":2}', encoding="utf-8")
    assert cache.read_json(path) == {"v": 2}


def test_cache_retries_reads_after_transient_failure(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "recipe.json"
    path.write_text('{"v":1}', encoding="utf-8")
    cache = FileReadCache()
    original = Path.read_bytes
    attempts = []

    def flaky(target: Path) -> bytes:
        attempts.append(target)
        if len(attempts) == 1:
            raise PermissionError("temporarily unavailable")
        return original(target)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    with pytest.raises(PermissionError):
        cache.read_json(path)
    assert cache.read_json(path) == {"v": 1}
    assert len(attempts) == 2


def test_cache_evicts_least_recently_used_entries(tmp_path: Path, monkeypatch) -> None:
    cache = FileReadCache(max_entries=1)
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_text("{}", encoding="utf-8")
    second.write_text("[]", encoding="utf-8")
    cache.read_json(first)
    cache.read_json(second)
    original = Path.read_bytes
    reads = []

    def reading(path: Path) -> bytes:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reading)
    assert cache.read_json(first) == {}
    assert reads == [first]
