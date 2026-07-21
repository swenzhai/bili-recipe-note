from __future__ import annotations

import copy
import os
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .batch_queue import batches_dir
from .pipeline import BatchJobItemResult, BatchJobOptions, BatchJobResult, run_batch
from .storage import atomic_write_json, read_json


@dataclass(frozen=True)
class BackgroundBatchStatus:
    batch_id: str
    status: str
    started_at: str
    finished_at: str | None = None
    error: str | None = None
    log_path: Path | None = None
    pid: int | None = None


@dataclass
class _RunRecord:
    worker: threading.Thread | subprocess.Popen
    status: BackgroundBatchStatus


_RUNS: dict[str, _RunRecord] = {}
_RUNS_LOCK = threading.Lock()
_DEFAULT_RUN_BATCH = run_batch


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def batch_log_path(batch_id: str, project_root: str | Path | None = None) -> Path:
    return batches_dir(project_root) / f"{batch_id}.log"


def background_runtime_dir(project_root: str | Path | None = None) -> Path:
    return batches_dir(project_root) / "runtime"


def background_status_path(batch_id: str, project_root: str | Path | None = None) -> Path:
    return background_runtime_dir(project_root) / f"{batch_id}.status.json"


def background_request_path(batch_id: str, project_root: str | Path | None = None) -> Path:
    return background_runtime_dir(project_root) / f"{batch_id}.request.json"


def background_result_path(batch_id: str, project_root: str | Path | None = None) -> Path:
    return background_runtime_dir(project_root) / f"{batch_id}.result.json"


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(f"[{_now()}] {message.rstrip()}\n")


def _write_status(status: BackgroundBatchStatus, project_root: str | Path | None = None) -> None:
    payload = asdict(status)
    payload["log_path"] = str(status.log_path) if status.log_path else None
    atomic_write_json(background_status_path(status.batch_id, project_root), payload, backup=False)


def _read_status(batch_id: str, project_root: str | Path | None = None) -> BackgroundBatchStatus | None:
    path = background_status_path(batch_id, project_root)
    if not path.is_file():
        return None
    try:
        value = read_json(path, expected_type=dict)
        return BackgroundBatchStatus(
            batch_id=str(value["batch_id"]),
            status=str(value["status"]),
            started_at=str(value["started_at"]),
            finished_at=str(value["finished_at"]) if value.get("finished_at") else None,
            error=str(value["error"]) if value.get("error") else None,
            log_path=Path(str(value["log_path"])) if value.get("log_path") else None,
            pid=int(value["pid"]) if value.get("pid") else None,
        )
    except Exception:
        return None


def _pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _worker_is_alive(worker: threading.Thread | subprocess.Popen) -> bool:
    return worker.is_alive() if isinstance(worker, threading.Thread) else worker.poll() is None


def _result_payload(result: BatchJobResult) -> dict:
    return {
        "items": [
            {
                "url": item.url,
                "status": item.status,
                "output_folder": str(item.output_folder) if item.output_folder else None,
                "note_path": str(item.note_path) if item.note_path else None,
                "error": item.error,
            }
            for item in result.items
        ]
    }


def _load_result(batch_id: str, project_root: str | Path | None = None) -> BatchJobResult:
    value = read_json(background_result_path(batch_id, project_root), expected_type=dict)
    items = []
    for raw in value.get("items") or []:
        if not isinstance(raw, dict):
            continue
        items.append(
            BatchJobItemResult(
                url=str(raw.get("url") or ""),
                status=str(raw.get("status") or "failed"),
                output_folder=Path(str(raw["output_folder"])) if raw.get("output_folder") else None,
                note_path=Path(str(raw["note_path"])) if raw.get("note_path") else None,
                error=str(raw["error"]) if raw.get("error") else None,
            )
        )
    return BatchJobResult(items)


