"""Persistent background batches for optional, independently stored AI covers."""
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

from .ai_review_runner import _error_kind as _shared_error_kind, _identity
from .batch_runner import _pid_is_alive
from .cover_policy import has_manually_approved_cover
from .data_paths import DATA_DIR_ENV, data_root, relocate_value
from .frame_selection_eligibility import frame_selection_eligibility
from .mobile_sync import recipe_visibility_states
from .storage import atomic_write_json, file_lock, read_json

_PROCESSES: dict[str, subprocess.Popen] = {}
_ACTIVE = {"queued", "running"}
_ITEM_STATES = {"pending", "running", "completed", "not_found", "needs_review", "skipped", "failed"}



def generate_frame_candidate(folder: Path, **kwargs) -> dict:
    from .frame_selection_service import generate_frame_candidate as generate
    return generate(folder, **kwargs)


def load_frame_candidate(out_dir: Path, folder: Path) -> dict | None:
    from .frame_selection_service import load_frame_candidate as load
    return load(out_dir, folder)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("菜谱输出目录不存在")
    _tasks(root)
    return root


def _tasks(root: Path) -> Path:
    runtime = root / ".frame-selection"
    tasks = runtime / "tasks"
    if runtime.is_symlink() or tasks.is_symlink():
        raise ValueError("选帧任务目录不能为符号链接")
    return tasks


def _path(root: Path, batch_id: str) -> Path:
    if not isinstance(batch_id, str) or not re.fullmatch(r"frame-[a-f0-9]{16}", batch_id):
        raise ValueError("选帧任务 ID 无效")
    path = _tasks(root) / (batch_id + ".json")
    if path.is_symlink():
        raise ValueError("选帧任务文件不能为符号链接")
    return path


def _folder(root: Path, value: str | Path) -> Path:
    folder = Path(value)
    folder = folder if folder.is_absolute() else root / folder
    if folder.is_symlink() or any(parent.is_symlink() for parent in folder.parents):
        raise ValueError("菜谱目录不能为符号链接")
    folder = folder.resolve()
    if folder == root or not folder.is_relative_to(root) or any(part.startswith(".") for part in folder.relative_to(root).parts):
        raise ValueError("菜谱必须位于当前输出目录内")
    if not (folder / "recipe.json").is_file() or (folder / "recipe.json").is_symlink():
        raise ValueError("菜谱文件不存在或为符号链接")
    return folder


def _normalize_options(options: dict) -> dict:
    from .frame_selection_service import normalize_frame_options

    values = normalize_frame_options(options)
    # Child processes use the project cwd; preserve paths entered elsewhere.
    for key in ("model", "cookies", "python_executable"):
        value = values.get(key)
        if value:
            expanded = str(Path(value).expanduser())
            values[key] = str(Path(expanded).absolute()) if key != "python_executable" or os.path.dirname(expanded) else expanded
    return values


def _preflight_options(options: dict) -> dict:
    from .frame_selection_service import validate_frame_environment

    return validate_frame_environment(options)


def _has_matching_candidate(root: Path, folder: Path, options: dict) -> bool:
    from .frame_selection_service import SELECTION_STRATEGY_VERSION

    candidate = load_frame_candidate(root, folder)
    if (candidate is None or not isinstance(candidate.get("options"), dict)
            or candidate.get("selection_strategy_version") != SELECTION_STRATEGY_VERSION):
        return False
    try:
        previous = _normalize_options(candidate["options"])
    except ValueError:
        return False
    relevant = lambda values: {key: value for key, value in values.items() if key not in {"cookies", "keep_video"}}
    return relevant(previous) == relevant(options)


def _active(state: dict) -> bool:
    pid = state.get("pid")
    if state.get("status") not in _ACTIVE or type(pid) is not int or pid <= 0:
        return False
    if sys.platform != "win32" and not _pid_is_alive(pid):
        return False
    identity = _identity(pid)
    if identity:
        return identity == state.get("process_start_id")
    process = _PROCESSES.get(state["batch_id"])
    return bool(process and process.pid == pid and process.poll() is None)


