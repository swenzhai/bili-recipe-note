from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from .config import CONFIG_DIR_NAME
from .storage import atomic_write_bytes, atomic_write_json
from .utils import build_output_folder_name


@dataclass(frozen=True)
class OutputFolderRename:
    source: Path
    target: Path
    title: str
    source_url: str


@dataclass(frozen=True)
class OutputFolderMigrationResult:
    planned: int
    renamed: int
    updated_documents: int
    manifest_path: Path | None


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _pid_is_alive(pid: Any) -> bool:
    try:
        numeric_pid = int(pid)
    except (TypeError, ValueError):
        return False
    if numeric_pid <= 0:
        return False
    try:
        os.kill(numeric_pid, 0)
    except OSError:
        return False
    return True


def _assert_no_running_batches(project_root: Path) -> None:
    runtime_root = project_root / CONFIG_DIR_NAME / "batches" / "runtime"
    for status_path in runtime_root.glob("*.status.json"):
        status = _read_object(status_path)
        if status.get("status") == "running" and _pid_is_alive(status.get("pid")):
            raise RuntimeError(
                f"Cannot rename output folders while batch {status.get('batch_id') or status_path.stem} is running"
            )


def _source_identity(metadata: dict[str, Any], source_url: str) -> tuple[str | None, str | None, str]:
    video_id = str(metadata.get("bvid") or metadata.get("id") or "").strip()
    if not video_id:
        match = re.search(r"/(BV[0-9A-Za-z]+)(?:[/?#]|$)", source_url, flags=re.IGNORECASE)
        if match:
            video_id = match.group(1)
    cid = str(metadata.get("cid") or "").strip()
    if cid:
        return video_id or None, cid, "cid"
    part_id = str(metadata.get("part_id") or "").strip()
    part_label = str(metadata.get("part_label") or "").strip().lower()
    if part_id:
        return video_id or None, part_id, part_label if part_label in {"cid", "p"} else "p"
    page = (parse_qs(urlparse(source_url).query).get("p") or [None])[0]
    return video_id or None, str(page) if page else None, "p"


def desired_output_folder(folder: str | Path) -> Path:
    source = Path(folder)
    recipe = _read_object(source / "recipe.json")
    metadata = {**_read_object(source / "job.json"), **_read_object(source / "source.json")}
    title = str(recipe.get("title") or "").strip() or "待整理"
    source_url = str(recipe.get("source_url") or metadata.get("source_url") or "").strip()
    video_id, part_id, part_label = _source_identity(metadata, source_url)
    name = build_output_folder_name(
        title,
        None,
        video_id=video_id,
        part_id=part_id,
        part_label=part_label,
        source_url=source_url,
    )
    return source.parent / name


def plan_output_folder_migration(out_dir: str | Path) -> list[OutputFolderRename]:
    root = Path(out_dir).expanduser().resolve()
    if not root.is_dir():
        return []
    plans: list[OutputFolderRename] = []
    targets: dict[Path, Path] = {}
    for source in sorted(path for path in root.iterdir() if path.is_dir() and path.name != "creators"):
        target = desired_output_folder(source)
        if target == source:
            continue
        if target in targets and targets[target] != source:
            raise FileExistsError(f"Multiple output folders map to the same target: {target}")
        if target.exists():
            raise FileExistsError(f"Output folder target already exists: {target}")
        recipe = _read_object(source / "recipe.json")
        metadata = {**_read_object(source / "job.json"), **_read_object(source / "source.json")}
        plans.append(
            OutputFolderRename(
                source=source,
                target=target,
                title=str(recipe.get("title") or "").strip() or "待整理",
                source_url=str(recipe.get("source_url") or metadata.get("source_url") or "").strip(),
            )
        )
        targets[target] = source
    return plans


def _path_variants(path: Path, project_root: Path) -> list[str]:
    variants = [str(path.resolve()), path.resolve().as_posix()]
    try:
        relative = path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return list(dict.fromkeys(variants))
    variants.extend((str(relative), relative.as_posix()))
    return list(dict.fromkeys(variants))


def _path_replacements(
    plans: Iterable[OutputFolderRename], project_root: Path
) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for plan in plans:
        old_variants = _path_variants(plan.source, project_root)
        new_variants = _path_variants(plan.target, project_root)
        for old, new in zip(old_variants, new_variants):
            replacements[old.rstrip("/\\")] = new.rstrip("/\\")
    return replacements


def _remap_text(value: str, replacements: dict[str, str]) -> str:
    direct = replacements.get(value.rstrip("/\\"))
    if direct is not None and value.rstrip("/\\") == value:
        return direct
    candidate = value
    while candidate:
        split_at = max(candidate.rfind("/"), candidate.rfind("\\"))
        if split_at < 0:
            break
        candidate = candidate[:split_at]
        replacement = replacements.get(candidate.rstrip("/\\"))
        if replacement is not None:
            return replacement + value[len(candidate):]
    return value


