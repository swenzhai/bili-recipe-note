"""Stop this checkout's Linux web servers without importing the application."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import signal
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def server_label(argv: tuple[str, ...] | list[str], cwd: Path, root: Path) -> str | None:
    if cwd != root or not argv:
        return None
    executable = Path(argv[0]).name
    arguments = list(argv[1:])
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        while arguments and arguments[0] in {"-u", "-B", "-E", "-s", "-S", "-I", "-O", "-OO"}:
            arguments.pop(0)
        if len(arguments) >= 2 and arguments[0] == "-m":
            entrypoint, arguments = arguments[1], arguments[2:]
        elif arguments and Path(arguments[0]).name in {"streamlit", "uvicorn"}:
            entrypoint, arguments = Path(arguments[0]).name, arguments[1:]
        else:
            return None
    else:
        entrypoint = executable
    if entrypoint == "bili_recipe_notes.ui_launcher":
        # This launcher also dispatches detached workers. Only web arguments
        # are eligible; never terminate a worker just because it uses it.
        options = iter(arguments)
        for option in options:
            if option in {"--host", "--port"}:
                if next(options, None) is None:
                    return None
            elif option not in {"--no-browser", "--legacy-ui"} and not option.startswith(("--host=", "--port=")):
                return None
        return "旧版管理页" if "--legacy-ui" in arguments else "管理与点餐服务"
    if entrypoint == "streamlit" and len(arguments) >= 2 and arguments[0] == "run":
        if (cwd / arguments[1]).resolve() == root / "bili_recipe_notes/ui.py":
            return "旧版管理页"
    if entrypoint == "uvicorn" and arguments and arguments[0] == "bili_recipe_notes.mobile_api:app":
        return "管理与点餐服务"
    return None


@dataclass(frozen=True)
class Process:
    pid: int
    started: str
    cwd: Path
    argv: tuple[str, ...]


def read_process(pid: int) -> Process | None:
    path = Path("/proc") / str(pid)
    try:
        if path.stat().st_uid != os.getuid():
            return None
        before = (path / "stat").read_text().rsplit(") ", 1)[1].split()
        if before[0] in {"Z", "X"}:
            return None
        cwd = (path / "cwd").resolve(strict=True)
        argv = tuple(os.fsdecode(part) for part in (path / "cmdline").read_bytes().split(b"\0") if part)
        after = (path / "stat").read_text().rsplit(") ", 1)[1].split()
        if before[19] != after[19] or after[0] in {"Z", "X"}:
            return None
        return Process(pid, before[19], cwd, argv)
    except (OSError, ValueError, IndexError):
        return None


def find_servers(root: Path) -> list[Process]:
    result = []
    for path in Path("/proc").iterdir():
        if path.name.isdecimal():
            process = read_process(int(path.name))
            if process and server_label(process.argv, process.cwd, root):
                result.append(process)
    return sorted(result, key=lambda process: process.pid)


def send_signal(process: Process, signum: int) -> None:
    descriptor = None
    try:
        # pidfd pins the identity even if a process exits and its PID is reused.
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            try:
                descriptor = os.pidfd_open(process.pid)
            except OSError:
                pass
        if read_process(process.pid) != process:
            return
        if descriptor is not None:
            signal.pidfd_send_signal(descriptor, signum)
        else:
            os.kill(process.pid, signum)
    except ProcessLookupError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def wait_for_exit(processes: list[Process], timeout: float) -> list[Process]:
    deadline = time.monotonic() + timeout
    while True:
        remaining = [process for process in processes if read_process(process.pid) == process]
        delay = deadline - time.monotonic()
        if not remaining or delay <= 0:
            return remaining
        time.sleep(min(0.1, delay))


def timeout_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("等待秒数必须是非负有限数值")
    return seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="关闭本工程、当前用户的新版和旧版网页服务（Linux）")
    parser.add_argument("--dry-run", action="store_true", help="只列出将关闭的服务，不发送信号")
    parser.add_argument("--timeout", type=timeout_seconds, default=15.0, help="正常退出的等待秒数，默认 15")
    parser.add_argument("--force", action="store_true", help="等待超时后强制结束仍未退出的服务")
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux") or not Path("/proc/self/stat").is_file():
        parser.error("需要 Linux /proc 文件系统")
    root = PROJECT_ROOT.resolve()
    processes = find_servers(root)
    if not processes:
        print("本工程没有正在运行的网页服务。")
        return 0
    print(f"项目目录：{root}", flush=True)
    for process in processes:
        print(f"{'将关闭' if args.dry_run else '正在关闭'} {server_label(process.argv, process.cwd, root)}（PID {process.pid}）", flush=True)
    print("独立生成、审核、介绍和选图任务会继续运行；需要暂停时请先在任务中心操作。", flush=True)
    if args.dry_run:
        return 0
    try:
        for process in processes:
            send_signal(process, signal.SIGTERM)
        remaining = wait_for_exit(processes, args.timeout)
        if remaining and args.force:
            for process in remaining:
                print(f"强制结束 PID {process.pid}", flush=True)
                send_signal(process, signal.SIGKILL)
            wait_for_exit(remaining, 3.0)
    except OSError as exc:
        print(f"停止失败：{exc}", file=sys.stderr)
        return 1
    # Catch a service manager restarting the server during shutdown, too.
    remaining = find_servers(root)
    if remaining:
        print("仍有网页服务未退出：" + ", ".join(str(process.pid) for process in remaining), file=sys.stderr)
        print("可稍后重试或加 --force；若由 systemd 等托管，请停止对应服务。", file=sys.stderr)
        return 1
    print("网页服务已全部关闭。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
