"""Explicit, resumable migration of legacy personal data; never merge recipes."""
from __future__ import annotations

import hashlib
import errno
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .creator_paths import creator_directory, creator_uid
from .data_paths import data_root, state_dir
from .output_layout import iter_output_folders
from .storage import atomic_write_json, file_lock, read_json


def _object(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = read_json(path)
    return value if isinstance(value, dict) else {}


def _artifact(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    if any((folder / name).is_file() for name in ("source.json", "job.json")):
        return True
    # PDF/parser intermediates may also be named recipe.json, but are source
    # material rather than generated app recipes and must retain their tree.
    recipe = _object(folder / "recipe.json")
    return bool(recipe.get("source_url")) and isinstance(recipe.get("steps"), list)


def _legacy_roots(project: Path, destination: Path) -> list[Path]:
    excluded = {".git", ".venv", "bili_recipe_notes", "tests", "web", "mobile", "data", "examples", "scripts",
                "creators", "deployments", "obsidian-vault"}
    roots = [
        path for path in sorted(project.iterdir())
        if path.is_dir() and not path.is_symlink() and path.name not in excluded
        and not path.name.startswith(".") and path != destination
        and (path.name.startswith("outputs") or any(
            _artifact(folder) for folder in iter_output_folders(path) if not folder.is_relative_to(destination)
        ))
    ]
    return roots


def plan_data_migration(project_root: str | Path | None = None) -> dict:
    project = Path(project_root or Path.cwd()).resolve()
    dest = data_root(project)
    manifest = dest / "state" / "migrations" / "data-layout.json"
    if manifest.is_file():
        previous = _object(manifest)
        if previous.get("status") != "completed":
            return previous
    roots = _legacy_roots(project, dest)
    plan: dict[str, Any] = {
        "version": 1, "status": "planned", "project_root": str(project), "data_root": str(dest),
        "created_at": datetime.now(timezone.utc).isoformat(), "moves": [], "conflicts": [],
        "output_roots": [str(root) for root in roots], "recipe_paths": {},
        "path_mappings": {
            str(project / old): new for old, new in (
                ("outputs", "recipes"), ("creators", "creators"), (".bili-recipe-notes", "state"),
                ("deployments", "exports/deployments"), ("obsidian-vault", "exports/obsidian-vault"),
            )
        },
        "manifest_path": str(manifest),
    }
    claimed: set[Path] = set()

    def add(source: Path, target: Path, kind: str = "data") -> None:
        if target in claimed or target.exists():
            plan["conflicts"].append(f"Target already exists: {target}")
        if source.is_symlink():
            plan["conflicts"].append(f"Symlink requires manual migration: {source}")
        if target.is_relative_to(source):
            plan["conflicts"].append(f"Destination is inside source: {source}")
        claimed.add(target)
        plan["moves"].append({"source": str(source), "target": str(target), "kind": kind})
        plan["path_mappings"][str(source)] = target.relative_to(dest).as_posix()

    # The newest full crawl remains the current list; older lists are preserved
    # below history, including lists saved under an old output directory.
    catalog = []
    for base in [project / "creators", *(root / "creators" for root in roots)]:
        if base.is_dir():
            for folder in sorted(base.iterdir()):
                if folder.is_dir() and (folder / "creator.json").is_file():
                    meta = _object(folder / "creator.json")
                    name = folder.name
                    if meta.get("uid") and name.startswith(str(meta["uid"]) + "-"):
                        name = name.split("-", 1)[1]
                    meta["creator_name"] = name
                    catalog.append((folder, meta))
                else:
                    add(folder, dest / "creators" / "history" / base.parent.name / folder.name)
    catalog.sort(key=lambda item: str(item[1].get("crawled_at") or ""), reverse=True)
    creator_targets: dict[str, Path] = {}
    creator_metadata: dict[str, dict] = {}
    by_url: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for folder, meta in catalog:
        identity = str(meta.get("uid") or meta["creator_name"])
        preferred = creator_metadata.setdefault(identity, meta)
        canonical = creator_targets.setdefault(identity, creator_directory(dest / "creators", meta))
        if canonical not in claimed and not canonical.exists():
            add(folder, canonical)
        else:
            add(folder, dest / "creators" / "history" / canonical.name / f"{folder.parent.parent.name}-{folder.name}")
        by_name.setdefault(str(meta["creator_name"]), preferred)
        if meta.get("uploader"):
            by_name.setdefault(str(meta["uploader"]), preferred)
        for links in sorted(folder.glob("*.txt")):
            for line in links.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("https://"):
                    by_url.setdefault(line.strip().rstrip("/"), preferred)

    for root in roots:
        plan["path_mappings"][str(root)] = "recipes"
        outputs = {folder for folder in iter_output_folders(root) if _artifact(folder)}

        def plan_entry(folder: Path) -> None:
            if folder in outputs:
                metadata: dict[str, Any] = {}
                for filename in ("job.json", "source.json", "recipe.json"):
                    metadata.update({key: value for key, value in _object(folder / filename).items() if value is not None})
                url = str(metadata.get("source_url") or "").rstrip("/")
                uid = creator_uid(metadata)
                creator = creator_metadata.get(uid) if uid else (
                    by_url.get(url) or by_name.get(str(metadata.get("creator_name") or metadata.get("uploader") or ""))
                )
                if creator:
                    metadata = {**metadata, "creator_uid": creator.get("uid"), "creator_name": creator["creator_name"]}
                target = creator_directory(dest / "recipes", metadata) / folder.name
                if target in claimed or target.exists():
                    target = target.parent / f"from-{root.name}" / folder.relative_to(root)
                add(folder, target, "recipe")
                plan["recipe_paths"][str(folder)] = target.relative_to(dest / "recipes").as_posix()
            elif folder.is_dir() and any(output.is_relative_to(folder) for output in outputs):
                for child in sorted(folder.iterdir()):
                    plan_entry(child)
            elif folder.name == "curation-review":
                target = dest / "recipes" / "curation-review"
                if target in claimed or target.exists():
                    target = dest / "sources" / root.name / folder.relative_to(root)
                add(folder, target)
            elif folder.suffix == ".zip":
                add(folder, dest / "exports" / "legacy" / root.name / folder.relative_to(root))
            else:
                add(folder, dest / "sources" / root.name / folder.relative_to(root))

        for folder in sorted(root.iterdir()):
            if folder.name not in {"creators", ".ai-review"}:
                plan_entry(folder)

    for root in roots:
        runtime = root / ".ai-review"
        if not runtime.is_dir():
            continue
        for entry in sorted(runtime.iterdir()):
            if entry.name == "history":
                old_keys = {
                    hashlib.sha256(Path(old).name.encode()).hexdigest()[:20]: rel
                    for old, rel in plan["recipe_paths"].items() if Path(old).parent == root
                }
                for bucket in sorted(entry.iterdir()):
                    identity = old_keys.get(bucket.name)
                    key = hashlib.sha256(identity.encode()).hexdigest()[:20] if identity else bucket.name
                    add(bucket, dest / "recipes" / ".ai-review" / "history" / key)
            else:
                add(entry, dest / "recipes" / ".ai-review" / entry.name, "audit_state")

    legacy_state = project / ".bili-recipe-notes"
    if legacy_state.is_dir():
        plan["path_mappings"][str(legacy_state)] = "state"
        for entry in sorted(legacy_state.iterdir()):
            target = dest / "state" / entry.name
            if entry.name == "migrations":
                target /= "legacy"
            elif entry.name == "layout.json":
                target = dest / "state" / "migrations" / "legacy-layout.json"
            add(entry, target, "state")
    for name, relative in [("deployments", "exports/deployments"), ("obsidian-vault", "exports/obsidian-vault")]:
        folder = project / name
        if folder.is_dir():
            add(folder, dest / relative)
    return plan


def _inventory(path: Path) -> dict[str, dict]:
    if path.is_symlink():
        raise ValueError(f"Symlink requires manual migration: {path}")
    if not path.exists():
        raise FileNotFoundError(f"Missing migration source: {path}")
    paths = sorted(path.rglob("*")) if path.is_dir() else [path]
    result = {}
    for item in paths:
        if item.is_symlink():
            raise ValueError(f"Symlink requires manual migration: {item}")
        if not item.is_file():
            continue
        digest = hashlib.sha256()
        with item.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        result[item.relative_to(path).as_posix() if path.is_dir() else "."] = {
            "size": item.stat().st_size, "sha256": digest.hexdigest(),
        }
    return result


def _process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            # ERROR_INVALID_PARAMETER means that the PID no longer exists;
            # access-denied and other unknown states must remain locked.
            return ctypes.get_last_error() != 87
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _release_abandoned_lock(manifest: Path) -> None:
    path = manifest.with_name(f"{manifest.name}.lock")
    if not path.exists():
        return
    before = path.stat()
    content = path.read_text(encoding="ascii")
    match = re.fullmatch(r"pid=([1-9][0-9]*) created=[0-9.]+\s*", content)
    if not match:
        raise RuntimeError(f"Invalid migration lock; inspect it before retrying: {path}")
    # A reused PID is conservatively treated as a live owner. Unlike a time
    # limit, this never discards a lock merely because a migration is slow.
    if not _process_alive(int(match.group(1))) and path.stat() == before:
        path.unlink()


def _assert_idle(plan: dict) -> None:
    project, dest = Path(plan["project_root"]), Path(plan["data_root"])
    candidates = [*(project / ".bili-recipe-notes" / "batches" / "runtime").glob("*.status.json"),
                  *(dest / "state" / "batches" / "runtime").glob("*.status.json")]
    for root in [*(Path(root) for root in plan["output_roots"]), dest / "recipes"]:
        candidates.extend((root / ".ai-review").glob("*.json"))
    for path in candidates:
        state = _object(path)
        pid = state.get("pid")
        if (state.get("status") in {"queued", "running", "starting", "stopping", "paused"}
                and type(pid) is int and pid > 0 and _process_alive(pid)):
            raise RuntimeError(f"Stop the running task before migration: {path}")


def _update_operational_state(plan: dict) -> None:
    """Only runtime state changes; all generated artifacts remain byte identical."""
    from .data_paths import relocate_value

    project, dest = Path(plan["project_root"]), Path(plan["data_root"])
    previous = _object(dest / "state" / "layout.json")
    previous_mappings = previous.get("path_mappings") or {}
    if not isinstance(previous_mappings, dict):
        raise ValueError("Invalid existing data layout path mappings")
    layout = {
        **previous, "version": plan["version"],
        "project_root": previous.get("project_root") or plan["project_root"],
        "data_root": str(dest),
        "data_roots": list(dict.fromkeys([
            *(previous.get("data_roots") or []), previous.get("data_root") or str(dest), str(dest),
        ])),
        "path_mappings": {**previous_mappings, **plan["path_mappings"]},
    }
    atomic_write_json(dest / "state" / "layout.json", layout)
    # Readers relocate batch/config/knowledge paths in memory using layout.json.
    # AI task folders are relative to their old output root; scope must be
    # translated explicitly when several libraries become one.
    for move in plan["moves"]:
        path = Path(move["target"])
        if move["kind"] != "audit_state" or path.suffix != ".json":
            continue
        state = _object(path)
        if not isinstance(state.get("items"), list) or not state.get("out_dir"):
            continue
        old_root = Path(state["out_dir"])
        if not old_root.is_absolute():
            old_root = project / old_root
        updated = dict(state)
        updated["out_dir"] = str(dest / "recipes")
        updated["items"] = [dict(item) for item in state["items"]]
        for item in updated["items"]:
            old = Path(item["folder"])
            old = old if old.is_absolute() else old_root / old
            item["folder"] = plan["recipe_paths"].get(str(old), item["folder"])
        if updated.get("current_folder"):
            old = Path(updated["current_folder"])
            old = old if old.is_absolute() else old_root / old
            updated["current_folder"] = plan["recipe_paths"].get(str(old), updated["current_folder"])
        if updated != state:
            original = dest / "state" / "migrations" / "originals" / "ai-review" / path.name
            if not original.exists():
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, original)
            atomic_write_json(path, updated, backup=False)
    database = dest / "state" / "mobile-sync.sqlite3"
    if database.is_file():
        with sqlite3.connect(database) as connection:
            backup = dest / "state" / "migrations" / "originals" / database.name
            if not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(backup) as target:
                    connection.backup(target)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, column in (("recipes", "output_folder"), ("assets", "path")):
                if table not in tables:
                    continue
                rows = connection.execute(f"SELECT rowid,{column} FROM {table}").fetchall()
                relocated = relocate_value([value for _, value in rows], project)
                for (rowid, value), updated in zip(rows, relocated):
                    if value != updated:
                        connection.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (updated, rowid))
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Mobile database integrity check failed")


