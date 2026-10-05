"""Persistent, opt-in generation of menu introductions, separate from auditing."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .ai_review_runner import _identity, _error_kind
from .batch_runner import _pid_is_alive
from .data_paths import DATA_DIR_ENV, data_root, relocate_value
from .dish_info import generate_dish_info, load_dish_info
from .llm import SUPPORTED_LLM_PROVIDERS
from .mobile_sync import recipe_visibility_states
from .storage import atomic_write_json, file_lock, read_json

_PROCESSES: dict[str, subprocess.Popen] = {}
_OPTIONS = {"provider", "openai_model", "local_llm_command", "codex_model", "codex_profile", "cli_extra_instructions"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir() or (root / ".dish-info").is_symlink():
        raise ValueError("菜谱目录不存在，或介绍任务目录为符号链接")
    return root


def _path(root: Path, batch_id: str) -> Path:
    if not isinstance(batch_id, str) or not re.fullmatch(r"intro-[a-f0-9]{16}", batch_id):
        raise ValueError("菜品介绍任务 ID 无效")
    path = root / ".dish-info" / (batch_id + ".json")
    if path.is_symlink():
        raise ValueError("介绍任务文件不能为符号链接")
    return path


def _folder(root: Path, value: str | Path) -> Path:
    path = Path(value)
    path = path if path.is_absolute() else root / path
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("菜品目录不能为符号链接")
    path = path.resolve()
    if path == root or not path.is_relative_to(root) or any(part.startswith(".") for part in path.relative_to(root).parts):
        raise ValueError("菜品必须位于当前输出目录内")
    if not (path / "recipe.json").is_file():
        raise ValueError("菜谱不存在")
    return path


def _active(state: dict) -> bool:
    pid = state.get("pid")
    if state.get("status") not in {"queued", "running"} or type(pid) is not int or pid <= 0:
        return False
    # On Windows identity uses query-only handles; os.kill(pid, 0) is unsafe.
    if sys.platform != "win32" and not _pid_is_alive(pid):
        return False
    identity = _identity(pid)
    if identity:
        return identity == state.get("process_start_id")
    process = _PROCESSES.get(state["batch_id"])
    return bool(process and process.poll() is None and process.pid == state["pid"])


def _write(root: Path, state: dict) -> dict:
    state.update(updated_at=_now(), total=len(state["items"]))
    for status in ("completed", "failed", "skipped"):
        state[status] = sum(item["status"] == status for item in state["items"])
    state["pending"] = sum(item["status"] in {"pending", "running"} for item in state["items"])
    atomic_write_json(_path(root, state["batch_id"]), state, backup=False)
    return state


def _read(root: Path, batch_id: str) -> dict:
    state = relocate_value(read_json(_path(root, batch_id), expected_type=dict))
    if state.get("out_dir") != str(root) or state.get("batch_id") != batch_id or not isinstance(state.get("items"), list):
        raise ValueError("菜品介绍任务状态无效")
    return state


def _update(root: Path, batch_id: str, fn: Callable[[dict], None], *, token: str | None = None) -> dict:
    with file_lock(_path(root, batch_id)):
        state = _read(root, batch_id)
        if token is not None and state.get("run_token") != token:
            raise ValueError("任务运行标识已过期")
        fn(state)
        return _write(root, state)


def _validate_options(options: dict) -> None:
    if set(options) - _OPTIONS or options.get("provider") not in SUPPORTED_LLM_PROVIDERS - {"none"}:
        raise ValueError("请先选择可用的 LLM 服务和配置")


def _has_valid_info(folder: Path, web_search: bool) -> bool:
    previous = load_dish_info(folder)
    return bool(previous and (not web_search or previous.get("search_status") in {"completed", "no_results"}))


def _finish_run(state: dict, status: str, **updates) -> None:
    finished_at = _now()
    state.update(status=status, current_folder=None, finished_at=finished_at, **updates)
    for run in state.get("runs", []):
        if run.get("run_token") == state.get("run_token"):
            run.update(status=status, finished_at=finished_at)


def list_dish_info_batches(out_dir: str | Path) -> list[dict]:
    root = _root(out_dir)
    for key, process in list(_PROCESSES.items()):
        if process.poll() is not None:
            _PROCESSES.pop(key, None)
    states = []
    for path in (root / ".dish-info").glob("intro-*.json"):
        state = _read(root, path.stem)
        if state.get("status") in {"queued", "running"} and not _active(state):
            def interrupted(current):
                if current.get("status") in {"queued", "running"} and not _active(current):
                    _finish_run(current, "interrupted", error="后台进程已退出，可继续任务")
            state = _update(root, path.stem, interrupted)
        states.append({**state, "active": _active(state)})
    return sorted(states, key=lambda state: state.get("updated_at", ""), reverse=True)


def _launch(root: Path, state: dict) -> dict:
    token = uuid.uuid4().hex
    state.update(status="queued", run_token=token, pid=os.getpid(), process_start_id=_identity(os.getpid()),
                 stop_requested=False, current_folder=None, error=None, pause_reason=None, finished_at=None, consecutive_failures=0)
    state.setdefault("runs", []).append({"run_token": token, "started_at": _now(), "llm_options": dict(state["options"])})
    _write(root, state)
    args = [str(root), state["batch_id"], token]
    command = ([sys.executable, "--dish-info-worker", *args] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "bili_recipe_notes.dish_info_worker", *args])
    package = str(Path(__file__).resolve().parent.parent)
    env = {**os.environ, DATA_DIR_ENV: str(data_root())}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [package, env.get("PYTHONPATH")]))
    log_path = root / ".dish-info" / (state["batch_id"] + ".log")
    try:
        if log_path.is_symlink():
            raise ValueError("任务日志不能为符号链接")
        with log_path.open("ab") as log:
            process = subprocess.Popen(command, cwd=package, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        _PROCESSES[state["batch_id"]] = process
        state.update(pid=process.pid, process_start_id=_identity(process.pid))
    except Exception as exc:
        _finish_run(state, "failed", error=str(exc))
        _write(root, state)
        raise
    _write(root, state)
    return {**state, "active": _active(state)}


def start_dish_info_batch(out_dir: str | Path, *, limit: int | None = 10, only_pending: bool = True,
                          web_search: bool = False, folders: list[str | Path] | None = None, **options) -> dict:
    root = _root(out_dir)
    _validate_options(options)
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("本轮数量必须为正整数")
    with file_lock(root / ".dish-info" / "control", timeout=15):
        active = next((state for state in list_dish_info_batches(root) if state["active"]), None)
        if active:
            return active
        selected = [_folder(root, value) for value in folders] if folders is not None else None
        visibility = recipe_visibility_states(root, folders=selected)
        candidates = []
        for value, visible in visibility.items():
            if not visible.get("content_visible") or not visible.get("published", True):
                continue
            folder = _folder(root, value)
            # A local-only introduction still qualifies when adding web context.
            if not only_pending or not _has_valid_info(folder, web_search):
                candidates.append(folder)
        if limit:
            candidates = candidates[:limit]
        state = {"schema_version": 1, "batch_id": "intro-" + uuid.uuid4().hex[:16], "out_dir": str(root),
                 "created_at": _now(), "status": "completed", "options": options, "web_search": web_search,
                 "only_pending": only_pending, "items": [{"folder": folder.relative_to(root).as_posix(), "status": "pending"} for folder in candidates]}
        return _launch(root, state) if candidates else _write(root, state)


def stop_dish_info_batch(out_dir: str | Path, batch_id: str) -> dict:
    return _update(_root(out_dir), batch_id, lambda state: state.update(stop_requested=True))


def resume_dish_info_batch(out_dir: str | Path, batch_id: str, *, retry_failed: bool = False, **options) -> dict:
    root = _root(out_dir)
    with file_lock(root / ".dish-info" / "control", timeout=15):
        active = next((state for state in list_dish_info_batches(root) if state["active"]), None)
        if active:
            return active
        state = _read(root, batch_id)
        state["options"] = {**state["options"], **options}
        _validate_options(state["options"])
        for item in state["items"]:
            if item["status"] == "running" or (retry_failed and item["status"] == "failed"):
                for attempt in item.get("attempts", []):
                    if not attempt.get("finished_at"):
                        attempt.update(status="interrupted", finished_at=_now())
                item.update(status="pending", error=None, error_kind=None, finished_at=None)
        return _launch(root, state) if any(item["status"] == "pending" for item in state["items"]) else state


def run_dish_info_batch(out_dir: str | Path, batch_id: str, token: str) -> dict:
    root = _root(out_dir)
    with file_lock(root / ".dish-info" / "control", timeout=30):
        state = _read(root, batch_id)
        if state.get("run_token") != token:
            raise ValueError("任务运行标识已过期")
        if state.get("status") == "running" and _active(state):
            return state
        _update(root, batch_id, lambda current: current.update(status="running", pid=os.getpid(), process_start_id=_identity(os.getpid())), token=token)
    while True:
        state = _read(root, batch_id)
        if state.get("run_token") != token:
            return state
        index = next((index for index, item in enumerate(state["items"]) if item["status"] == "pending"), None)
        if index is None or state.get("stop_requested"):
            return _update(root, batch_id, lambda current: _finish_run(current,
                "paused" if index is not None else "completed_with_errors" if current["failed"] else "completed",
                pause_reason="requested" if index is not None else None), token=token)
        name = state["items"][index]["folder"]

        def starting(current):
            current["current_folder"] = name
            current["items"][index].update(status="running", started_at=_now(), error=None, error_kind=None)
        _update(root, batch_id, starting, token=token)
        result = {"status": "completed", "error": None}
        for attempt in range(1, 4):
            def attempting(current):
                current["items"][index].setdefault("attempts", []).append({"run_token": token, "attempt": attempt,
                    "started_at": _now(), "status": "running", "llm_options": dict(state["options"])})
            _update(root, batch_id, attempting, token=token)
            try:
                folder = _folder(root, name)
                visibility = recipe_visibility_states(root, folders=[folder])[str(folder)]
                if not visibility.get("content_visible") or not visibility.get("published", True):
                    result = {"status": "skipped", "error": None, "skip_reason": "not_visible_or_published"}
                elif state["only_pending"] and _has_valid_info(folder, state["web_search"]):
                    result = {"status": "skipped", "error": None, "skip_reason": "valid_info"}
                else:
                    info = generate_dish_info(folder, web_search=state["web_search"], only_pending=state["only_pending"],
                                              history_dir=root / ".dish-info" / "history", **state["options"])
                    result = {"status": "completed", "error": None, "search_status": info.get("search_status")}
            except Exception as exc:
                result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:2000], "error_kind": _error_kind(exc)}
            def attempted(current):
                current["items"][index]["attempts"][-1].update(result, finished_at=_now())
                if result.get("error_kind") == "transient" and attempt < 3:
                    current["items"][index]["attempts"][-1]["retry_delay_seconds"] = 2 ** (attempt - 1)
            _update(root, batch_id, attempted, token=token)
            if result.get("error_kind") != "transient" or attempt == 3:
                break
            time.sleep(min(4, 2 ** (attempt - 1)))

        def finished(current):
            current["items"][index].update(result, finished_at=_now())
            current["current_folder"] = None
            current["consecutive_failures"] = current.get("consecutive_failures", 0) + 1 if result["status"] == "failed" else 0
            if result.get("error_kind") == "configuration" or current["consecutive_failures"] >= 5:
                _finish_run(current, "paused", error="连续失败或模型配置有误，请检查后重试",
                            pause_reason="configuration" if result.get("error_kind") == "configuration" else "consecutive_failures")
        state = _update(root, batch_id, finished, token=token)
        print(f"{_now()} {name}: {result['status']}", flush=True)
        if state["status"] == "paused":
            return state
