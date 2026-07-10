from __future__ import annotations

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
