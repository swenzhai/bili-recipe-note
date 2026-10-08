"""Compact duplicate generation queues around their current video results.

Only queue bookkeeping is changed. Recipe outputs are evidence, never targets.
"""
from __future__ import annotations

import copy
import os
import re
from dataclasses import asdict, fields
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import batch_runner as runner
from .batch_queue import BatchQueueItem, BatchQueueState, batch_path, batches_dir, load_batch_state, now_utc
from .data_paths import resolve_stored_path
from .history import is_complete_output, is_raw_output
from .pipeline import BatchJobOptions, RecipeJobOptions, _recipe_output_meets_requirements
from .storage import StorageError, atomic_write_json, atomic_write_text, file_lock, read_json

_OPTION_KEYS = {field.name for field in fields(BatchJobOptions)}
_RUNNING_ITEMS = {"running", "raw_running", "recipe_running"}
_CLASSIFIED = {"non_recipe", "technique"}


def video_key(value: str) -> str:
    """Drop sharing parameters without conflating parts or resolving short URLs."""
    raw = str(value).strip()
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", raw):
        raw = "https://www.bilibili.com/video/" + raw
    parsed = urlparse(raw)
    if parsed.hostname in {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
        match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]+)/?", parsed.path)
        if match:
            page = (parse_qs(parsed.query).get("p") or ["1"])[0]
            if page.isdigit() and int(page) > 0:
                return "https://www.bilibili.com/video/" + match[1] + (f"?p={int(page)}" if int(page) > 1 else "")
    if parsed.hostname == "b23.tv" and re.fullmatch(r"/[A-Za-z0-9]+/?", parsed.path):
        return "https://b23.tv/" + parsed.path.strip("/")
    # Unknown legacy addresses are compared literally; do not guess identity.
    return raw


def effective_options(state: BatchQueueState, project_root: Path, out_dir=None) -> dict:
    project_root = Path(project_root).resolve()
    options = copy.deepcopy(state.options)
    if not options.get("out") and out_dir is not None:
        options["out"] = str(Path(out_dir).expanduser().resolve())
    path = runner.background_request_path(state.batch_id, project_root)
    if path.is_symlink():
        raise ValueError("任务请求不能是符号链接")
    if path.exists():
        request = read_json(path, expected_type=dict)
        previous = request.get("options")
        if not isinstance(previous, dict):
            raise ValueError("任务请求缺少有效参数")
        if previous.get("batch_id") != state.batch_id:
            raise ValueError("任务请求 ID 不匹配")
        if resolve_stored_path(previous.get("out") or options.get("out"), project_root) != resolve_stored_path(options.get("out"), project_root):
            raise ValueError("任务请求的菜谱目录不匹配")
        options.update({key: value for key, value in previous.items() if key in _OPTION_KEYS})
    options.pop("urls", None)
    options.pop("cookies", None)
    return options


def _group_key(state: BatchQueueState, options: dict, project_root: Path, out: Path):
    if not options.get("out") or resolve_stored_path(options["out"], project_root) != out:
        return None
    if options.get("skip_existing", True) is not True:
        return None
    stage = options.get("target_stage", "recipe")
    if stage not in {"raw", "recipe"} or not state.items:
        return None
    # Model changes are normal retries. Strict output requirements remain distinct.
    return (str(out), stage, bool(options.get("require_llm")), bool(options.get("require_screenshot")),
            tuple(sorted({video_key(item.url) for item in state.items})))


def batch_group_key(state: BatchQueueState, project_root, out_dir):
    root, out = Path(project_root).resolve(), Path(out_dir).expanduser().resolve()
    return _group_key(state, effective_options(state, root, out), root, out)


def group_key(urls, options: dict, out_dir):
    """Build the same key for a new admin request without creating a queue."""
    out = Path(out_dir).expanduser().resolve()
    if Path(options.get("out") or out).expanduser().resolve() != out or options.get("skip_existing", True) is not True:
        return None
    stage = options.get("target_stage", "recipe")
    if stage not in {"raw", "recipe"} or not urls:
        return None
    return (str(out), stage, bool(options.get("require_llm")), bool(options.get("require_screenshot")),
            tuple(sorted({video_key(url) for url in urls})))


def _runtime(state: BatchQueueState, root: Path):
    path = runner.background_status_path(state.batch_id, root)
    if path.is_symlink():
        raise ValueError("任务状态不能是符号链接")
    if path.exists():
        raw = read_json(path, expected_type=dict)
        if raw.get("batch_id") != state.batch_id or not raw.get("status") or not raw.get("started_at"):
            raise ValueError("后台任务状态损坏")
    return runner._get_background_batch_status(state.batch_id, root)


