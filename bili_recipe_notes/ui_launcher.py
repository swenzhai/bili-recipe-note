from __future__ import annotations

import os
import sys
import argparse
import threading
import webbrowser
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
    if len(sys.argv) == 5 and sys.argv[1] == "--admin-creator-worker":
        from .admin_creator_worker import main as creator_worker_main
        return creator_worker_main(sys.argv[2:])
    if len(sys.argv) == 4 and sys.argv[1] == "--frame-selection-supervisor":
        from bili_recipe_notes.frame_selection_service import supervisor_main

        return supervisor_main(sys.argv[2:])

    if len(sys.argv) == 5 and sys.argv[1] == "--frame-selection-worker":
        from bili_recipe_notes.frame_selection_worker import main as frame_selection_worker_main

        return frame_selection_worker_main(sys.argv[2:])

    if len(sys.argv) == 5 and sys.argv[1] == "--dish-info-worker":
        from bili_recipe_notes.dish_info_worker import main as dish_info_worker_main

        return dish_info_worker_main(sys.argv[2:])

    if len(sys.argv) == 5 and sys.argv[1] == "--ai-review-worker":
        from bili_recipe_notes.ai_review_worker import main as ai_review_worker_main

        return ai_review_worker_main(sys.argv[2:])

    if len(sys.argv) == 3 and sys.argv[1] == "--batch-worker-request":
        from .batch_worker import main as batch_worker_main

        sys.argv = [sys.argv[0], sys.argv[2]]
        return batch_worker_main()

    parser = argparse.ArgumentParser(description="Chef Zhai React 管理页与点餐服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--legacy-ui", action="store_true", help="启动原 Streamlit 管理页（8501）")
    args = parser.parse_args(sys.argv[1:])
    if args.legacy_ui:
        os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")
        from streamlit.web import cli as streamlit_cli
        sys.argv = ["streamlit", "run", str(resolve_ui_path()), "--global.developmentMode=false",
                    "--server.headless=true", f"--server.address={args.host}",
                    "--browser.serverAddress=127.0.0.1"]
        return streamlit_cli.main()

    import uvicorn
    from bili_recipe_notes.mobile_api import create_app

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    static = bundle_root / "web" / "dist"
    if not (static / "index.html").is_file():
        parser.error("缺少网页构建产物，请先在 web/ 运行 corepack pnpm build")
    app = create_app(static_dir=static)
    if not args.no_browser:
        timer = threading.Timer(1, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/admin/"))
        timer.daemon = True
        timer.start()
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
