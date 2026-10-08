from __future__ import annotations

import sys
from pathlib import Path

from .batch_runner import (
    BackgroundBatchStatus,
    _append_log,
    _now,
    _result_payload,
    _write_status,
    background_result_path,
    batch_log_path,
)
from .pipeline import BatchJobOptions, run_batch
from .storage import atomic_write_json, atomic_write_text, read_json


def clear_resolved_history(batch_id: str, project_root: Path, started_at: str, *, target_stage: str | None = None) -> None:
    """Drop obsolete diagnostics after the whole persisted batch succeeds."""
    from . import batch_runner
    from .batch_queue import batch_path, load_batch_state
    from .history import is_complete_output, is_raw_output
    with batch_runner._batch_control_lock(batch_id, project_root):
        current = batch_runner._get_background_batch_status(batch_id, project_root)
        if not current or current.status != "done" or current.started_at != started_at:
            return
        state = load_batch_state(batch_id, project_root)
        stage = target_stage or state.options.get("target_stage", "recipe")
        validate = is_raw_output if stage == "raw" else is_complete_output
        if not state.items or any(
            item.status not in {"non_recipe", "technique"} and (
                item.stages[stage].status != "done" or not item.output_folder or not validate(item.output_folder)
            ) for item in state.items
        ):
            return
        for path in (batch_path(batch_id, project_root).with_suffix(".json.bak"),
                     batch_log_path(batch_id, project_root),
                     batch_log_path(batch_id, project_root).with_suffix(".log.bak"),
                     background_result_path(batch_id, project_root).with_suffix(".json.bak")):
            # Remove the obsolete record itself, never a symlink target.
            path.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    request_path = Path(sys.argv[1]).expanduser().resolve()
    request = read_json(request_path, expected_type=dict)
    request_path.unlink(missing_ok=True)
    options_data = request.get("options")
    if not isinstance(options_data, dict):
        return 2
    options = BatchJobOptions(**options_data)
    if not options.batch_id:
        return 2
    root = Path(str(request.get("project_root") or Path.cwd())).resolve()
    started_at = str(request.get("started_at") or _now())
    log_path = batch_log_path(options.batch_id, root)
    atomic_write_text(log_path, f"[{_now()}] Background worker started; target={options.target_stage}; pid={os_getpid()}\n", backup=False)
    try:
        result = run_batch(options, log=lambda message: _append_log(log_path, message))
    except BaseException as exc:  # noqa: BLE001 - persist worker death for the UI
        status = BackgroundBatchStatus(
            options.batch_id,
            "failed",
            started_at,
            _now(),
            str(exc),
            log_path,
            os_getpid(),
        )
        _append_log(log_path, f"Background worker failed: {exc}")
        _write_status(status, root)
        return 1

    failed_count = sum(item.status == "failed" for item in result.items)
    atomic_write_json(background_result_path(options.batch_id, root), _result_payload(result), backup=False)
    status = BackgroundBatchStatus(
        options.batch_id,
        "done_with_errors" if failed_count else "done",
        started_at,
        _now(),
        f"{failed_count} item(s) failed" if failed_count else None,
        log_path,
        os_getpid(),
    )
    _append_log(log_path, f"Background worker finished; failed={failed_count}")
    _write_status(status, root)
    if not failed_count:
        try:
            clear_resolved_history(options.batch_id, root, started_at, target_stage=options.target_stage)
        except Exception:
            # Diagnostic housekeeping must not turn a completed recipe into a
            # failed generation. A later consolidation can clear these files.
            import logging
            logging.getLogger(__name__).exception("Could not clear resolved batch history")
    return 1 if failed_count else 0


def os_getpid() -> int:
    import os

    return os.getpid()


if __name__ == "__main__":
    raise SystemExit(main())