def _is_active(state: BatchQueueState, runtime) -> bool:
    record = runner._RUNS.get(state.batch_id)
    if record and runner._worker_is_alive(record.worker):
        return True
    if runtime and runtime.status in {"running", "paused"}:
        return True
    return runtime is None and any(item.status in _RUNNING_ITEMS or any(stage.status == "running" for stage in item.stages.values()) for item in state.items)


def _verified_folder(item: BatchQueueItem, stage: str, options: dict, out: Path) -> Path | None:
    if not item.output_folder:
        return None
    folder = Path(item.output_folder).resolve()
    if not folder.is_relative_to(out) or folder == out:
        return None
    if not folder.is_dir():
        return None
    # Fail the group closed when evidence exists but is unreadable/corrupt.
    evidence = {}
    for name in ("source.json", "job.json", "recipe.json", "transcript.json"):
        path = folder / name
        if path.is_symlink():
            raise ValueError("原始产物不能是符号链接")
        if path.exists():
            evidence[name] = read_json(path, expected_type=list if name == "transcript.json" else dict)
    urls = [raw.get("source_url") for raw in evidence.values() if isinstance(raw, dict) and raw.get("source_url")]
    if not urls or any(video_key(url) != video_key(item.url) for url in urls):
        return None
    if stage == "raw":
        return folder if is_raw_output(folder) else None
    recipe_options = RecipeJobOptions(url=item.url, require_llm=bool(options.get("require_llm")),
                                     require_screenshot=bool(options.get("require_screenshot")))
    return folder if is_complete_output(folder) and _recipe_output_meets_requirements(folder, recipe_options) else None


def _resolved(item: BatchQueueItem, stage: str) -> bool:
    return item.status in _CLASSIFIED or item.stages[stage].status == "done" or item.status in {"done", "skipped"}


def item_has_result(item: BatchQueueItem, options: dict, out_dir) -> bool:
    """Check current on-disk evidence before reusing a completed queue item."""
    if item.status in _CLASSIFIED:
        return True
    try:
        return _verified_folder(item, options.get("target_stage", "recipe"), options,
                                Path(out_dir).expanduser().resolve()) is not None
    except (StorageError, OSError, ValueError, TypeError):
        return False


def _merge_items(states: list[BatchQueueState], options: dict, out: Path):
    stage = options.get("target_stage", "recipe")
    # Main queue ordering is stable even when imported URLs have other spellings.
    keys = list(dict.fromkeys(video_key(item.url) for item in states[0].items))
    merged, recovered = [], 0
    for key in keys:
        candidates = [(item, state.updated_at) for state in states for item in state.items if video_key(item.url) == key]
        candidates.sort(key=lambda row: row[0].finished_at or row[1], reverse=True)
        classified = next((item for item, _ in candidates if item.status in _CLASSIFIED), None)
        if classified:
            merged.append(copy.deepcopy(classified))
            continue
        successful = next(((item, folder) for item, _ in candidates if (folder := _verified_folder(item, stage, options, out))), None)
        if successful:
            item, folder = successful
            value = copy.deepcopy(item)
            value.output_folder = str(folder)
            value.note_path = str(folder / "note.md") if stage == "recipe" else value.note_path
            value.status = "done" if stage == "recipe" else "raw_ready"
            value.error = None
            for name in (("raw", "recipe") if stage == "recipe" else ("raw",)):
                value.stages[name].status = "done"
                value.stages[name].error = None
            # An old error on an irrelevant later stage should not survive a
            # raw-only task summary; its original output remains untouched.
            for state in value.stages.values():
                state.error = None
            original = next(item for item in states[0].items if video_key(item.url) == key)
            recovered += int(not _resolved(original, stage))
            merged.append(value)
            continue
        # Do not claim a stale done marker with missing artifacts is success.
        if any(_resolved(item, stage) for item, _ in candidates):
            raise ValueError("完成标记没有可核验的原始产物，保留原任务")
        failures = [item for item, _ in candidates if item.status == "failed" or item.stages[stage].status == "failed"]
        value = copy.deepcopy((failures or [candidates[0][0]])[0])
        if failures:
            value.status = "failed"
        merged.append(value)
    return merged, recovered


def _bookkeeping_paths(batch_id: str, root: Path) -> list[Path]:
    paths = [batch_path(batch_id, root), runner.batch_log_path(batch_id, root),
             runner.background_status_path(batch_id, root), runner.background_request_path(batch_id, root),
             runner.background_result_path(batch_id, root)]
    return paths + [path.with_name(path.name + ".bak") for path in paths]


def _preflight(paths: list[Path], root: Path) -> None:
    allowed = batches_dir(root).resolve()
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(allowed):
            raise ValueError("任务文件路径不安全")
        if path.exists():
            if not path.is_file():
                raise ValueError("任务状态不是普通文件")
            # Detect existing inaccessible records before changing the group.
            with path.open("rb") as handle:
                handle.read(1)
            if not os.access(path.parent, os.W_OK):
                raise PermissionError(path.parent)


