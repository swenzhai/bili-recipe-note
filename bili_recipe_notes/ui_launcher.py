from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    from streamlit.web import cli as streamlit_cli

    ui_path = Path(__file__).with_name("ui.py")
    sys.argv = [
        "streamlit",
        "run",
        str(ui_path),
        "--global.developmentMode=false",
        "--server.headless=true",
    ]
    return streamlit_cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