def _remove_source(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _move_verified(source: Path, target: Path, expected: dict) -> None:
    """Rename on one filesystem, stage and verify before removing cross-device data."""
    if target.exists():
        # A process can stop after copying, or during removal of the old tree.
        # Only remove remaining old files when the entire target is verified
        # and every remaining source byte is already present there.
        if _inventory(target) != expected:
            raise FileExistsError(f"Unverified migration destination: {target}")
        remaining = _inventory(source)
        if any(expected.get(name) != info for name, info in remaining.items()):
            raise RuntimeError(f"Migration source changed: {source}")
        _remove_source(source)
        return
    if _inventory(source) != expected:
        raise RuntimeError(f"Migration source changed: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        source.rename(target)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    token = hashlib.sha256(str(source).encode()).hexdigest()[:12]
    staging = target.with_name(f".{target.name}.migration-{token}")
    if staging.exists() and _inventory(staging) != expected:
        _remove_source(staging)
    if not staging.exists():
        if source.is_dir():
            shutil.copytree(source, staging)
        else:
            shutil.copy2(source, staging)
    if _inventory(staging) != expected:
        raise RuntimeError(f"Cross-device copy verification failed: {source}")
    staging.rename(target)
    _remove_source(source)


def apply_data_migration(plan: dict) -> dict:
    if plan.get("status") == "completed":
        return plan
    if plan["conflicts"]:
        raise FileExistsError("; ".join(plan["conflicts"]))
    if not plan["moves"]:
        return {**plan, "status": "completed", "file_count": 0}
    _assert_idle(plan)
    manifest = Path(plan["manifest_path"])
    _release_abandoned_lock(manifest)
    with file_lock(manifest, timeout=1, stale_after=float("inf")):
        if not plan.get("inventory"):
            # Validate all files and destinations before changing any source.
            plan["inventory"] = {}
            for move in plan["moves"]:
                if Path(move["target"]).exists():
                    raise FileExistsError(move["target"])
                plan["inventory"][move["source"]] = _inventory(Path(move["source"]))
            plan["status"] = "moving"
            atomic_write_json(manifest, plan, backup=False)
        try:
            for move in plan["moves"]:
                source, target = Path(move["source"]), Path(move["target"])
                if source.exists():
                    _move_verified(source, target, plan["inventory"][str(source)])
                if not target.exists():
                    raise FileNotFoundError(f"Missing migration source and target: {source}")
                verify = plan.get("status") != "updating" or move["kind"] not in {"state", "audit_state"}
                if verify and _inventory(target) != plan["inventory"][str(source)]:
                    raise RuntimeError(f"File verification failed: {target}")
            plan["status"] = "updating"
            atomic_write_json(manifest, plan, backup=False)
            _update_operational_state(plan)
            # Remove only empty old directory shells, including creator roots.
            project = Path(plan["project_root"])
            shells = [*(Path(root) for root in plan["output_roots"]), project / "creators", project / ".bili-recipe-notes"]
            for shell in shells:
                if shell.exists():
                    for directory in sorted([*shell.rglob("*"), shell], key=lambda p: len(p.parts), reverse=True):
                        if directory.is_dir() and not directory.is_symlink():
                            try:
                                directory.rmdir()
                            except OSError:
                                pass
            plan.update(status="completed", completed_at=datetime.now(timezone.utc).isoformat(),
                        file_count=sum(len(files) for files in plan["inventory"].values()))
            atomic_write_json(manifest, plan, backup=False)
            return plan
        except Exception as exc:
            plan["last_error"] = f"{type(exc).__name__}: {exc}"
            atomic_write_json(manifest, plan, backup=False)
            raise