def _write(root: Path, state: dict) -> dict:
    state.update(updated_at=_now(), total=len(state["items"]))
    for status in ("completed", "failed", "skipped", "not_found", "needs_review"):
        state[status] = sum(item["status"] == status for item in state["items"])
    state["processed"] = sum(state[status] for status in ("completed", "failed", "skipped", "not_found", "needs_review"))
    state["pending"] = sum(item["status"] in {"pending", "running"} for item in state["items"])
    atomic_write_json(_path(root, state["batch_id"]), state, backup=False)
    return state


def _read(root: Path, batch_id: str) -> dict:
    state = relocate_value(read_json(_path(root, batch_id), expected_type=dict))
    if state.get("batch_id") != batch_id or state.get("out_dir") != str(root) or not isinstance(state.get("items"), list):
        raise ValueError("选帧任务状态无效")
    if any(not isinstance(item, dict) or not isinstance(item.get("folder"), str) or item.get("status") not in _ITEM_STATES for item in state["items"]):
        raise ValueError("选帧任务条目无效")
    return state


def _update(root: Path, batch_id: str, callback: Callable[[dict], None], *, token: str | None = None) -> dict:
    with file_lock(_path(root, batch_id)):
        state = _read(root, batch_id)
        if token is not None and state.get("run_token") != token:
            raise ValueError("选帧任务运行标识已过期")
        callback(state)
        return _write(root, state)


def _finish(state: dict, status: str, **updates) -> None:
    state.update(status=status, current_folder=None, current_progress=None, finished_at=_now(), **updates)
    for run in state.get("runs", []):
        if run.get("run_token") == state.get("run_token"):
            run.update(status=status, finished_at=state["finished_at"], run_processed=state.get("run_processed", 0))


def list_frame_selection_batches(out_dir: str | Path) -> list[dict]:
    root = _root(out_dir)
    for key, process in list(_PROCESSES.items()):
        if process.poll() is not None and _PROCESSES.get(key) is process:
            _PROCESSES.pop(key, None)
    states = []
    for path in _tasks(root).glob("frame-*.json"):
        state = _read(root, path.stem)
        if state.get("status") in _ACTIVE and not _active(state):
            def interrupted(current):
                if current.get("status") in _ACTIVE and not _active(current):
                    _finish(current, "interrupted", error="后台进程已退出，可继续任务")
            state = _update(root, path.stem, interrupted)
        states.append({**state, "active": _active(state)})
    return sorted(states, key=lambda state: state.get("updated_at", ""), reverse=True)


def _launch(root: Path, state: dict) -> dict:
    token = uuid.uuid4().hex
    state.update(status="queued", run_token=token, pid=os.getpid(), process_start_id=_identity(os.getpid()),
                 stop_requested=False, current_folder=None, current_progress=None, error=None, pause_reason=None,
                 finished_at=None, consecutive_failures=0, run_processed=0)
    state.setdefault("run_limit", 0)
    state.setdefault("runs", []).append({"run_token": token, "started_at": _now(), "options": dict(state["options"]),
                                       "run_limit": state["run_limit"], "run_scope": state.get("run_scope", "pending"),
                                       "run_targets": list(state.get("run_targets", []))})
    with file_lock(_path(root, state["batch_id"])):
        _write(root, state)
    args = [str(root), state["batch_id"], token]
    command = ([sys.executable, "--frame-selection-worker", *args] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "bili_recipe_notes.frame_selection_worker", *args])
    package = str(Path(__file__).resolve().parent.parent)
    env = {**os.environ, DATA_DIR_ENV: str(data_root())}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (package, env.get("PYTHONPATH"))))
    try:
        log_path = _tasks(root) / (state["batch_id"] + ".log")
        if log_path.is_symlink():
            raise ValueError("选帧任务日志不能为符号链接")
        with log_path.open("ab") as log:
            process = subprocess.Popen(command, cwd=package, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, start_new_session=True)
        _PROCESSES[state["batch_id"]] = process
        state = _update(root, state["batch_id"], lambda current: current.update(pid=process.pid, process_start_id=_identity(process.pid)), token=token)
    except Exception as exc:
        _update(root, state["batch_id"], lambda current: _finish(current, "failed", error=str(exc)), token=token)
        raise
    return {**state, "active": _active(state)}


