"""Persistent creator-link discovery, outside the HTTP server's lifetime."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import os
import re
import subprocess
import sys
import uuid

from .ai_review_runner import _identity
from .batch_runner import _pid_is_alive
from .config import load_config
from .data_paths import DATA_DIR_ENV, data_root, state_dir
from .pipeline import crawl_and_archive_creator
from .storage import atomic_write_json, file_lock, read_json

_PROCESSES: dict[str, subprocess.Popen] = {}


def _now():
    return datetime.now(timezone.utc).isoformat()


def creator_task_path(project_root, task_id):
    if not re.fullmatch(r"creator-[a-f0-9]{16}", task_id):
        raise ValueError("UP 主任务标识无效")
    root = state_dir(project_root) / "creator-tasks"
    path = root / (task_id + ".json")
    if root.is_symlink() or path.is_symlink():
        raise ValueError("UP 主任务目录不能使用符号链接")
    return path


def _active(state):
    pid = state.get("pid")
    return (state.get("status") in {"running", "queued"} and type(pid) is int and pid > 0
            and (sys.platform == "win32" or _pid_is_alive(pid))
            and bool(state.get("process_start_id")) and _identity(pid) == state["process_start_id"])


def list_creator_tasks(project_root):
    for key, process in list(_PROCESSES.items()):
        if process.poll() is not None:
            _PROCESSES.pop(key, None)
    result = []
    root = state_dir(project_root) / "creator-tasks"
    for path in root.glob("creator-*.json"):
        path = creator_task_path(project_root, path.stem)
        state = read_json(path, expected_type=dict)
        if state.get("status") in {"running", "queued"} and not _active(state):
            with file_lock(path):
                state = read_json(path, expected_type=dict)
                if state.get("status") in {"running", "queued"} and not _active(state):
                    state.update(status="interrupted", error="链接更新进程已退出，可重试", updated_at=_now(), finished_at=_now())
                    state["items"][0].update(status="failed", error=state["error"])
                    atomic_write_json(path, state, backup=False)
        result.append({**state, "active": _active(state)})
    return result


def start_creator_task(project_root, url, *, task_id=None):
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "space.bilibili.com" or parsed.username or parsed.password or parsed.port:
        raise ValueError("请填写 Bilibili UP 主主页链接")
    match = re.fullmatch(r"/([0-9]+)(?:/video)?/?", parsed.path)
    if not match:
        raise ValueError("UP 主主页应类似 https://space.bilibili.com/123456/video")
    url = "https://space.bilibili.com/" + match[1] + "/video"
    root = Path(project_root).resolve()
    task_id = task_id or "creator-" + uuid.uuid4().hex[:16]
    path = creator_task_path(root, task_id)
    with file_lock(path.parent / "control", timeout=15):
        active = next((state for state in list_creator_tasks(root) if state.get("active")), None)
        if active:
            return {**active, "already_running": True}
        stamp = _now()
        state = {"batch_id": task_id, "created_at": stamp, "updated_at": stamp,
                 "started_at": stamp, "status": "queued", "options": {"url": url},
                 "items": [{"url": url, "status": "pending"}], "pid": os.getpid(),
                 "process_start_id": _identity(os.getpid()), "run_token": uuid.uuid4().hex}
        atomic_write_json(path, state, backup=False)
        package = str(Path(__file__).resolve().parent.parent)
        args = [str(root), task_id, state["run_token"]]
        command = ([sys.executable, "--admin-creator-worker", *args] if getattr(sys, "frozen", False)
                   else [sys.executable, "-m", "bili_recipe_notes.admin_creator_worker", *args])
        env = {**os.environ, DATA_DIR_ENV: str(data_root(root))}
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (package, env.get("PYTHONPATH"))))
        try:
            log_path = path.with_suffix(".log")
            if log_path.is_symlink():
                raise ValueError("任务日志不能为符号链接")
            with log_path.open("ab") as log:
                process = subprocess.Popen(command, cwd=package, env=env, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=log, start_new_session=True)
            _PROCESSES[task_id] = process
            with file_lock(path):
                current = read_json(path, expected_type=dict)
                if current.get("run_token") == state["run_token"] and current.get("status") == "queued":
                    current.update(pid=process.pid, process_start_id=_identity(process.pid))
                    atomic_write_json(path, current, backup=False)
                state = current
        except Exception as exc:
            state.update(status="failed", error=str(exc), finished_at=_now(), updated_at=_now())
            state["items"][0].update(status="failed", error=str(exc))
            atomic_write_json(path, state, backup=False)
            raise
        return {**state, "active": _active(state)}


def run_creator_task(project_root, task_id, token):
    path = creator_task_path(project_root, task_id)
    with file_lock(path):
        state = read_json(path, expected_type=dict)
        if state.get("run_token") != token:
            raise ValueError("UP 主任务运行标识过期")
        state.update(status="running", pid=os.getpid(), process_start_id=_identity(os.getpid()), updated_at=_now())
        state["items"][0]["status"] = "running"
        atomic_write_json(path, state, backup=False)
    try:
        config = load_config(Path(project_root))
        result = crawl_and_archive_creator(state["options"]["url"], config.cookies, config.out_dir,
                                           lambda message: print(message, flush=True))
        state["items"][0].update(status="completed", count=len(result.crawl.videos),
                                  warnings=result.crawl.warnings, complete=result.crawl.complete)
        state.update(status="completed")
    except Exception as exc:
        state["items"][0].update(status="failed", error=str(exc))
        state.update(status="failed", error=str(exc))
        print(str(exc), file=sys.stderr, flush=True)
    state.update(updated_at=_now(), finished_at=_now())
    with file_lock(path):
        current = read_json(path, expected_type=dict)
        if current.get("run_token") == token:
            atomic_write_json(path, state, backup=False)
    return state


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        raise ValueError("UP 主后台进程参数无效")
    state = run_creator_task(*args)
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
