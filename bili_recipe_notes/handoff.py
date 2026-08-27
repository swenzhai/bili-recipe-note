from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .batch_queue import (
    BATCH_ID_RE,
    BatchQueueItem,
    BatchQueueState,
    BatchStageState,
    batch_path,
    create_batch_id,
    load_batch_state,
    now_utc,
    save_batch_state,
)
from .history import is_complete_output, is_raw_output
from .storage import CorruptDataError, atomic_write_json, backup_path, read_json
from .utils import sanitize_filename


HANDOFF_FORMAT = "bili-recipe-notes-handoff"
HANDOFF_VERSION = 1
MAX_ARCHIVE_FILES = 100_000
MAX_ARCHIVE_FILE_SIZE = 2 * 1024**3
MAX_ARCHIVE_TOTAL_SIZE = 20 * 1024**3

PORTABLE_FILE_NAMES = {
    "source.json",
    "transcript.json",
    "job.json",
    "recipe.json",
    "note.md",
    "quality.json",
    "recipe.review.json",
    "extra_analysis.md",
    "extra_analysis.json",
    "sync-meta.json",
}
PATH_FIELDS = {
    "output_folder": ".",
    "source_path": "source.json",
    "transcript_path": "transcript.json",
    "recipe_path": "recipe.json",
    "note_path": "note.md",
    "job_path": "job.json",
}
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


class HandoffError(RuntimeError):
    """Raised when a handoff package is invalid or unsafe to import."""


@dataclass(frozen=True)
class HandoffExportResult:
    path: Path
    batch_id: str
    item_count: int
    raw_count: int
    recipe_count: int
    file_count: int
    size_bytes: int


@dataclass(frozen=True)
class HandoffImportResult:
    batch_id: str
    batch_path: Path
    item_count: int
    restored_count: int
    raw_count: int
    recipe_count: int
    pending_count: int
    creator_document_count: int
    backup_count: int


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _portable_job_bytes(path: Path) -> bytes:
    try:
        value = read_json(path, expected_type=dict)
    except Exception:
        return path.read_bytes()
    for key, portable_value in PATH_FIELDS.items():
        if key in value:
            value[key] = portable_value
    return _json_bytes(value)


def _portable_options(value: Any, *, key: str = "") -> Any:
    """Remove login material and local-only paths from an options snapshot."""

    lowered = key.lower()
    if "cookie" in lowered:
        return None
    if isinstance(value, dict):
        return {
            str(item_key): _portable_options(item_value, key=str(item_key))
            for item_key, item_value in value.items()
            if str(item_key) != "urls" and "cookie" not in str(item_key).lower()
        }
    if isinstance(value, list):
        return [_portable_options(item, key=key) for item in value]
    if isinstance(value, str) and (
        value.startswith("/") or value.startswith("~") or WINDOWS_ABSOLUTE_PATH_RE.match(value)
    ):
        return None
    return value


def _resolve_output_folder(value: str | None, project_root: Path) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path


def _artifact_stage(folder: Path | None) -> str | None:
    if folder is None or not folder.is_dir():
        return None
    if is_complete_output(folder):
        return "recipe"
    if is_raw_output(folder):
        return "raw"
    return None