def consolidate_batches(project_root, out_dir, *, batch_ids=None) -> dict[str, int]:
    """Merge inactive duplicate queues; return counts, never delete recipe data.

    ``batch_ids`` restricts processing to full groups containing one of those IDs.
    Re-running after success is a no-op. Unreadable metadata fails closed.
    """
    root, out = Path(project_root).resolve(), Path(out_dir).expanduser().resolve()
    result = dict(groups=0, removed_tasks=0, recovered_items=0, removed_files=0, skipped_groups=0)
    selected = set(batch_ids) if batch_ids is not None else None
    groups = {}
    try:
        for path in sorted(batches_dir(root).glob("*.json")):
            if path.is_symlink():
                raise ValueError("任务记录不能是符号链接")
            state = load_batch_state(path.stem, root)
            options = effective_options(state, root, out)
            key = _group_key(state, options, root, out)
            if key is not None:
                groups.setdefault(key, []).append(state.batch_id)
    except (StorageError, OSError, ValueError, TypeError):
        result["skipped_groups"] += 1
        return result

    for key, ids in groups.items():
        if len(ids) < 2 or selected is not None and not selected.intersection(ids):
            continue
        try:
            with runner._batch_group_control_lock(ids, root):
                states = [load_batch_state(batch_id, root) for batch_id in ids]
                options_by_id = {state.batch_id: effective_options(state, root, out) for state in states}
                if any(_group_key(state, options_by_id[state.batch_id], root, out) != key for state in states):
                    raise ValueError("任务参数刚刚变更")
                runtimes = {state.batch_id: _runtime(state, root) for state in states}
                if any(_is_active(state, runtimes[state.batch_id]) for state in states):
                    raise ValueError("任务仍在运行或暂停")
                if not any(item.status in {"done", "skipped", "raw_ready", "failed", *_CLASSIFIED}
                           or any(stage.status in {"done", "failed"} for stage in item.stages.values())
                           for state in states for item in state.items):
                    raise ValueError("尚未开始的队列无需整理")
                states.sort(key=lambda state: (state.updated_at, state.created_at, state.batch_id), reverse=True)
                main = copy.deepcopy(states[0])
                main.options = options_by_id[main.batch_id]
                main.options["batch_id"] = main.batch_id
                main.items, recovered = _merge_items(states, main.options, out)
                stage = main.options.get("target_stage", "recipe")
                failures = [item for item in main.items if not _resolved(item, stage) and
                            (item.status == "failed" or any(value.status == "failed" for value in item.stages.values()))]
                unresolved = [item for item in main.items if not _resolved(item, stage)]
                status = "done_with_errors" if failures else "pending" if unresolved else "done"
                stamp = now_utc()
                main.updated_at = stamp
                runtime = runner.BackgroundBatchStatus(main.batch_id, status, main.created_at, stamp if status != "pending" else None,
                                                       log_path=runner.batch_log_path(main.batch_id, root))
                all_paths = [path for batch_id in ids for path in _bookkeeping_paths(batch_id, root)]
                _preflight(all_paths, root)
                # Persist every surviving URL and effective option before any
                # redundant record is removed. No historical .bak is created.
                with file_lock(batch_path(main.batch_id, root)):
                    atomic_write_json(batch_path(main.batch_id, root), asdict(main), backup=False)
                atomic_write_json(runner.background_status_path(main.batch_id, root),
                                  {**asdict(runtime), "log_path": str(runtime.log_path)}, backup=False)
                atomic_write_json(runner.background_result_path(main.batch_id, root),
                                  {"items": [{key: asdict(item)[key] for key in
                                              ("url", "status", "output_folder", "note_path", "error")}
                                             for item in main.items]}, backup=False)
                keep = {batch_path(main.batch_id, root), runner.background_status_path(main.batch_id, root),
                        runner.background_result_path(main.batch_id, root)}
                if failures:
                    log = "\n".join(f"{item.url}: {item.error or item.stages[stage].error or '处理失败，等待重试'}" for item in failures) + "\n"
                    atomic_write_text(runner.batch_log_path(main.batch_id, root), log, backup=False)
                    keep.add(runner.batch_log_path(main.batch_id, root))
                removed_files = 0
                # Keep redundant queue JSON until its auxiliary history is
                # gone so an interrupted cleanup can discover it again.
                record_paths = {batch_path(batch_id, root) for batch_id in ids}
                for path in sorted(all_paths, key=lambda path: path in record_paths):
                    if path not in keep and path.exists():
                        path.unlink()
                        removed_files += 1
                for batch_id in ids:
                    runner._RUNS.pop(batch_id, None)
                result["groups"] += 1
                result["removed_tasks"] += len(ids) - 1
                result["recovered_items"] += recovered
                result["removed_files"] += removed_files
        except (StorageError, OSError, ValueError, TypeError):
            result["skipped_groups"] += 1
    return result
