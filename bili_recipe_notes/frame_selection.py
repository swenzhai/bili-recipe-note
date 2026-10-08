"""Launch the optional local frame selector in its own ML environment."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


FRAME_PYTHON_ENV = "BILI_RECIPE_FRAME_PYTHON"
DEFAULT_REQUIREMENT = "找到一张制作完成的菜品特写，主体清晰；优先选择无遮挡、构图完整的画面，手、餐具或字幕仅在遮挡菜品主体时影响评价。"
TOOL_ROOT = Path(__file__).resolve().parent.parent / "external" / "video-frame-choose"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用独立的本地视觉模型环境，从视频中选择菜品画面。",
        epilog=("--device、--threads、--interval、--coarse-limit、--fine-fps、--top-k、"
                "--fine-limit 等参数原样传给 video-frame-choose。默认不允许降级；"
                "仅显式传入 --allow-fallback 时允许工具选择最接近的候选。"
                "输出为独立 PNG 与 JSON，不会应用到菜谱。"),
        allow_abbrev=False,
    )
    parser.add_argument("--input", required=True, type=Path, help="本地视频文件，相对当前工作目录")
    parser.add_argument("--output", required=True, type=Path, help="新的 PNG 文件或结果目录；已有结果不会覆盖")
    parser.add_argument("--requirement", "--prompt", default=DEFAULT_REQUIREMENT, help="选图要求，默认成品菜特写")
    parser.add_argument("--model", type=Path, help="本地模型目录，默认 external/video-frame-choose/models/Qwen2.5-VL-3B-Instruct")
    parser.add_argument("--python", dest="python_executable", help=f"选图工具的 Python；也可设置 {FRAME_PYTHON_ENV}")
    return parser


def _python_executable(override: str | None) -> str:
    configured = override if override is not None else os.environ.get(FRAME_PYTHON_ENV)
    if configured and configured.strip():
        expanded = os.path.expanduser(configured)
        # Treat the override as one executable, never as a shell command.
        found = shutil.which(expanded)
        if found:
            return found
        raise FileNotFoundError(f"找不到可执行的选图 Python：{configured}")
    executable = TOOL_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise FileNotFoundError(
            f"选图独立环境未就绪：{executable}。请按子模块 README 准备环境，"
            f"或用 --python / {FRAME_PYTHON_ENV} 指定已安装选图依赖的 Python。"
        )
    # Do not resolve the venv's Python symlink: its location selects that venv.
    return str(executable)


def main(argv: list[str] | None = None) -> int:
    args, extra = build_parser().parse_known_args(argv)
    script = TOOL_ROOT / "select_frame.py"
    try:
        if not script.is_file():
            raise FileNotFoundError(
                f"缺少选图子模块入口：{script}。请在项目根目录运行 "
                "git submodule update --init external/video-frame-choose。"
            )
        command = [
            _python_executable(args.python_executable), str(script),
            "--input", str(args.input.expanduser()),
            "--output", str(args.output.expanduser()),
            "--requirement", args.requirement,
            "--model", str((args.model or TOOL_ROOT / "models" / "Qwen2.5-VL-3B-Instruct").expanduser()),
            *extra,
        ]
        # Keep cwd and streams: relative user paths and the tool's JSON/progress
        # retain their original meaning, including not_found and error codes.
        return subprocess.run(command, check=False).returncode
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f"选图启动失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