def _remap_value(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _remap_text(value, replacements)
    if isinstance(value, list):
        return [_remap_value(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _remap_value(item, replacements) for key, item in value.items()}
    return value


def _rewrite_json_document(
    path: Path,
    replacements: dict[str, str],
    originals: dict[Path, bytes],
) -> bool:
    value = _read_object(path)
    if not value:
        return False
    updated = _remap_value(value, replacements)
    if updated == value:
        return False
    originals.setdefault(path, path.read_bytes())
    atomic_write_json(path, updated)
    return True


def _update_mobile_database(database_path: Path, replacements: dict[str, str]) -> int:
    if not database_path.is_file():
        return 0
    changed = 0
    with sqlite3.connect(database_path, timeout=15.0) as connection:
        for table, column in (("recipes", "output_folder"), ("assets", "path")):
            rows = connection.execute(f"SELECT rowid,{column} FROM {table}").fetchall()
            for rowid, value in rows:
                updated = _remap_text(str(value), replacements)
                if updated == value:
                    continue
                connection.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (updated, rowid))
                changed += 1
    return changed


def apply_output_folder_migration(
    plans: list[OutputFolderRename],
    *,
    project_root: str | Path | None = None,
    write_manifest: bool = True,
) -> OutputFolderMigrationResult:
    root = Path(project_root or Path.cwd()).expanduser().resolve()
    if not plans:
        return OutputFolderMigrationResult(0, 0, 0, None)
    _assert_no_running_batches(root)
    for plan in plans:
        if not plan.source.is_dir():
            raise FileNotFoundError(f"Output folder no longer exists: {plan.source}")
        if plan.target.exists():
            raise FileExistsError(f"Output folder target already exists: {plan.target}")

    renamed: list[OutputFolderRename] = []
    originals: dict[Path, bytes] = {}
    updated_documents = 0
    replacements = _path_replacements(plans, root)
    try:
        for plan in plans:
            plan.source.rename(plan.target)
            renamed.append(plan)
        for plan in plans:
            job_path = plan.target / "job.json"
            if job_path.is_file() and _rewrite_json_document(job_path, replacements, originals):
                updated_documents += 1
        batches_root = root / CONFIG_DIR_NAME / "batches"
        for path in sorted(batches_root.glob("*.json")):
            if _rewrite_json_document(path, replacements, originals):
                updated_documents += 1
        runtime_root = batches_root / "runtime"
        for path in sorted(runtime_root.glob("*.result.json")):
            if _rewrite_json_document(path, replacements, originals):
                updated_documents += 1
        updated_documents += _update_mobile_database(root / CONFIG_DIR_NAME / "mobile-sync.sqlite3", replacements)
    except Exception:
        for path, content in originals.items():
            atomic_write_bytes(path, content, backup=False)
        for plan in reversed(renamed):
            if plan.target.exists() and not plan.source.exists():
                plan.target.rename(plan.source)
        raise

    manifest_path = None
    if write_manifest:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        manifest_path = root / CONFIG_DIR_NAME / "migrations" / f"output-folders-{stamp}.json"
        atomic_write_json(
            manifest_path,
            {
                "version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "project_root": str(root),
                "renames": [
                    {
                        **asdict(plan),
                        "source": str(plan.source),
                        "target": str(plan.target),
                    }
                    for plan in plans
                ],
                "updated_documents": updated_documents,
            },
            backup=False,
        )
    return OutputFolderMigrationResult(
        planned=len(plans),
        renamed=len(renamed),
        updated_documents=updated_documents,
        manifest_path=manifest_path,
    )


def rename_completed_output_folder(folder: str | Path) -> Path:
    source = Path(folder).expanduser().resolve()
    target = desired_output_folder(source)
    if target == source:
        return source
    if target.exists():
        raise FileExistsError(f"Output folder target already exists: {target}")
    plan = OutputFolderRename(source, target, "", "")
    source.rename(target)
    job_path = target / "job.json"
    if job_path.is_file():
        original = job_path.read_bytes()
        try:
            replacements = _path_replacements([plan], Path.cwd().resolve())
            _rewrite_json_document(job_path, replacements, {})
        except Exception:
            atomic_write_bytes(job_path, original, backup=False)
            target.rename(source)
            raise
    return target


def repair_output_folder_references(
    plans: list[OutputFolderRename],
    *,
    project_root: str | Path | None = None,
) -> OutputFolderMigrationResult:
    root = Path(project_root or Path.cwd()).expanduser().resolve()
    _assert_no_running_batches(root)
    replacements = _path_replacements(plans, root)
    originals: dict[Path, bytes] = {}
    updated_documents = 0
    try:
        for plan in plans:
            job_path = plan.target / "job.json"
            if job_path.is_file() and _rewrite_json_document(job_path, replacements, originals):
                updated_documents += 1
        batches_root = root / CONFIG_DIR_NAME / "batches"
        for path in sorted(batches_root.glob("*.json")):
            if _rewrite_json_document(path, replacements, originals):
                updated_documents += 1
        runtime_root = batches_root / "runtime"
        for path in sorted(runtime_root.glob("*.result.json")):
            if _rewrite_json_document(path, replacements, originals):
                updated_documents += 1
        updated_documents += _update_mobile_database(root / CONFIG_DIR_NAME / "mobile-sync.sqlite3", replacements)
    except Exception:
        for path, content in originals.items():
            atomic_write_bytes(path, content, backup=False)
        raise

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    manifest_path = root / CONFIG_DIR_NAME / "migrations" / f"output-folders-{stamp}.json"
    atomic_write_json(
        manifest_path,
        {
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(root),
            "recovered_interrupted_migration": True,
            "renames": [
                {
                    **asdict(plan),
                    "source": str(plan.source),
                    "target": str(plan.target),
                }
                for plan in plans
            ],
            "updated_documents": updated_documents,
        },
        backup=False,
    )
    return OutputFolderMigrationResult(len(plans), 0, updated_documents, manifest_path)
