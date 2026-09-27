"""Internal subprocess entry point for persistent AI-review jobs."""

from __future__ import annotations

import sys

from .ai_review_runner import _root, _update, run_ai_review_batch


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        return 2
    out_dir, batch_id, run_token = args
    try:
        state = run_ai_review_batch(out_dir, batch_id, run_token)
    except BaseException as exc:
        try:
            def failed(current: dict) -> None:
                if current.get("run_token") == run_token:
                    current.update(status="interrupted", current_folder=None, error=str(exc))
            _update(_root(out_dir), batch_id, failed)
        except Exception:
            pass
        print(f"AI review worker interrupted: {exc}", file=sys.stderr, flush=True)
        return 1
    return 1 if state["status"] == "completed_with_errors" else 0


if __name__ == "__main__":
    raise SystemExit(main())