def _manual_cover(folder: Path) -> bool:
    return has_manually_approved_cover(read_json(folder / "recipe.json", expected_type=dict))


def start_frame_selection_batch(out_dir: str | Path, *, limit: int | None = 10, only_pending: bool = True,
                                folders: list[str | Path] | None = None, published_only: bool = True,
                                skip_confirmed: bool = True, create_only: bool = False,
                                run_limit: int = 0, **options) -> dict:
    root = _root(out_dir)
    options = _normalize_options(options)
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("本轮数量必须为正整数")
    if type(run_limit) is not int or run_limit < 0:
        raise ValueError("每次执行数量必须为非负整数，0 表示不限")
    if any(type(value) is not bool for value in (only_pending, published_only, skip_confirmed, create_only)):
        raise ValueError("选帧范围设置必须为布尔值")
    with file_lock(_tasks(root) / "control", timeout=15):
        active = next((state for state in list_frame_selection_batches(root) if state["active"]), None)
        if active and not create_only:
            return {**active, "already_running": True}
        if not create_only:
            options = _preflight_options(options)
        selected = [_folder(root, folder) for folder in folders] if folders is not None else None
        candidates = []
        for value, visible in recipe_visibility_states(root, folders=selected).items():
            if not visible.get("content_visible") or (published_only and not visible.get("published", True)):
                continue
            folder = _folder(root, value)
            if frame_selection_eligibility(folder, options["requirement"], visible):
                continue
            # Errors in an individual recipe are persisted by the worker.
            try:
                if skip_confirmed and _manual_cover(folder):
                    continue
                if only_pending and _has_matching_candidate(root, folder, options):
                    continue
            except (OSError, ValueError, RuntimeError):
                pass
            candidates.append(folder)
            if limit and len(candidates) >= limit:
                break
        state = {"schema_version": 1, "batch_id": "frame-" + uuid.uuid4().hex[:16], "out_dir": str(root),
                 "created_at": _now(), "status": "completed", "options": options, "only_pending": only_pending,
                 "published_only": published_only, "skip_confirmed": skip_confirmed, "stop_requested": False,
                 "current_folder": None, "current_progress": None, "run_limit": run_limit, "run_processed": 0,
                 "items": [{"folder": folder.relative_to(root).as_posix(), "status": "pending"} for folder in candidates]}
        if candidates and not create_only:
            return _launch(root, state)
        if candidates:
            state["status"] = "pending"
        else:
            _finish(state, "completed")
        with file_lock(_path(root, state["batch_id"])):
            return {**_write(root, state), "active": False}


def stop_frame_selection_batch(out_dir: str | Path, batch_id: str) -> dict:
    root = _root(out_dir)
    with file_lock(_tasks(root) / "control", timeout=15):
        state = _update(root, batch_id, lambda current: current.update(stop_requested=True) if current["status"] in _ACTIVE else None)
    return {**state, "active": _active(state)}


