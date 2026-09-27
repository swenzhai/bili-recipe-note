from __future__ import annotations

import copy
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

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
    paused_at: str | None = None
    paused_seconds: float = 0.0
    process_start_id: str | None = None


@dataclass
class _RunRecord:
    worker: threading.Thread | subprocess.Popen
    status: BackgroundBatchStatus


_RUNS: dict[str, _RunRecord] = {}
_RUNS_LOCK = threading.Lock()
_DEFAULT_RUN_BATCH = run_batch
_ACTIVE_STATUSES = {"running", "paused"}


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
    # The worker builds its final status independently of the UI controls.
    if status.status not in _ACTIVE_STATUSES:
        previous = _read_status(status.batch_id, project_root)
        if previous and previous.pid == status.pid:
            status = replace(
                status,
                paused_seconds=previous.paused_seconds,
                process_start_id=previous.process_start_id,
            )
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
            paused_at=str(value["paused_at"]) if value.get("paused_at") else None,
            paused_seconds=float(value.get("paused_seconds") or 0.0),
            process_start_id=str(value["process_start_id"]) if value.get("process_start_id") else None,
        )
    except Exception:
        return None


def _process_state(pid: int) -> str | None:
    if sys.platform.startswith("linux"):
        try:
            # comm may contain spaces or parentheses; fields follow its final ')'.
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            return None
    if sys.platform == "darwin":
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return result.stdout.strip()[:1] or None
    return None


def _pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        return _process_state(pid) not in {None, "Z", "X"}
    return True


def _process_start_id(pid: int) -> str | None:
    """Identify a process across UI restarts without trusting a recycled PID."""

    if sys.platform.startswith("linux"):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return f"{boot_id}:{fields[19]}"
        except (OSError, IndexError):
            return None
    if sys.platform == "darwin":
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return result.stdout.strip() or None
    return None