def _start_thread_batch(
    options: BatchJobOptions,
    *,
    project_root: str | Path | None,
    on_complete: Callable[[BatchJobResult], None] | None,
) -> BackgroundBatchStatus:
    """Compatibility path for an explicitly replaced/injected run_batch callable."""

    batch_id = str(options.batch_id)
    log_path = batch_log_path(batch_id, project_root)
    copied_options = copy.deepcopy(options)
    started_at = _now()
    initial = BackgroundBatchStatus(batch_id, "running", started_at, log_path=log_path, pid=os.getpid())

    def worker() -> None:
        _append_log(log_path, f"Background batch started; target={copied_options.target_stage}")
        try:
            result = run_batch(copied_options, log=lambda message: _append_log(log_path, message))
        except Exception as exc:  # noqa: BLE001
            final = BackgroundBatchStatus(
                batch_id, "failed", started_at, _now(), str(exc), log_path, os.getpid()
            )
            _append_log(log_path, f"Background batch failed: {exc}")
        else:
            failed_count = sum(item.status == "failed" for item in result.items)
            atomic_write_json(background_result_path(batch_id, project_root), _result_payload(result), backup=False)
            if on_complete:
                try:
                    on_complete(result)
                except Exception as exc:  # noqa: BLE001
                    _append_log(log_path, f"Post-processing failed: {exc}")
            final = BackgroundBatchStatus(
                batch_id,
                "done_with_errors" if failed_count else "done",
                started_at,
                _now(),
                f"{failed_count} item(s) failed" if failed_count else None,
                log_path,
                os.getpid(),
            )
            _append_log(log_path, f"Background batch finished; failed={failed_count}")
        _write_status(final, project_root)
        with _RUNS_LOCK:
            record = _RUNS.get(batch_id)
            if record:
                record.status = final

    thread = threading.Thread(target=worker, name=f"batch-{batch_id}", daemon=True)
    _RUNS[batch_id] = _RunRecord(thread, initial)
    _write_status(initial, project_root)
    thread.start()
    return initial


def start_background_batch(
    options: BatchJobOptions,
    *,
    project_root: str | Path | None = None,
    on_complete: Callable[[BatchJobResult], None] | None = None,
) -> BackgroundBatchStatus:
    """Start a persistent batch in a separate Python process.

    Whisper/CTranslate2 native workers are deliberately kept outside the
    Streamlit process so a heavy transcription cannot take down the web UI.
    """

    if not options.batch_id:
        raise ValueError("background batches require a persisted batch_id")
    batch_id = options.batch_id
    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd().resolve()
    log_path = batch_log_path(batch_id, root)

    with _RUNS_LOCK:
        existing = _RUNS.get(batch_id)
        if existing and _worker_is_alive(existing.worker):
            return existing.status
        persisted = _read_status(batch_id, root)
        if persisted and persisted.status == "running" and _pid_is_alive(persisted.pid):
            return persisted

        # Tests and callers can still inject a lightweight runner without
        # creating a child interpreter. Production always takes the process path.
        if run_batch is not _DEFAULT_RUN_BATCH:
            return _start_thread_batch(options, project_root=root, on_complete=on_complete)

        request_path = background_request_path(batch_id, root)
        request_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            request_path,
            {"options": asdict(options), "started_at": _now(), "project_root": str(root)},
            backup=False,
        )
        try:
            os.chmod(request_path, 0o600)
        except OSError:
            pass
        started_at = _now()
        initial = BackgroundBatchStatus(batch_id, "running", started_at, log_path=log_path)
        _write_status(initial, root)
        background_result_path(batch_id, root).unlink(missing_ok=True)

        popen_kwargs: dict = {
            "cwd": str(root),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        environment = os.environ.copy()
        package_root = str(Path(__file__).resolve().parents[1])
        existing_python_path = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            package_root + os.pathsep + existing_python_path if existing_python_path else package_root
        )
        popen_kwargs["env"] = environment
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        command = (
            [sys.executable, "--batch-worker-request", str(request_path)]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "bili_recipe_notes.batch_worker", str(request_path)]
        )
        process = subprocess.Popen(command, **popen_kwargs)
        initial = BackgroundBatchStatus(batch_id, "running", started_at, log_path=log_path, pid=process.pid)
        current = _read_status(batch_id, root)
        if not current or current.status == "running":
            _write_status(initial, root)
        _RUNS[batch_id] = _RunRecord(process, initial)

        if on_complete:
            def watch() -> None:
                process.wait()
                status = _read_status(batch_id, root)
                if status and status.status in {"done", "done_with_errors"}:
                    try:
                        on_complete(_load_result(batch_id, root))
                    except Exception as exc:  # noqa: BLE001
                        _append_log(log_path, f"Post-processing failed: {exc}")

            threading.Thread(target=watch, name=f"batch-watch-{batch_id}", daemon=True).start()
        return initial


def get_background_batch_status(
    batch_id: str,
    project_root: str | Path | None = None,
) -> BackgroundBatchStatus | None:
    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd().resolve()
    status = _read_status(batch_id, root)
    if status and status.status == "running" and status.pid and not _pid_is_alive(status.pid):
        status = BackgroundBatchStatus(
            batch_id=status.batch_id,
            status="failed",
            started_at=status.started_at,
            finished_at=_now(),
            error="Batch worker exited unexpectedly. See the batch log for details.",
            log_path=status.log_path,
            pid=status.pid,
        )
        _write_status(status, root)
    return status


def read_batch_log(
    batch_id: str,
    *,
    max_chars: int = 12000,
    project_root: str | Path | None = None,
) -> str:
    path = batch_log_path(batch_id, project_root)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]
