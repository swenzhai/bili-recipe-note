from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import CONFIG_DIR_NAME
from .storage import CorruptDataError, atomic_write_json, file_lock, read_json

BATCHES_DIR_NAME = "batches"
BATCH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass
class BatchStageState:
    status: str = "pending"
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


def _default_stages() -> dict[str, BatchStageState]:
    return {"raw": BatchStageState(), "recipe": BatchStageState()}


@dataclass
class BatchQueueItem:
    url: str
    status: str = "pending"
    output_folder: str | None = None
    note_path: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    stages: dict[str, BatchStageState] = field(default_factory=_default_stages)


@dataclass
class BatchQueueState:
    batch_id: str
    created_at: str
    updated_at: str
    options: dict[str, Any]
    items: list[BatchQueueItem]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def batches_dir(project_root: str | Path | None = None) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    return root / CONFIG_DIR_NAME / BATCHES_DIR_NAME


def batch_path(batch_id: str, project_root: str | Path | None = None) -> Path:
    if not BATCH_ID_RE.fullmatch(batch_id):
        raise ValueError(f"Invalid batch id: {batch_id!r}")
    return batches_dir(project_root) / f"{batch_id}.json"


def create_batch_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]


def create_batch_state(
    urls: list[str],
    options: dict[str, Any],
    batch_id: str | None = None,
    project_root: str | Path | None = None,
) -> BatchQueueState:
    seen: set[str] = set()
    items: list[BatchQueueItem] = []
    for url in urls:
        cleaned = url.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        items.append(BatchQueueItem(url=cleaned))
    stamp = now_utc()
    state = BatchQueueState(
        batch_id=batch_id or create_batch_id(),
        created_at=stamp,
        updated_at=stamp,
        options=options,
        items=items,
    )
    save_batch_state(state, project_root=project_root)
    return state


def save_batch_state(state: BatchQueueState, project_root: str | Path | None = None) -> Path:
    state.updated_at = now_utc()
    path = batch_path(state.batch_id, project_root)
    with file_lock(path):
        if path.exists():
            _load_batch_state_from_path(path)
        atomic_write_json(path, asdict(state))
    return path


def _load_batch_state_from_path(path: Path) -> BatchQueueState:
    raw = read_json(path, expected_type=dict)
    try:
        raw_items = raw.get("items") or []
        if not isinstance(raw_items, list) or not all(isinstance(item, dict) for item in raw_items):
            raise TypeError("items must be a list of objects")
        options = raw.get("options") or {}
        if not isinstance(options, dict):
            raise TypeError("options must be an object")
        parsed_items: list[BatchQueueItem] = []
        for item in raw_items:
            item_data = dict(item)
            raw_stages = item_data.pop("stages", None)
            stages = _default_stages()
            if isinstance(raw_stages, dict):
                for name in ("raw", "recipe"):
                    stage_data = raw_stages.get(name)
                    if isinstance(stage_data, dict):
                        stages[name] = BatchStageState(
                            status=str(stage_data.get("status") or "pending"),
                            error=str(stage_data["error"]) if stage_data.get("error") is not None else None,
                            started_at=str(stage_data["started_at"])
                            if stage_data.get("started_at") is not None
                            else None,
                            finished_at=str(stage_data["finished_at"])
                            if stage_data.get("finished_at") is not None
                            else None,
                        )
            else:
                _infer_legacy_stages(item_data, stages)
            parsed_items.append(BatchQueueItem(**item_data, stages=stages))

        state = BatchQueueState(
            batch_id=str(raw["batch_id"]),
            created_at=str(raw["created_at"]),
            updated_at=str(raw["updated_at"]),
            options=options,
            items=parsed_items,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CorruptDataError(f"Invalid batch state in {path}: {exc}") from exc
    if path.stem != state.batch_id:
        raise CorruptDataError(
            f"Batch id mismatch in {path}: document contains {state.batch_id!r}."
        )
    return state


def _infer_legacy_stages(item: dict[str, Any], stages: dict[str, BatchStageState]) -> None:
    """Infer stage state for batch files written before stage tracking existed."""
    status = str(item.get("status") or "pending")
    folder_value = item.get("output_folder")
    folder = Path(str(folder_value)) if folder_value else None
    has_transcript = bool(folder and (folder / "transcript.json").is_file())
    has_recipe = bool(folder and (folder / "recipe.json").is_file() and (folder / "note.md").is_file())

    if status in {"done", "skipped"} or has_recipe:
        stages["raw"].status = "done"
        stages["recipe"].status = "done"
    elif status == "raw_ready" or has_transcript:
        stages["raw"].status = "done"
        stages["recipe"].status = "failed" if status == "failed" else "pending"
        stages["recipe"].error = str(item.get("error")) if status == "failed" and item.get("error") else None
    elif status == "failed":
        stages["raw"].status = "failed"
        stages["raw"].error = str(item.get("error")) if item.get("error") else None


def load_batch_state(batch_id: str, project_root: str | Path | None = None) -> BatchQueueState:
    return _load_batch_state_from_path(batch_path(batch_id, project_root))


def list_batch_states(project_root: str | Path | None = None) -> list[BatchQueueState]:
    root = batches_dir(project_root)
    if not root.exists():
        return []
    paths = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [_load_batch_state_from_path(path) for path in paths]


def selectable_items(
    state: BatchQueueState,
    resume_mode: str,
    target_stage: str = "recipe",
) -> list[BatchQueueItem]:
    if resume_mode == "retry-failed":
        return [
            item
            for item in state.items
            if item.status == "failed"
            or any(
                item.stages[name].status == "failed"
                for name in (("raw",) if target_stage == "raw" else ("raw", "recipe"))
            )
        ]
    if resume_mode == "resume-unfinished":
        required = ("raw",) if target_stage == "raw" else ("raw", "recipe")
        return [
            item
            for item in state.items
            if not (
                item.status in {"done", "skipped"}
                and all(item.stages[name].status == "pending" for name in ("raw", "recipe"))
            )
            and any(item.stages[name].status != "done" for name in required)
        ]
    return state.items
