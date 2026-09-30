"""Durable, sequential AI-review batches independent of the UI process."""

from __future__ import annotations

from .output_layout import iter_output_folders
from .data_paths import relocate_value

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .ai_review import _now, review_recipe
from .batch_runner import _pid_is_alive, _process_start_id
from .data_paths import DATA_DIR_ENV, data_root
from .llm import SUPPORTED_LLM_PROVIDERS
from .review_policy import load_ai_review
from .storage import atomic_write_json, file_lock

_ACTIVE = {"queued", "running"}
_OPTIONS = {"openai_model", "local_llm_command", "codex_model", "codex_profile", "cli_extra_instructions"}
_PROCESSES: dict[str, subprocess.Popen] = {}
_RETIRED_PROCESSES: list[subprocess.Popen] = []


def _windows_process_identity(pid: int) -> str | None:
    """Use query-only Win32 handles: os.kill(pid, 0) is unsafe on Windows."""

    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return None
        creation = times[0]
        return f"windows:{creation.dwHighDateTime}:{creation.dwLowDateTime}"
    finally:
        kernel.CloseHandle(handle)


def _identity(pid: int) -> str | None:
    return _windows_process_identity(pid) if sys.platform == "win32" else _process_start_id(pid)


def _reap_finished_processes() -> None:
    for key, process in list(_PROCESSES.items()):
        if process.poll() is not None and _PROCESSES.get(key) is process:
            _PROCESSES.pop(key, None)
    _RETIRED_PROCESSES[:] = [process for process in _RETIRED_PROCESSES if process.poll() is None]


