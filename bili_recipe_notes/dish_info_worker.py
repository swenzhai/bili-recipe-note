from __future__ import annotations

import sys

from .dish_info_runner import _root, _update, _finish_run, run_dish_info_batch


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        return 2
    root, batch_id, token = args
    try:
        state = run_dish_info_batch(root, batch_id, token)
    except BaseException as exc:
        try:
            def failed(current):
                _finish_run(current, "interrupted", error=str(exc))
            _update(_root(root), batch_id, failed, token=token)
        except Exception:
            pass
        return 1
    return 1 if state["status"] == "completed_with_errors" else 0


if __name__ == "__main__":
    raise SystemExit(main())
