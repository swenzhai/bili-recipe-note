from __future__ import annotations

import sys
import signal
import threading

from .frame_selection_runner import _finish, _root, _update, run_frame_selection_batch


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        return 2
    root, batch_id, token = args
    cleanup_failed = False
    previous_sigterm = None
    handles_sigterm = threading.current_thread() is threading.main_thread()
    if handles_sigterm:
        def terminate(signum, frame):
            # Unwind storage locks and the model supervisor's finally blocks.
            # Repeated termination requests must not interrupt that cleanup.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            raise SystemExit("后台选图进程收到终止信号")
        previous_sigterm = signal.signal(signal.SIGTERM, terminate)
    try:
        try:
            state = run_frame_selection_batch(root, batch_id, token)
        except BaseException as exc:
            try:
                _update(_root(root), batch_id, lambda current: _finish(current, "interrupted", error=str(exc)), token=token)
            except Exception:
                pass
            return 1
    finally:
        from .frame_selection_remote import close_sessions
        try:
            close_sessions()
        except Exception as exc:
            cleanup_failed = True
            print("远端模型清理失败：" + str(exc), file=sys.stderr)
            try:
                _update(_root(root), batch_id, lambda current: current.update(cleanup_error=str(exc)), token=token)
            except Exception:
                pass
        if handles_sigterm:
            signal.signal(signal.SIGTERM, previous_sigterm)
    return 1 if cleanup_failed or state["status"] in {"completed_with_errors", "failed", "interrupted"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