def _root(out_dir: str | Path) -> Path:
    root = Path(out_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("菜谱输出目录不存在")
    runtime = root / ".ai-review"
    if runtime.is_symlink():
        raise ValueError("AI 审核状态目录不能是符号链接")
    return root


def _state_path(root: Path, batch_id: str) -> Path:
    if not isinstance(batch_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", batch_id):
        raise ValueError("AI 审核任务 ID 无效")
    path = root / ".ai-review" / f"{batch_id}.json"
    if path.is_symlink():
        raise ValueError("AI 审核状态文件不能是符号链接")
    return path


def _folder(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents if parent != root):
        raise ValueError("AI 审核拒绝符号链接菜谱目录")
    candidate = candidate.resolve()
    if candidate == root or not candidate.is_relative_to(root) or any(
        part.startswith(".") for part in candidate.relative_to(root).parts
    ):
        raise ValueError("AI 审核只能处理输出目录内的菜谱子目录")
    if not candidate.is_dir() or not (candidate / "recipe.json").is_file():
        raise ValueError("仅支持已生成 recipe.json 的菜谱目录")
    return candidate


def _read(root: Path, batch_id: str) -> dict:
    path = _state_path(root, batch_id)
    state = relocate_value(json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(state, dict) or state.get("batch_id") != batch_id or state.get("out_dir") != str(root):
        raise ValueError("AI 审核任务状态无效")
    return state


def _counts(state: dict) -> dict:
    items = state["items"]
    state.update(
        total=len(items),
        completed=sum(item["status"] == "completed" for item in items),
        failed=sum(item["status"] == "failed" for item in items),
        skipped=sum(item["status"] == "skipped" for item in items),
        pending=sum(item["status"] in {"pending", "running"} for item in items),
        updated_at=_now(),
    )
    return state


def _write(root: Path, state: dict) -> dict:
    _counts(state)
    atomic_write_json(_state_path(root, state["batch_id"]), state, backup=False)
    return state


def _update(root: Path, batch_id: str, change: Callable[[dict], None]) -> dict:
    path = _state_path(root, batch_id)
    with file_lock(path):
        state = _read(root, batch_id)
        change(state)
        return _write(root, state)


def _alive(state: dict) -> bool:
    pid = state.get("pid")
    if type(pid) is not int or pid <= 0:
        return False
    if sys.platform == "win32":
        identity = _windows_process_identity(pid)
        return bool(identity and identity == state.get("process_start_id"))
    if not _pid_is_alive(pid):
        return False
    identity = state.get("process_start_id")
    if identity:
        return identity == _process_start_id(pid)
    # On platforms lacking process birth IDs, use our Popen handle. After a
    # restart, only an identifiable worker may be trusted as still active.
    process = _PROCESSES.get(state["batch_id"])
    return process is not None and process.pid == pid and process.poll() is None


def _decorate(state: dict) -> dict:
    state["active"] = state["status"] in _ACTIVE and _alive(state)
    return state


def get_ai_review_batch(out_dir: str | Path, batch_id: str | None = None) -> dict | None:
    _reap_finished_processes()
    root = _root(out_dir)
    if batch_id is None:
        states = list_ai_review_batches(root)
        return next((state for state in states if state["active"]), states[0] if states else None)
    path = _state_path(root, batch_id)
    if not path.is_file():
        return None
    state = _read(root, batch_id)
    if state["status"] in _ACTIVE and not _alive(state):
        def interrupted(current: dict) -> None:
            if current["status"] in _ACTIVE and not _alive(current):
                current.update(status="interrupted", current_folder=None, error="后台审核进程已退出，可继续未完成任务")
        state = _update(root, batch_id, interrupted)
    return _decorate(state)


def list_ai_review_batches(out_dir: str | Path) -> list[dict]:
    root = _root(out_dir)
    states = []
    for path in (root / ".ai-review").glob("*.json"):
        try:
            state = get_ai_review_batch(root, path.stem)
        except (OSError, ValueError, TypeError, KeyError):
            continue
        if state:
            states.append(state)
    return sorted(states, key=lambda state: state.get("created_at", ""), reverse=True)


def _launch(root: Path, state: dict) -> dict:
    """Caller holds the per-library control lock through worker launch."""

    batch_id = state["batch_id"]
    log_path = root / ".ai-review" / f"{batch_id}.log"
    if log_path.is_symlink():
        raise ValueError("审核任务日志不能是符号链接")
    state.update(status="queued", pid=os.getpid(), process_start_id=_identity(os.getpid()), run_token=uuid.uuid4().hex,
                 stop_requested=False, current_folder=None, error=None, finished_at=None, pause_reason=None,
                 consecutive_failures=0)
    state.setdefault("runs", []).append({"run_token": state["run_token"], "started_at": _now(),
        "provider": state["provider"], "llm_options": dict(state["llm_options"]),
        "max_attempts": state.get("max_attempts", 3), "consecutive_failure_limit": state.get("consecutive_failure_limit", 5)})
    _write(root, state)
    args = [str(root), batch_id, state["run_token"]]
    command = ([sys.executable, "--ai-review-worker", *args] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "bili_recipe_notes.ai_review_worker", *args])
    env = os.environ.copy()
    env[DATA_DIR_ENV] = str(data_root())
    package_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_root, env.get("PYTHONPATH")]))
    try:
        with log_path.open("ab") as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       cwd=package_root, env=env, start_new_session=True)
        previous = _PROCESSES.get(batch_id)
        if previous is not None:
            _RETIRED_PROCESSES.append(previous)
        _PROCESSES[batch_id] = process
        state.update(pid=process.pid, process_start_id=_identity(process.pid))
        _write(root, state)
    except Exception as exc:
        state.update(status="interrupted", error=str(exc))
        _write(root, state)
        raise
    return _decorate(state)