def _legacy_worker_matches(status: BackgroundBatchStatus, root: Path) -> bool:
    """Allow pre-pause-feature workers only when their command identifies this batch."""

    request = str(background_request_path(status.batch_id, root))
    if sys.platform.startswith("linux"):
        try:
            command = Path(f"/proc/{status.pid}/cmdline").read_bytes().decode().split("\0")
        except (OSError, UnicodeError):
            return False
        return command[1:] in (
            ["-m", "bili_recipe_notes.batch_worker", request, ""],
            ["--batch-worker-request", request, ""],
        )
    if sys.platform == "darwin":
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(status.pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        command = result.stdout.strip()
        return command.endswith((f" -m bili_recipe_notes.batch_worker {request}", f" --batch-worker-request {request}"))
    return False


@contextmanager
def _batch_control_lock(batch_id: str, root: Path) -> Iterator[None]:
    """Serialize UI controls across threads and separate UI processes."""

    with _RUNS_LOCK:
        if os.name == "nt":
            yield
            return
        import fcntl

        path = background_runtime_dir(root) / f"{batch_id}.control.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)


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

    with _batch_control_lock(batch_id, root):
        persisted = _get_background_batch_status(batch_id, root)
        existing = _RUNS.get(batch_id)
        if existing and _worker_is_alive(existing.worker):
            return persisted or existing.status
        if persisted and persisted.status in _ACTIVE_STATUSES and _pid_is_alive(persisted.pid):
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
        initial = BackgroundBatchStatus(
            batch_id, "running", started_at, log_path=log_path,
            pid=process.pid, process_start_id=_process_start_id(process.pid),
        )
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
    with _batch_control_lock(batch_id, root):
        return _get_background_batch_status(batch_id, root)


def _get_background_batch_status(batch_id: str, root: Path) -> BackgroundBatchStatus | None:
    status = _read_status(batch_id, root)
    identifiable_platform = sys.platform.startswith("linux") or sys.platform == "darwin"
    if status and status.status in _ACTIVE_STATUSES and status.pid and (
        not _pid_is_alive(status.pid)
        or (status.process_start_id and status.process_start_id != _process_start_id(status.pid))
        or (
            identifiable_platform and status.pid != os.getpid() and not status.process_start_id
            and not _legacy_worker_matches(status, root)
        )
    ):
        status = replace(
            status,
            status="failed",
            finished_at=_now(),
            error="Batch worker exited unexpectedly. See the batch log for details.",
            paused_at=None,
        )
        _write_status(status, root)
    elif (
        status and status.status == "running" and status.pid and status.pid != os.getpid()
        and identifiable_platform and _process_state(status.pid) in {"T", "t"}
    ):
        # Recover if the UI died after SIGSTOP, or after persisting running
        # but before SIGCONT. The process itself is authoritative here.
        status = replace(status, status="paused", paused_at=_now())
        _write_status(status, root)
        _remember_status(status)
    return status


def _controllable_process_group(status: BackgroundBatchStatus, root: Path) -> int:
    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        raise RuntimeError("Pausing background batches is supported only on Linux and macOS.")
    pid = status.pid
    if not pid or pid == os.getpid():
        raise RuntimeError("This batch does not have a separate worker process and cannot be paused.")
    try:
        group = os.getpgid(pid)
        session = os.getsid(pid)
    except ProcessLookupError as exc:
        raise RuntimeError("The batch worker has already exited.") from exc
    if group != pid or session != pid or group == os.getpgrp():
        raise RuntimeError("The batch worker does not have an isolated process group; pause is unavailable.")
    if status.process_start_id:
        matches = status.process_start_id == _process_start_id(pid)
    else:
        matches = _legacy_worker_matches(status, root)
    if not matches:
        raise RuntimeError("The batch worker identity could not be verified; pause is unavailable.")
    return group


def _remember_status(status: BackgroundBatchStatus) -> None:
    record = _RUNS.get(status.batch_id)
    if record and record.status.pid == status.pid:
        record.status = status


def pause_background_batch(
    batch_id: str,
    project_root: str | Path | None = None,
) -> BackgroundBatchStatus:
    """Immediately suspend an isolated worker and its subprocesses on POSIX.

    The paused process remains in memory, preserving its current item. The
    persisted status allows a restarted UI to continue the same process.
    """

    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd().resolve()
    with _batch_control_lock(batch_id, root):
        status = _get_background_batch_status(batch_id, root)
        if status is None:
            raise ValueError(f"No background batch found: {batch_id}")
        if status.status == "paused":
            return status
        if status.status != "running":
            raise RuntimeError("Only a running background batch can be paused.")
        group = _controllable_process_group(status, root)
        os.killpg(group, signal.SIGSTOP)
        try:
            # Delivery is asynchronous; wait until the worker cannot overwrite
            # our status or finish between the terminal-state check and write.
            deadline = time.monotonic() + 2
            while _process_state(group) not in {"T", "t"}:
                if not _pid_is_alive(group):
                    # Descendants may have received SIGSTOP before their
                    # leader exited; do not leave them stranded.
                    try:
                        os.killpg(group, signal.SIGCONT)
                    except ProcessLookupError:
                        pass
                    return _get_background_batch_status(batch_id, root) or status
                if time.monotonic() >= deadline:
                    raise RuntimeError("The batch worker did not stop in time.")
                time.sleep(0.01)
            current = _read_status(batch_id, root)
            if current and current.status not in _ACTIVE_STATUSES:
                os.killpg(group, signal.SIGCONT)
                _remember_status(current)
                return current
            paused = replace(
                status, status="paused", paused_at=_now(), process_start_id=_process_start_id(group)
            )
            _write_status(paused, root)
        except BaseException:
            # A failed status write must not leave an invisible stopped worker.
            try:
                os.killpg(group, signal.SIGCONT)
            except ProcessLookupError:
                pass
            raise
        _remember_status(paused)
        return paused


def resume_background_batch(
    batch_id: str,
    project_root: str | Path | None = None,
) -> BackgroundBatchStatus:
    """Continue the same paused worker without retrying or restarting its item."""

    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd().resolve()
    with _batch_control_lock(batch_id, root):
        status = _get_background_batch_status(batch_id, root)
        if status is None:
            raise ValueError(f"No background batch found: {batch_id}")
        if status.status == "running":
            return status
        if status.status != "paused":
            raise RuntimeError("Only a paused background batch can be continued.")
        group = _controllable_process_group(status, root)
        paused_seconds = status.paused_seconds
        if status.paused_at:
            paused_seconds += max(
                0.0,
                (datetime.now(timezone.utc) - datetime.fromisoformat(status.paused_at)).total_seconds(),
            )
        running = replace(status, status="running", paused_at=None, paused_seconds=paused_seconds)
        # Persist while stopped, so a worker that finishes immediately on
        # SIGCONT retains its own terminal status rather than being overwritten.
        _write_status(running, root)
        try:
            os.killpg(group, signal.SIGCONT)
        except BaseException:
            _write_status(status, root)
            raise
        _remember_status(running)
        return running


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