def _portable_files(folder: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(folder)
        if any(part.startswith(".") for part in relative.parts) or path.name.endswith((".bak", ".tmp", ".lock")):
            continue
        if path.name in PORTABLE_FILE_NAMES or (relative.parts and relative.parts[0] == "images"):
            files.append(path)
    return files


def _creator_documents(out_dir: Path, urls: set[str]) -> list[tuple[Path, str]]:
    matches: list[tuple[Path, str]] = []
    for links_path in sorted((out_dir / "creators").glob("*/video_links.txt")):
        try:
            document_urls = {line.strip() for line in links_path.read_text(encoding="utf-8").splitlines()}
        except OSError:
            continue
        if not urls.intersection(document_urls):
            continue
        creator_name = sanitize_filename(links_path.parent.name)
        matches.append((links_path, f"creators/{creator_name}/video_links.txt"))
        manifest_path = links_path.parent / "creator.json"
        if manifest_path.is_file():
            matches.append((manifest_path, f"creators/{creator_name}/creator.json"))
    return matches


def _write_archive_member(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
    records: list[dict[str, Any]],
) -> None:
    archive.writestr(name, data)
    records.append({"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})


def export_batch_handoff(
    batch_id: str,
    out_dir: str | Path,
    destination: str | Path | None = None,
    *,
    project_root: str | Path | None = None,
) -> HandoffExportResult:
    """Export one batch and all durable work products as a portable ZIP."""

    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd().resolve()
    output_root = Path(out_dir).expanduser().resolve()
    state = load_batch_state(batch_id, project_root=root)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination_path = (
        Path(destination).expanduser()
        if destination
        else output_root / "handoffs" / f"{sanitize_filename(batch_id)}-{stamp}.handoff.zip"
    )
    if (destination_path.exists() and destination_path.is_dir()) or destination_path.suffix.lower() != ".zip":
        destination_path = destination_path / f"{sanitize_filename(batch_id)}-{stamp}.handoff.zip"
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    items: list[dict[str, Any]] = []
    item_sources: list[tuple[Path, str]] = []
    recipe_count = 0
    raw_count = 0
    for index, item in enumerate(state.items, start=1):
        folder = _resolve_output_folder(item.output_folder, root)
        stage = _artifact_stage(folder)
        archive_folder: str | None = None
        folder_name: str | None = None
        if stage and folder:
            folder_name = sanitize_filename(folder.name)
            archive_folder = f"outputs/{index:06d}-{folder_name}"
            item_sources.append((folder, archive_folder))
            if stage == "recipe":
                recipe_count += 1
            else:
                raw_count += 1
        items.append(
            {
                "url": item.url,
                "artifact_stage": stage,
                "folder_name": folder_name,
                "archive_folder": archive_folder,
            }
        )

    portable_batch = {
        "batch_id": state.batch_id,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "options": _portable_options(state.options),
        "items": items,
    }
    records: list[dict[str, Any]] = []
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.", suffix=".tmp", dir=destination_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            _write_archive_member(archive, "batch.json", _json_bytes(portable_batch), records)
            for folder, archive_folder in item_sources:
                for source in _portable_files(folder):
                    relative = source.relative_to(folder).as_posix()
                    data = _portable_job_bytes(source) if source.name == "job.json" else source.read_bytes()
                    _write_archive_member(archive, f"{archive_folder}/{relative}", data, records)
            urls = {item.url for item in state.items}
            for source, archive_name in _creator_documents(output_root, urls):
                _write_archive_member(archive, archive_name, source.read_bytes(), records)
            knowledge_path = root / ".bili-recipe-notes" / "knowledge_base.json"
            if knowledge_path.is_file():
                _write_archive_member(
                    archive,
                    ".bili-recipe-notes/knowledge_base.json",
                    knowledge_path.read_bytes(),
                    records,
                )
            import_dir = root / ".bili-recipe-notes" / "knowledge-imports"
            for source in sorted(import_dir.glob("*")):
                if source.is_file() and source.suffix.lower() not in {".bak", ".tmp"}:
                    _write_archive_member(
                        archive,
                        f".bili-recipe-notes/knowledge-imports/{source.name}",
                        source.read_bytes(),
                        records,
                    )
            manifest = {
                "format": HANDOFF_FORMAT,
                "version": HANDOFF_VERSION,
                "exported_at": now_utc(),
                "source_batch_id": state.batch_id,
                "item_count": len(state.items),
                "raw_count": raw_count,
                "recipe_count": recipe_count,
                "files": records,
            }
            archive.writestr("handoff.json", _json_bytes(manifest))
        if destination_path.exists():
            shutil.copy2(destination_path, backup_path(destination_path))
        os.replace(temporary_path, destination_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return HandoffExportResult(
        path=destination_path,
        batch_id=state.batch_id,
        item_count=len(state.items),
        raw_count=raw_count,
        recipe_count=recipe_count,
        file_count=len(records),
        size_bytes=destination_path.stat().st_size,
    )


def _safe_member_name(info: zipfile.ZipInfo) -> str:
    path = PurePosixPath(info.filename)
    if not info.filename or info.filename.startswith(("/", "\\")) or ".." in path.parts:
        raise HandoffError(f"交接包包含不安全路径：{info.filename!r}")
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if unix_mode and stat.S_ISLNK(unix_mode):
        raise HandoffError(f"交接包不允许符号链接：{info.filename!r}")
    if info.flag_bits & 0x1:
        raise HandoffError("交接包不能使用加密 ZIP 条目。")
    return path.as_posix()


def _validate_archive(archive: zipfile.ZipFile) -> tuple[dict[str, Any], dict[str, zipfile.ZipInfo]]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_FILES:
        raise HandoffError(f"交接包文件过多（上限 {MAX_ARCHIVE_FILES}）。")
    total_size = 0
    by_name: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = _safe_member_name(info)
        if name in by_name:
            raise HandoffError(f"交接包包含重复路径：{name}")
        if info.file_size > MAX_ARCHIVE_FILE_SIZE:
            raise HandoffError(f"交接包单个文件过大：{name}")
        total_size += info.file_size
        if total_size > MAX_ARCHIVE_TOTAL_SIZE:
            raise HandoffError("交接包解压后总大小超过安全上限。")
        by_name[name] = info
    if "handoff.json" not in by_name or "batch.json" not in by_name:
        raise HandoffError("这不是有效的工作交接包：缺少 handoff.json 或 batch.json。")
    try:
        manifest = json.loads(archive.read("handoff.json"))
    except (UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise HandoffError(f"交接包清单损坏：{exc}") from exc
    if not isinstance(manifest, dict):
        raise HandoffError("交接包清单格式错误。")
    if manifest.get("format") != HANDOFF_FORMAT or manifest.get("version") != HANDOFF_VERSION:
        raise HandoffError("不支持的交接包格式或版本。")

    expected: dict[str, tuple[int, str]] = {}
    for record in manifest.get("files") or []:
        if not isinstance(record, dict):
            raise HandoffError("交接包文件校验清单格式错误。")
        name = str(record.get("path") or "")
        if name in expected or name not in by_name:
            raise HandoffError(f"交接包文件校验清单不一致：{name!r}")
        raw_size = record.get("size")
        expected[name] = (int(raw_size) if raw_size is not None else -1, str(record.get("sha256") or ""))
    allowed_names = set(expected) | {"handoff.json"}
    unexpected = set(by_name) - allowed_names
    if unexpected:
        raise HandoffError(f"交接包含有未登记文件：{sorted(unexpected)[0]}")
    for name, (expected_size, expected_hash) in expected.items():
        digest = hashlib.sha256()
        size = 0
        with archive.open(by_name[name], "r") as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise HandoffError(f"交接包文件校验失败：{name}")
    return manifest, by_name


def _read_batch_snapshot(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        value = json.loads(archive.read("batch.json"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HandoffError(f"批次快照损坏：{exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise HandoffError("批次快照格式错误。")
    batch_id = str(value.get("batch_id") or "")
    if not BATCH_ID_RE.fullmatch(batch_id):
        raise HandoffError("批次快照中的 batch_id 无效。")
    return value


def _copy_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.copy2(destination, backup_path(destination))
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target, archive.open(info, "r") as source:
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _existing_source_url(folder: Path) -> str:
    for name in ("source.json", "job.json", "recipe.json"):
        try:
            value = read_json(folder / name, expected_type=dict)
        except Exception:
            continue
        url = str(value.get("source_url") or "").strip()
        if url:
            return url
    return ""


def _destination_folder(out_dir: Path, folder_name: str, url: str) -> Path:
    candidate = out_dir / sanitize_filename(folder_name)
    if not candidate.exists() or _existing_source_url(candidate) in {"", url}:
        return candidate
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return out_dir / sanitize_filename(f"{folder_name}-imported-{digest}")


def _restore_job_paths(folder: Path) -> None:
    path = folder / "job.json"
    if not path.is_file():
        return
    try:
        job = read_json(path, expected_type=dict)
    except Exception:
        return
    replacements = {
        "output_folder": str(folder),
        "source_path": str(folder / "source.json"),
        "transcript_path": str(folder / "transcript.json"),
        "recipe_path": str(folder / "recipe.json"),
        "note_path": str(folder / "note.md"),
        "job_path": str(folder / "job.json"),
    }
    for key, value in replacements.items():
        if key in job or key in {"output_folder", "source_path", "transcript_path"}:
            job[key] = value
    # _copy_member already preserved the previous local job.json. Do not replace
    # that backup with the just-imported portable version while remapping paths.
    atomic_write_json(path, job, backup=False)


def _reset_item_state(item: BatchQueueItem, stage: str | None, folder: Path | None) -> None:
    item.error = None
    item.started_at = None
    item.finished_at = None
    item.output_folder = str(folder) if folder else None
    item.note_path = str(folder / "note.md") if folder and stage == "recipe" else None
    item.stages = {
        "raw": BatchStageState(status="done" if stage in {"raw", "recipe"} else "pending"),
        "recipe": BatchStageState(status="done" if stage == "recipe" else "pending"),
    }
    item.status = "done" if stage == "recipe" else "raw_ready" if stage == "raw" else "pending"


def _stage_rank(item: BatchQueueItem) -> int:
    if item.stages.get("recipe", BatchStageState()).status == "done":
        return 2
    if item.stages.get("raw", BatchStageState()).status == "done":
        return 1
    return 0


def _prepare_options(options: Any, out_dir: Path) -> dict[str, Any]:
    value = _portable_options(options if isinstance(options, dict) else {})
    result = value if isinstance(value, dict) else {}
    result["out"] = str(out_dir)
    result.pop("cookies", None)
    return result


def import_handoff_bundle(
    bundle_path: str | Path,
    out_dir: str | Path,
    *,
    project_root: str | Path | None = None,
) -> HandoffImportResult:
    """Safely import a handoff ZIP and remap all batch paths to this computer."""

    source_path = Path(bundle_path).expanduser()
    output_root = Path(out_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"交接包不存在：{source_path}")

    try:
        archive = zipfile.ZipFile(source_path)
    except zipfile.BadZipFile as exc:
        raise HandoffError(f"ZIP 文件损坏：{exc}") from exc
    with archive:
        manifest, by_name = _validate_archive(archive)
        snapshot = _read_batch_snapshot(archive)
        incoming_batch_id = str(snapshot["batch_id"])
        if str(manifest.get("source_batch_id") or "") != incoming_batch_id:
            raise HandoffError("交接包清单与批次快照不一致。")
        try:
            manifest_item_count = int(manifest.get("item_count"))
        except (TypeError, ValueError) as exc:
            raise HandoffError("交接包条目数量格式错误。") from exc
        if manifest_item_count != len(snapshot["items"]):
            raise HandoffError("交接包条目数量与批次快照不一致。")
        incoming_state_path = batch_path(incoming_batch_id, project_root=root)
        try:
            state = load_batch_state(incoming_batch_id, project_root=root) if incoming_state_path.is_file() else None
        except CorruptDataError:
            state = BatchQueueState(
                batch_id=create_batch_id(),
                created_at=now_utc(),
                updated_at=now_utc(),
                options=_prepare_options(snapshot.get("options"), output_root),
                items=[],
            )
        if state is None:
            stamp = str(snapshot.get("created_at") or now_utc())
            state = BatchQueueState(
                batch_id=incoming_batch_id,
                created_at=stamp,
                updated_at=str(snapshot.get("updated_at") or stamp),
                options=_prepare_options(snapshot.get("options"), output_root),
                items=[],
            )

        existing_by_url = {item.url: item for item in state.items}
        restored_count = 0
        backup_count = 0
        for raw_item in snapshot["items"]:
            if not isinstance(raw_item, dict):
                raise HandoffError("批次条目格式错误。")
            url = str(raw_item.get("url") or "").strip()
            if not url:
                raise HandoffError("批次条目缺少 URL。")
            declared_stage = str(raw_item.get("artifact_stage") or "") or None
            archive_folder = str(raw_item.get("archive_folder") or "")
            folder_name = str(raw_item.get("folder_name") or "")
            existing = existing_by_url.get(url)
            existing_folder = _resolve_output_folder(existing.output_folder, root) if existing else None
            folder: Path | None = None
            imported_stage: str | None = None
            if declared_stage in {"raw", "recipe"} and archive_folder and folder_name:
                folder = (
                    existing_folder
                    if existing_folder and _existing_source_url(existing_folder) in {"", url}
                    else _destination_folder(output_root, folder_name, url)
                )
                prefix = archive_folder.rstrip("/") + "/"
                members = [
                    (name, info) for name, info in by_name.items() if name.startswith(prefix) and not info.is_dir()
                ]
                if not members:
                    raise HandoffError(f"批次条目缺少工作文件：{url}")
                local_rank = 2 if is_complete_output(folder) else 1 if is_raw_output(folder) else 0
                incoming_rank = 2 if declared_stage == "recipe" else 1
                if incoming_rank >= local_rank:
                    for name, info in members:
                        relative = PurePosixPath(name).relative_to(PurePosixPath(archive_folder))
                        destination = folder.joinpath(*relative.parts)
                        if destination.exists():
                            backup_count += 1
                        _copy_member(archive, info, destination)
                    _restore_job_paths(folder)
                    restored_count += 1
                imported_stage = _artifact_stage(folder)

            if existing is None:
                existing = BatchQueueItem(url=url)
                state.items.append(existing)
                existing_by_url[url] = existing
                _reset_item_state(existing, imported_stage, folder)
                continue
            existing_artifact_stage = _artifact_stage(existing_folder)
            current_rank = max(
                _stage_rank(existing),
                2 if existing_artifact_stage == "recipe" else 1 if existing_artifact_stage == "raw" else 0,
            )
            imported_rank = 2 if imported_stage == "recipe" else 1 if imported_stage == "raw" else 0
            if imported_rank > 0 and imported_rank >= current_rank:
                _reset_item_state(existing, imported_stage, folder)
            elif existing_artifact_stage:
                _reset_item_state(existing, existing_artifact_stage, existing_folder)
            elif existing.status in {"running", "raw_running", "recipe_running"}:
                _reset_item_state(existing, None, None)

        creator_document_count = 0
        for name, info in by_name.items():
            path = PurePosixPath(name)
            if len(path.parts) != 3 or path.parts[0] != "creators" or path.name not in {
                "creator.json",
                "video_links.txt",
            }:
                continue
            destination = output_root / "creators" / sanitize_filename(path.parts[1]) / path.name
            if destination.exists():
                backup_count += 1
            _copy_member(archive, info, destination)
            creator_document_count += 1

        knowledge_member = by_name.get(".bili-recipe-notes/knowledge_base.json")
        if knowledge_member is not None:
            knowledge_destination = root / ".bili-recipe-notes" / "knowledge_base.json"
            if knowledge_destination.exists():
                backup_count += 1
            _copy_member(archive, knowledge_member, knowledge_destination)
        for name, info in by_name.items():
            prefix = ".bili-recipe-notes/knowledge-imports/"
            if not name.startswith(prefix) or PurePosixPath(name).name in {"", ".", ".."}:
                continue
            destination = root / ".bili-recipe-notes" / "knowledge-imports" / PurePosixPath(name).name
            if destination.exists():
                backup_count += 1
            _copy_member(archive, info, destination)

    state.options = _prepare_options({**state.options, **(snapshot.get("options") or {})}, output_root)
    batch_file = save_batch_state(state, project_root=root)
    raw_count = sum(_stage_rank(item) == 1 for item in state.items)
    recipe_count = sum(_stage_rank(item) == 2 for item in state.items)
    pending_count = sum(_stage_rank(item) == 0 for item in state.items)
    return HandoffImportResult(
        batch_id=state.batch_id,
        batch_path=batch_file,
        item_count=len(state.items),
        restored_count=restored_count,
        raw_count=raw_count,
        recipe_count=recipe_count,
        pending_count=pending_count,
        creator_document_count=creator_document_count,
        backup_count=backup_count,
    )
