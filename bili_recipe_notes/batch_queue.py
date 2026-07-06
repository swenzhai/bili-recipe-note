from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import CONFIG_DIR_NAME

BATCHES_DIR_NAME = "batches"


@dataclass
class BatchQueueItem:
    url: str
    status: str = "pending"
    output_folder: str | None = None
    note_path: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_batch_state(batch_id: str, project_root: str | Path | None = None) -> BatchQueueState:
    raw = json.loads(batch_path(batch_id, project_root).read_text(encoding="utf-8"))
    return BatchQueueState(
        batch_id=raw["batch_id"],
        created_at=raw["created_at"],
        updated_at=raw["updated_at"],
        options=raw.get("options") or {},
        items=[BatchQueueItem(**item) for item in raw.get("items") or []],
    )


def list_batch_states(project_root: str | Path | None = None) -> list[BatchQueueState]:
    root = batches_dir(project_root)
    if not root.exists():
        return []
    states: list[BatchQueueState] = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            states.append(load_batch_state(path.stem, project_root=project_root))
        except Exception:
            continue
    return states


def selectable_items(state: BatchQueueState, resume_mode: str) -> list[BatchQueueItem]:
    if resume_mode == "retry-failed":
        return [item for item in state.items if item.status == "failed"]
    if resume_mode == "resume-unfinished":
        return [item for item in state.items if item.status in {"pending", "failed", "running"}]
    return state.items
