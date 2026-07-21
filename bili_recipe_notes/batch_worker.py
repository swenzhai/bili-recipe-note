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
from .storage import atomic_write_json, read_json


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
    _append_log(log_path, f"Background worker started; target={options.target_stage}; pid={os_getpid()}")
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
    return 1 if failed_count else 0


def os_getpid() -> int:
    import os

    return os.getpid()


if __name__ == "__main__":
    raise SystemExit(main())