def start_ai_review_batch(
    out_dir: str | Path,
    *,
    provider: str = "codex",
    limit: int | None = None,
    only_pending: bool = True,
    retry_failed: bool = False,
    folders: list[str | Path] | None = None,
    max_attempts: int = 3,
    consecutive_failure_limit: int = 5,
    retry_base_seconds: float = 1.0,
    **llm_options: Any,
) -> dict:
    root = _root(out_dir)
    if provider not in SUPPORTED_LLM_PROVIDERS - {"none"}:
        raise ValueError("请先选择可用的 AI 服务")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("审核数量限制必须为正整数")
    if set(llm_options) - _OPTIONS:
        raise ValueError("未知的 AI 模型配置")
    if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
        raise ValueError("每条自动尝试次数必须在 1 到 5 之间")
    if type(consecutive_failure_limit) is not int or consecutive_failure_limit < 1:
        raise ValueError("连续失败暂停阈值必须为正整数")
    if type(retry_base_seconds) not in (int, float) or not 0 <= retry_base_seconds <= 30:
        raise ValueError("重试等待必须在 0 到 30 秒之间")
    with file_lock(root / ".ai-review" / "control", timeout=15):
        batches = list_ai_review_batches(root)
        active = next((state for state in batches if state["active"]), None)
        if active:
            return active
        candidates = ([_folder(root, item) for item in folders] if folders is not None else
                      [_folder(root, path) for path in sorted(iter_output_folders(root))
                       if (path / "recipe.json").is_file()])
        candidates = list(dict.fromkeys(candidates))
        if retry_failed:
            latest: dict[str, str] = {}
            for batch in batches:
                for item in batch["items"]:
                    latest.setdefault(item["folder"], item["status"])
            candidates = [path for path in candidates if latest.get(path.relative_to(root).as_posix()) == "failed"]
            only_pending = False
        if only_pending:
            candidates = [path for path in candidates if load_ai_review(path) is None]
        if limit is not None:
            candidates = candidates[:limit]
        state = {
            "schema_version": 1, "batch_id": "audit-" + uuid.uuid4().hex[:16], "out_dir": str(root),
            "created_at": _now(), "status": "completed", "provider": provider,
            "llm_options": llm_options, "only_pending": only_pending, "stop_requested": False,
            "max_attempts": max_attempts, "consecutive_failure_limit": consecutive_failure_limit,
            "retry_base_seconds": retry_base_seconds, "consecutive_failures": 0,
            "pid": None, "process_start_id": None, "current_folder": None,
            "items": [{"folder": path.relative_to(root).as_posix(), "status": "pending", "error": None} for path in candidates],
        }
        if not candidates:
            state["finished_at"] = _now()
            return _decorate(_write(root, state))
        return _launch(root, state)


def request_stop_ai_review_batch(out_dir: str | Path, batch_id: str) -> dict:
    root = _root(out_dir)
    with file_lock(root / ".ai-review" / "control", timeout=15):
        state = get_ai_review_batch(root, batch_id)
        if state is None:
            raise ValueError("AI 审核任务不存在")
        def stop(current: dict) -> None:
            if current["status"] in _ACTIVE:
                current["stop_requested"] = True
        return _decorate(_update(root, batch_id, stop))


def resume_ai_review_batch(out_dir: str | Path, batch_id: str, *, retry_failed: bool = False,
                          provider: str | None = None, **llm_options: Any) -> dict:
    root = _root(out_dir)
    if provider is not None and provider not in SUPPORTED_LLM_PROVIDERS - {"none"}:
        raise ValueError("请先选择可用的 AI 服务")
    if set(llm_options) - _OPTIONS:
        raise ValueError("未知的 AI 模型配置")
    with file_lock(root / ".ai-review" / "control", timeout=15):
        batches = list_ai_review_batches(root)
        active = next((state for state in batches if state["active"]), None)
        if active:
            if active["batch_id"] == batch_id:
                return active
            raise RuntimeError("该目录已有 AI 审核任务运行")
        state = get_ai_review_batch(root, batch_id)
        if state is None:
            raise ValueError("AI 审核任务不存在")
        if provider is not None:
            state["provider"] = provider
        state["llm_options"] = {**state.get("llm_options", {}), **llm_options}
        for item in state["items"]:
            if item["status"] == "running" or (retry_failed and item["status"] == "failed"):
                for attempt in item.get("attempts", []):
                    if attempt.get("status") == "running":
                        attempt.update(status="interrupted", finished_at=_now(), error="后台进程中断")
                item.update(status="pending", error=None)
        if not any(item["status"] == "pending" for item in state["items"]):
            return state
        return _launch(root, state)


def _error_kind(exc: Exception) -> str:
    message = str(exc).casefold()
    if any(fragment in message for fragment in (
        "api_key is not set", "api key is not set", "invalid api key", "incorrect api key", "local command is empty",
        "no such file or directory", "command not found", "unauthorized", "http error 401", "http error 403",
        "model not found", "insufficient_quota", "unsupported llm provider",
    )) and not isinstance(exc, FileNotFoundError):
        return "configuration"
    if isinstance(exc, (TimeoutError, ConnectionError, subprocess.TimeoutExpired)) or any(fragment in message for fragment in (
        "timed out", "timeout", "connection reset", "connection refused", "temporarily unavailable",
        "temporary failure", "name resolution", "network is unreachable", "remote end closed", "econnreset",
        "rate limit", "429", "http error 500", "http error 502", "http error 503", "http error 504", "remote disconnected",
    )):
        return "transient"
    return "validation" if isinstance(exc, (ValueError, FileNotFoundError)) else "permanent"


