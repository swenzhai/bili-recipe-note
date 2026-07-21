from __future__ import annotations

import os
import sys
from pathlib import Path


def resolve_ui_path() -> Path:
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        ui_path = bundle_root / "bili_recipe_notes" / "ui.py"
    else:
        ui_path = Path(__file__).with_name("ui.py")
    if not ui_path.is_file():
        raise FileNotFoundError(f"Bundled Streamlit UI not found: {ui_path}")
    return ui_path


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--batch-worker-request":
        from .batch_worker import main as batch_worker_main

        sys.argv = [sys.argv[0], sys.argv[2]]
        return batch_worker_main()

    os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")
    from streamlit.web import cli as streamlit_cli

    sys.argv = [
        "streamlit",
        "run",
        str(resolve_ui_path()),
        "--global.developmentMode=false",
        "--server.headless=true",
        "--server.address=127.0.0.1",
        "--browser.serverAddress=127.0.0.1",
    ]
    return streamlit_cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