def resume_frame_selection_batch(out_dir: str | Path, batch_id: str, *, retry_failed: bool = False,
                                 retry_not_found: bool = False, run_limit: int | None = None, **options) -> dict:
    root = _root(out_dir)
    if run_limit is not None and (type(run_limit) is not int or run_limit < 0):
        raise ValueError("每次执行数量必须为非负整数，0 表示不限")
    if type(retry_not_found) is not bool or (retry_not_found and retry_failed):
        raise ValueError("无匹配重试必须单独执行")
    with file_lock(_tasks(root) / "control", timeout=15):
        active = next((state for state in list_frame_selection_batches(root) if state["active"]), None)
        if active:
            if retry_not_found:
                raise RuntimeError("选帧任务正在运行，请暂停后再重试无匹配项")
            if active["batch_id"] != batch_id:
                raise RuntimeError("当前目录已有选帧任务运行")
            return active
        state = _read(root, batch_id)
        state["options"] = _preflight_options(_normalize_options({**state["options"], **options}))
        state["run_limit"] = state.get("run_limit", 0) if run_limit is None else run_limit
        previous_targets = set(state.get("run_targets", [])) if state.get("run_scope") == "not_found" else set()
        for item in state["items"]:
            if item["status"] == "running" or (retry_failed and item["status"] == "failed"):
                for attempt in item.get("attempts", []):
                    if not attempt.get("finished_at"):
                        attempt.update(status="interrupted", finished_at=_now())
                item.update(status="not_found" if item["folder"] in previous_targets else "pending",
                            error=None, error_kind=None, finished_at=None)
        if retry_not_found:
            # Resume an unfinished targeted round before retrying its already
            # checked no-matches again. Original pending items stay untouched.
            remaining = [item["folder"] for item in state["items"]
                         if item["status"] == "not_found" and item["folder"] in previous_targets]
            targets = remaining or [item["folder"] for item in state["items"] if item["status"] == "not_found"]
            state.update(run_scope="not_found", run_targets=targets)
        else:
            state.update(run_scope="pending", run_targets=[])
        return _launch(root, state) if _next_item_index(state) is not None else {**state, "active": False}


def _next_item_index(state: dict) -> int | None:
    if state.get("run_scope") == "not_found":
        targets = set(state.get("run_targets", []))
        return next((index for index, item in enumerate(state["items"])
                     if item["status"] == "not_found" and item["folder"] in targets), None)
    return next((index for index, item in enumerate(state["items"]) if item["status"] == "pending"), None)


def _error_kind(exc: Exception) -> str:
    from .frame_selection_service import FrameSelectionConfigurationError

    if isinstance(exc, FrameSelectionConfigurationError):
        return "configuration"
    category = getattr(exc, "error_kind", None)
    if category in {"configuration", "transient", "validation", "permanent"}:
        return category
    message = str(exc).casefold()
    if isinstance(exc, (ImportError, ModuleNotFoundError)) or any(fragment in message for fragment in (
        "选图独立环境", "选图 python", "缺少选图子模块", "model directory", "模型目录", "cuda", "no module named",
    )):
        return "configuration"
    return _shared_error_kind(exc)