def run_ai_review_batch(out_dir: str | Path, batch_id: str, run_token: str) -> dict:
    """Worker entry point; errors affect one item and are saved before continuing."""

    root = _root(out_dir)
    # Parent persists the PID before releasing this lock.
    with file_lock(root / ".ai-review" / "control", timeout=30):
        state = _read(root, batch_id)
        if state.get("run_token") != run_token:
            raise RuntimeError("审核 worker 标识已过期")
        def begin(current: dict) -> None:
            current.update(status="running", pid=os.getpid(), process_start_id=_identity(os.getpid()))
        _update(root, batch_id, begin)

    while True:
        state = _read(root, batch_id)
        if state.get("run_token") != run_token:
            return state
        pending = next((index for index, item in enumerate(state["items"]) if item["status"] == "pending"), None)
        if pending is None or state.get("stop_requested"):
            def finish(current: dict) -> None:
                current.update(status=("paused" if pending is not None else
                                       "completed_with_errors" if current["failed"] else "completed"),
                               current_folder=None, finished_at=_now())
            return _update(root, batch_id, finish)
        name = state["items"][pending]["folder"]
        def starting(current: dict) -> None:
            current["items"][pending].update(status="running", started_at=_now(), error=None)
            current["current_folder"] = name
        _update(root, batch_id, starting)
        try:
            folder = _folder(root, name)
            if state["only_pending"] and load_ai_review(folder) is not None:
                result = {"status": "skipped", "error": None, "error_kind": None}
            else:
                for retry_index in range(state.get("max_attempts", 3)):
                    def attempt_start(current: dict) -> None:
                        attempts = current["items"][pending].setdefault("attempts", [])
                        attempts.append({"attempt": len(attempts) + 1, "started_at": _now(), "status": "running",
                                         "provider": state["provider"], "llm_options": dict(state["llm_options"]),
                                         "run_token": run_token})
                    _update(root, batch_id, attempt_start)
                    try:
                        review = review_recipe(folder, provider=state["provider"], only_pending=state["only_pending"], **state["llm_options"])
                    except Exception as exc:
                        kind = _error_kind(exc)
                        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:2000], "error_kind": kind}
                        def attempt_failed(current: dict) -> None:
                            current["items"][pending]["attempts"][-1].update(result, finished_at=_now())
                        _update(root, batch_id, attempt_failed)
                        if kind != "transient" or retry_index + 1 >= state.get("max_attempts", 3):
                            break
                        delay = min(30.0, state.get("retry_base_seconds", 1.0) * 2 ** retry_index)
                        def retrying(current: dict) -> None:
                            current["items"][pending]["attempts"][-1]["retry_delay_seconds"] = delay
                        _update(root, batch_id, retrying)
                        time.sleep(delay)
                    else:
                        result = {"status": "completed", "error": None, "error_kind": None,
                                  "classification": review["classification"], "review_status": review["status"],
                                  "visibility": review["visibility"]}
                        def attempt_done(current: dict) -> None:
                            current["items"][pending]["attempts"][-1].update(status="completed", finished_at=_now(), error=None)
                        _update(root, batch_id, attempt_done)
                        break
        except Exception as exc:
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:2000], "error_kind": _error_kind(exc)}
        def finished(current: dict) -> None:
            if current.get("run_token") != run_token:
                raise RuntimeError("审核 worker 标识已过期")
            current["items"][pending].update(result, finished_at=_now())
            current["current_folder"] = None
            current["consecutive_failures"] = current.get("consecutive_failures", 0) + 1 if result["status"] == "failed" else 0
            if (result.get("error_kind") == "configuration" or
                    current["consecutive_failures"] >= current.get("consecutive_failure_limit", 5)):
                current.update(status="paused", pause_reason=("configuration" if result.get("error_kind") == "configuration" else "consecutive_failures"),
                               error=("模型配置错误，请修正配置后重试" if result.get("error_kind") == "configuration" else "连续失败已达到阈值，自动暂停以避免重复消耗"),
                               finished_at=_now())
        updated = _update(root, batch_id, finished)
        print(f"{_now()} {name}: {result['status']}", flush=True)
        if updated["status"] == "paused":
            return updated
