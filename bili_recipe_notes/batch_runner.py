from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .batch_queue import batches_dir
from .pipeline import BatchJobOptions, BatchJobResult, run_batch


@dataclass(frozen=True)
class BackgroundBatchStatus:
    batch_id: str
    status: str
    started_at: str
    finished_at: str | None = None
    error: str | None = None
    log_path: Path | None = None


@dataclass
class _RunRecord:
    thread: threading.Thread
    status: BackgroundBatchStatus


_RUNS: dict[str, _RunRecord] = {}
_RUNS_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def batch_log_path(batch_id: str, project_root: str | Path | None = None) -> Path:
    return batches_dir(project_root) / f"{batch_id}.log"


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(f"[{_now()}] {message.rstrip()}\n")


def start_background_batch(
    options: BatchJobOptions,
    *,
    project_root: str | Path | None = None,
    on_complete: Callable[[BatchJobResult], None] | None = None,
) -> BackgroundBatchStatus:
    """Start a persistent batch without blocking the Streamlit request thread."""
    if not options.batch_id:
        raise ValueError("background batches require a persisted batch_id")
    batch_id = options.batch_id
    log_path = batch_log_path(batch_id, project_root)
    copied_options = copy.deepcopy(options)
    started_at = _now()

    with _RUNS_LOCK:
        existing = _RUNS.get(batch_id)
        if existing and existing.thread.is_alive():
            return existing.status

        initial_status = BackgroundBatchStatus(
            batch_id=batch_id,
            status="running",
            started_at=started_at,
            log_path=log_path,
        )

        def worker() -> None:
            _append_log(log_path, f"Background batch started; target={copied_options.target_stage}")
            try:
                result = run_batch(copied_options, log=lambda message: _append_log(log_path, message))
            except Exception as exc:  # noqa: BLE001 - persisted state and log must survive worker failure
                final = BackgroundBatchStatus(
                    batch_id=batch_id,
                    status="failed",
                    started_at=started_at,
                    finished_at=_now(),
                    error=str(exc),
                    log_path=log_path,
                )
                _append_log(log_path, f"Background batch failed: {exc}")
            else:
                failed_count = sum(item.status == "failed" for item in result.items)
                if on_complete:
                    try:
                        on_complete(result)
                    except Exception as exc:  # noqa: BLE001
                        _append_log(log_path, f"Post-processing failed: {exc}")
                final = BackgroundBatchStatus(
                    batch_id=batch_id,
                    status="done_with_errors" if failed_count else "done",
                    started_at=started_at,
                    finished_at=_now(),
                    error=f"{failed_count} item(s) failed" if failed_count else None,
                    log_path=log_path,
                )
                _append_log(log_path, f"Background batch finished; failed={failed_count}")
            with _RUNS_LOCK:
                record = _RUNS.get(batch_id)
                if record:
                    record.status = final

        thread = threading.Thread(target=worker, name=f"batch-{batch_id}", daemon=True)
        _RUNS[batch_id] = _RunRecord(thread=thread, status=initial_status)
        thread.start()
        return initial_status


def get_background_batch_status(batch_id: str) -> BackgroundBatchStatus | None:
    with _RUNS_LOCK:
        record = _RUNS.get(batch_id)
        return record.status if record else None


def read_batch_log(batch_id: str, *, max_chars: int = 12000) -> str:
    path = batch_log_path(batch_id)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]