def run_frame_selection_batch(out_dir: str | Path, batch_id: str, token: str) -> dict:
    root = _root(out_dir)
    with file_lock(_tasks(root) / "control", timeout=30):
        state = _read(root, batch_id)
        if state.get("run_token") != token:
            raise ValueError("选帧任务运行标识已过期")
        if state.get("status") == "running" and _active(state):
            return state
        if state.get("status") not in {"queued", "paused", "interrupted"}:
            return state
        _update(root, batch_id, lambda current: current.update(status="running", pid=os.getpid(), process_start_id=_identity(os.getpid())), token=token)
    while True:
        state = _read(root, batch_id)
        if state.get("run_token") != token:
            return state
        index = _next_item_index(state)
        limit_reached = bool(state.get("run_limit") and state.get("run_processed", 0) >= state["run_limit"])
        if index is None or state.get("stop_requested") or limit_reached:
            has_pending = any(item["status"] == "pending" for item in state["items"])
            paused = index is not None or has_pending
            reason = ("requested" if state.get("stop_requested") else "batch_limit" if index is not None
                      else "retry_scope_completed") if paused else None
            return _update(root, batch_id, lambda current: _finish(current,
                "paused" if paused else "completed_with_errors" if current["failed"] else "completed",
                pause_reason=reason), token=token)
        name = state["items"][index]["folder"]

        def starting(current):
            current.update(current_folder=name, current_progress={"phase": "preparing"})
            current["items"][index].update(status="running", started_at=_now(), error=None, error_kind=None)
        _update(root, batch_id, starting, token=token)
        for attempt in range(1, 4):
            if attempt > 1:
                latest = _read(root, batch_id)
                if latest.get("run_token") != token:
                    raise ValueError("选帧任务运行标识已过期")
                if latest.get("stop_requested"):
                    break
            def attempting(current):
                current["items"][index].setdefault("attempts", []).append({"run_token": token, "attempt": attempt,
                    "started_at": _now(), "status": "running", "options": dict(state["options"]), "progress": []})
            _update(root, batch_id, attempting, token=token)

            def progress(event: dict):
                def reported(current):
                    snapshot = {**event, "updated_at": _now()}
                    current["current_progress"] = snapshot
                    current["items"][index]["attempts"][-1]["progress"].append(snapshot)
                _update(root, batch_id, reported, token=token)

            try:
                folder = _folder(root, name)
                visible = recipe_visibility_states(root, folders=[folder])[str(folder)]
                if not visible.get("content_visible") or (state["published_only"] and not visible.get("published", True)):
                    result = {"status": "skipped", "error": None, "skip_reason": "not_visible_or_published"}
                elif reason := frame_selection_eligibility(folder, state["options"]["requirement"], visible):
                    result = {"status": "skipped", "error": None, "skip_reason": reason}
                elif state["skip_confirmed"] and _manual_cover(folder):
                    result = {"status": "skipped", "error": None, "skip_reason": "manually_confirmed"}
                elif (state.get("run_scope") != "not_found" and state["only_pending"]
                      and _has_matching_candidate(root, folder, state["options"])):
                    result = {"status": "skipped", "error": None, "skip_reason": "valid_candidate"}
                else:
                    candidate = generate_frame_candidate(folder, out_dir=root, options=state["options"], progress=progress)
                    if candidate.get("status") not in {"ok", "not_found", "needs_review"}:
                        raise ValueError("选帧结果状态无效")
                    result = {"status": "completed" if candidate["status"] == "ok" else candidate["status"], "error": None, "result_status": candidate["status"],
                              **{key: candidate.get(key) for key in ("image_path", "timestamp", "reason", "run_id", "result_path", "stdout_path", "stderr_path")}}
            except Exception as exc:
                result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:2000], "error_kind": _error_kind(exc)}

            def attempted(current):
                record = current["items"][index]["attempts"][-1]
                record.update(result, finished_at=_now())
                if result.get("error_kind") == "transient" and attempt < 3:
                    record["retry_delay_seconds"] = 2 ** (attempt - 1)
            current = _update(root, batch_id, attempted, token=token)
            if result.get("error_kind") != "transient" or attempt == 3 or current.get("stop_requested"):
                break
            time.sleep(2 ** (attempt - 1))

        def finished(current):
            current["items"][index].update(result, finished_at=_now())
            if current.get("run_scope") == "not_found":
                current["run_targets"] = [folder for folder in current.get("run_targets", []) if folder != name]
            current.update(current_folder=None, current_progress=None)
            current["run_processed"] = current.get("run_processed", 0) + 1
            current["consecutive_failures"] = current.get("consecutive_failures", 0) + 1 if result["status"] == "failed" else 0
            if result.get("error_kind") == "configuration":
                _finish(current, "paused", error="选帧环境不可用，请检查后重试",
                        pause_reason="configuration")
        state = _update(root, batch_id, finished, token=token)
        print(f"{_now()} {name}: {result.get('result_status', result['status'])}", flush=True)
        if state["status"] == "paused":
            return state
