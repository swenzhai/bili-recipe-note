from __future__ import annotations

import sys

from .frame_selection_runner import _finish, _root, _update, run_frame_selection_batch


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        return 2
    root, batch_id, token = args
    try:
        state = run_frame_selection_batch(root, batch_id, token)
    except BaseException as exc:
        try:
            _update(_root(root), batch_id, lambda current: _finish(current, "interrupted", error=str(exc)), token=token)
        except Exception:
            pass
        return 1
    return 1 if state["status"] in {"completed_with_errors", "failed", "interrupted"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
