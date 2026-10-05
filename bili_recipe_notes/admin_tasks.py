"""UI-independent adapters for durable acquisition and recipe background jobs."""
from __future__ import annotations

from dataclasses import asdict, fields
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlparse
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import ai_review_runner as reviews
from . import batch_runner as generation
from . import dish_info_runner as introductions
from . import frame_selection_runner as frames
from .batch_queue import create_batch_state, list_batch_states, load_batch_state, save_batch_state, selectable_items
from .data_paths import creators_dir, resolve_stored_path, state_dir
from .mobile_sync import normalize_source_url
from .pipeline import BatchJobOptions
from .storage import read_json

KINDS = {"generation": "采集与生成", "review": "AI 菜谱审核", "frames": "AI 菜品选图", "dish-info": "菜品介绍", "creators": "UP 主链接更新", "settings": "数据与知识库"}
LLM_KEYS = {"provider", "openai_model", "local_llm_command", "codex_model", "codex_profile", "cli_extra_instructions"}
GEN_KEYS = {f.name for f in fields(BatchJobOptions)} - {"urls", "out", "cookies", "batch_id", "resume_mode", "source_database_path"}


class StartTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["generation", "review", "frames", "dish-info"]
    recipe_ids: list[str] | None = Field(default=None, max_length=10000)
    limit: int = Field(default=10, ge=0, le=10000)
    only_pending: StrictBool = True
    options: dict = Field(default_factory=dict)
    urls: list[str] = Field(default_factory=list, max_length=10000)
    target_stage: Literal["raw", "recipe"] = "recipe"
    create_only: StrictBool = False
    creator_name: str | None = Field(default=None, max_length=200)


class TaskAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_current_config: StrictBool = False
    options: dict = Field(default_factory=dict)
    target_stage: Literal["raw", "recipe"] | None = None


class RefreshCreator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2000)


def canonical_video_urls(values: list[str]) -> list[str]:
    """Accept Bilibili video addresses only; retain multi-part page identity."""
    result = []
    for value in values:
        for line in value.splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            if re.fullmatch(r"BV[0-9A-Za-z]{10}", raw):
                raw = "https://www.bilibili.com/video/" + raw
            parsed = urlparse(raw)
            if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.port:
                raise ValueError(f"无效的视频链接：{raw[:120]}")
            host = parsed.hostname
            if host in {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
                match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]+)/?", parsed.path)
                if not match:
                    raise ValueError("请使用 Bilibili 视频链接，UP 主主页请在清单更新中填写")
                page = (parse_qs(parsed.query).get("p") or [""])[0]
                if page and (not page.isdigit() or int(page) < 1):
                    raise ValueError("视频分 P 参数无效")
                url = "https://www.bilibili.com/video/" + match[1]
                if page and int(page) > 1:
                    url += "?" + urlencode({"p": int(page)})
            elif host == "b23.tv" and re.fullmatch(r"/[A-Za-z0-9]+/?", parsed.path):
                url = "https://b23.tv/" + parsed.path.strip("/")
            else:
                raise ValueError("仅支持 Bilibili 视频或 b23.tv 分享链接")
            if url not in result:
                result.append(url)
    if not result:
        raise ValueError("请至少填写一个视频链接")
    return result


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
    except (TypeError, ValueError):
        return None


def _duration(state: dict) -> float | None:
    runs = state.get("runs") or []
    if runs:
        now = datetime.now(timezone.utc)
        return sum(max(0, ((_stamp(run.get("finished_at")) or now) - start).total_seconds())
                   for run in runs if (start := _stamp(run.get("started_at"))))
    start = _stamp(state.get("started_at"))
    if not start:
        return None
    end = _stamp(state.get("finished_at")) or _stamp(state.get("paused_at")) or datetime.now(timezone.utc)
    return max(0, (end - start).total_seconds() - float(state.get("paused_seconds") or 0))


def _tail(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        return ""
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - 20000))
        return handle.read().decode("utf-8", errors="replace")


def _public_options(options: dict) -> dict:
    return {key: value for key, value in options.items()
            if key not in {"cookies", "run_token", "source_database_path"}}


def summarize(kind: str, state: dict, *, detail: bool = False, log: str = "") -> dict:
    items = state.get("items") or []
    completed = sum(item.get("status") in {"completed", "not_found"} for item in items)
    failed = sum(item.get("status") == "failed" for item in items)
    skipped = sum(item.get("status") == "skipped" for item in items)
    total, processed = len(items), completed + failed + skipped
    active = bool(state.get("active"))
    status = str(state.get("status") or "pending")
    if active and state.get("stop_requested"):
        status = "pausing"
    elapsed = _duration(state)
    options = state.get("options") or {"provider": state.get("provider"), **state.get("llm_options", {})}
    result = {"id": state["batch_id"], "kind": kind, "title": KINDS[kind], "status": status,
              "total": total, "processed": processed, "completed": completed, "failed": failed,
              "skipped": skipped, "not_found": sum(item.get("status") == "not_found" for item in items),
              "progress": processed / total if total else 1.0,
              "current": state.get("current_folder") or state.get("current"),
              "current_progress": state.get("current_progress"), "elapsed_seconds": elapsed,
              "eta_seconds": elapsed / processed * (total - processed) if active and elapsed and processed else None,
              "updated_at": state.get("updated_at") or state.get("created_at"),
              "can_pause": active and kind != "creators" and status != "pausing",
              "can_resume": (not active and any(i.get("status") in {"pending", "running"} for i in items)) or status == "paused",
              "can_retry": not active and failed > 0,
              "error": state.get("error"), "pause_reason": state.get("pause_reason"),
              "already_running": bool(state.get("already_running"))}
    if detail:
        result.update(items=items, log=log, options=_public_options(options))
        if kind == "dish-info":
            result["options"]["web_search"] = state.get("web_search", False)
    return result


def _saved_generation_options(ctx, state):
    options = dict(state.options)
    request = generation.background_request_path(state.batch_id, ctx.store.project_root)
    if request.is_file() and not request.is_symlink():
        raw = read_json(request, expected_type=dict).get("options", {})
        if raw.get("batch_id") == state.batch_id and resolve_stored_path(raw.get("out") or ctx.config().out_dir, ctx.store.project_root) == Path(ctx.config().out_dir).resolve():
            options.update({key: value for key, value in raw.items() if key in GEN_KEYS})
    return options


def _generation_state(ctx, state, *, detail=False) -> dict:
    runtime = generation.get_background_batch_status(state.batch_id, ctx.store.project_root)
    options = _saved_generation_options(ctx, state)
    stage = options.get("target_stage", "recipe")
    items = []
    for item in state.items:
        value = asdict(item)
        if item.status in {"skipped", "non_recipe", "technique"}:
            value["status"] = "skipped"
        elif item.stages[stage].status == "done" or item.status == "done":
            value["status"] = "completed"
        elif item.status == "failed" or item.stages[stage].status == "failed":
            value["status"] = "failed"
        else:
            value["status"] = "running" if item.status in {"running", "raw_running", "recipe_running"} else "pending"
        items.append(value)
    status = runtime.status if runtime else "pending"
    status = {"done": "completed", "done_with_errors": "completed_with_errors"}.get(status, status)
    if not runtime and items and all(i["status"] in {"completed", "failed", "skipped"} for i in items):
        status = "completed_with_errors" if any(i["status"] == "failed" for i in items) else "completed"
    data = {**(asdict(runtime) if runtime else {}), **asdict(state), "options": options, "status": status,
            "active": bool(runtime and runtime.status == "running"), "items": items,
            "current": next((i["url"] for i in items if i["status"] == "running"), None)}
    result = summarize("generation", data, detail=detail,
                       log=generation.read_batch_log(state.batch_id, project_root=ctx.store.project_root) if detail else "")
    # SIGSTOP/SIGCONT controls are supported only on POSIX. Other platforms
    # still retain resume/retry for interrupted or finished jobs.
    import os
    result["can_pause"] = result["can_pause"] and os.name != "nt"
    if runtime and runtime.status == "paused":
        result["can_retry"] = False
        result["can_resume"] = os.name != "nt"
    result["target_stage"] = stage
    result["can_generate_recipe"] = stage == "raw" and not result["can_pause"] and status != "paused"
    return result


def _generation_batches(ctx):
    return [state for state in list_batch_states(ctx.store.project_root)
            if resolve_stored_path(state.options.get("out") or ctx.config().out_dir, ctx.store.project_root) == Path(ctx.config().out_dir).resolve()]


def _ai_states(kind, out_dir):
    return {"review": reviews.list_ai_review_batches,
            "frames": frames.list_frame_selection_batches,
            "dish-info": introductions.list_dish_info_batches}[kind](out_dir)


def _settings_summary(state, detail=False):
    names = {"knowledge_extract": "提取烹饪知识", "knowledge_document": "导入知识文档", "deployment_export": "导出数据包",
             "handoff_export": "导出工作交接", "handoff_import": "导入工作交接", "menu_image": "生成菜单图片"}
    items = [{**item, "status": {"done": "completed"}.get(item.get("status"), item.get("status"))}
             for item in state.get("items", [])]
    total = state.get("total") or 1
    if not items:
        items = [{"id": state["id"], "status": "completed" if state["status"] == "completed" else "failed"
                  if state["status"] in {"failed", "interrupted"} else "pending", "error": state.get("error")}]
    value = summarize("settings", {**state, "batch_id": state["id"], "items": items,
                                     "active": state["status"] in {"running", "queued"}}, detail=detail)
    names["recipe_archive"] = "批量归档菜谱"
    if str(state.get("kind", "")).startswith("recipe_tool_"):
        from .admin_recipe_tools import LABELS
        names[state["kind"]] = LABELS.get(state["kind"][12:], "菜谱修订工具")
    value.update(title=names.get(state.get("kind"), "数据与知识库"), total=total,
                 processed=state.get("processed", value["processed"]), can_pause=False,
                 can_resume=state["status"] in {"interrupted", "failed", "completed_with_errors"},
                 can_retry=state["status"] in {"interrupted", "failed", "completed_with_errors"})
    value["progress"] = min(1, value["processed"] / total)
    if detail:
        value["result"] = state.get("result")
    return value


def list_tasks(ctx) -> list[dict]:
    from .admin_creator_worker import list_creator_tasks
    from .admin_settings import list_settings_operations
    result = [_generation_state(ctx, state) for state in _generation_batches(ctx)]
    for kind in ("review", "frames", "dish-info"):
        result.extend(summarize(kind, state) for state in _ai_states(kind, ctx.config().out_dir))
    result.extend(summarize("creators", state) for state in list_creator_tasks(ctx.store.project_root))
    result.extend(_settings_summary(state) for state in list_settings_operations(ctx))
    return sorted(result, key=lambda item: item.get("updated_at") or "", reverse=True)


def _find(ctx, kind: str, task_id: str) -> dict:
    if kind not in KINDS:
        raise HTTPException(404, "任务类型不存在")
    if kind == "settings":
        from .admin_settings import get_settings_operation
        return _settings_summary(get_settings_operation(ctx, task_id), detail=True)
    if kind == "generation":
        state = next((s for s in _generation_batches(ctx) if s.batch_id == task_id), None)
        if state:
            return _generation_state(ctx, state, detail=True)
    else:
        root = Path(ctx.config().out_dir)
        if kind == "creators":
            from .admin_creator_worker import list_creator_tasks, creator_task_path
            states = list_creator_tasks(ctx.store.project_root)
            log_path = creator_task_path(ctx.store.project_root, task_id).with_suffix(".log")
        else:
            states = _ai_states(kind, root)
            folder = {"review": ".ai-review", "frames": ".frame-selection/tasks", "dish-info": ".dish-info"}[kind]
            log_path = root / folder / (task_id + ".log")
        state = next((s for s in states if s["batch_id"] == task_id), None)
        if state:
            return summarize(kind, state, detail=True, log=_tail(log_path))
    raise HTTPException(404, "任务不存在或不属于当前菜谱库")


def _llm_options(config):
    return {"provider": config.llm_provider, "openai_model": config.openai_model,
            "codex_model": config.codex_model, "codex_profile": config.codex_profile,
            "local_llm_command": config.local_llm_command, "cli_extra_instructions": config.llm_cli_extra_instructions}


def _generation_options(ctx, overrides=None, previous=None):
    config = ctx.config()
    config_values = asdict(config)
    defaults = {key: value for key, value in config_values.items() if key in GEN_KEYS}
    defaults.update(no_screenshot=not config.enable_screenshot, no_llm_summary=not config.enable_llm_summary or config.llm_provider == "none")
    values = {**defaults, **{key: value for key, value in (previous or {}).items() if key in GEN_KEYS}, **(overrides or {})}
    if set(values) - GEN_KEYS:
        raise ValueError("未知生成参数：" + ", ".join(sorted(set(values) - GEN_KEYS)))
    for key in {"no_screenshot", "no_llm_summary", "require_llm", "require_screenshot", "keep_media", "enable_recipe_review", "skip_existing"}:
        if key in values and type(values[key]) is not bool:
            raise ValueError(f"{key} 必须为布尔值")
    for key, low, high in (("max_recipe_steps", 4, 12), ("max_step_images", 1, 4)):
        if key in values and (type(values[key]) is not int or not low <= values[key] <= high):
            raise ValueError(f"{key} 必须在 {low}–{high} 之间")
    if values.get("llm_provider") not in {"none", "codex", "openai", "local", "opencode"}:
        raise ValueError("请选择可用的模型服务")
    if values.get("target_stage", "recipe") not in {"raw", "recipe"}:
        raise ValueError("生成阶段无效")
    return BatchJobOptions(urls=[], out=config.out_dir, cookies=config.cookies,
                           source_database_path=str(ctx.store.database_path), **values)


@contextmanager
def _generation_control(ctx):
    # Serialize the overlap check AND worker launch across HTTP workers. A
    # native lock is released on process death and never ages out mid-launch.
    from .admin_settings import _try_operation_lock
    path = state_dir(ctx.store.project_root) / "batches" / "runtime" / "admin-generation-starts.json"
    handle = _try_operation_lock(path)
    if handle is None:
        raise HTTPException(409, "另一个请求正在安排生成任务，请稍后重试")
    try:
        yield
    finally:
        handle.close()


def _video_key(value):
    try:
        return canonical_video_urls([value])[0]
    except ValueError:
        # Legacy batches can contain addresses accepted by older versions;
        # strip tracking queries there without needing any network request.
        return normalize_source_url(value)


def _check_generation_overlaps(ctx, urls, *, exclude=None):
    requested = {_video_key(url) for url in urls}
    conflicts = []
    for state in _generation_batches(ctx):
        if state.batch_id == exclude:
            continue
        runtime = generation.get_background_batch_status(state.batch_id, ctx.store.project_root)
        if not runtime or runtime.status not in {"running", "paused"}:
            continue
        count = len(requested & {_video_key(item.url) for item in state.items})
        if count:
            conflicts.append(f"{state.batch_id} 重复 {count} 条")
    if conflicts:
        raise HTTPException(409, "相同视频已有运行中或暂停中的任务，已阻止重复启动：" + "；".join(conflicts))


def _generation_action(ctx, task_id, action, body):
    with _generation_control(ctx):
        runtime = generation.get_background_batch_status(task_id, ctx.store.project_root)
        state = load_batch_state(task_id, ctx.store.project_root)
        if action == "pause":
            generation.pause_background_batch(task_id, ctx.store.project_root)
        elif runtime and runtime.status == "paused":
            if action == "retry":
                raise ValueError("请先继续已暂停的任务")
            stage = _saved_generation_options(ctx, state).get("target_stage", "recipe")
            _check_generation_overlaps(ctx, [item.url for item in selectable_items(state, "resume-unfinished", stage)], exclude=task_id)
            generation.resume_background_batch(task_id, ctx.store.project_root)
        elif runtime and runtime.status == "running":
            return {**_find(ctx, "generation", task_id), "already_running": True}
        else:
            previous = _saved_generation_options(ctx, state)
            updates = dict(body.options)
            updates.setdefault("target_stage", previous.get("target_stage", "recipe"))
            updates.setdefault("creator_name", previous.get("creator_name"))
            if body.target_stage:
                updates["target_stage"] = body.target_stage
            options = _generation_options(ctx, updates, None if body.use_current_config else previous)
            options.batch_id = task_id
            options.resume_mode = "retry-failed" if action == "retry" else "resume-unfinished"
            selected = selectable_items(state, options.resume_mode, options.target_stage)
            _check_generation_overlaps(ctx, [item.url for item in selected], exclude=task_id)
            state.options.update({key: value for key, value in asdict(options).items() if key not in {"cookies", "urls"}})
            save_batch_state(state, ctx.store.project_root)
            generation.start_background_batch(options, project_root=ctx.store.project_root)
        return _find(ctx, "generation", task_id)


def _start(ctx, body: StartTask):
    config = ctx.config()
    if body.kind == "generation":
        urls = canonical_video_urls(body.urls)
        options = _generation_options(ctx, {**body.options, "target_stage": body.target_stage, "creator_name": body.creator_name})
        stored = {key: value for key, value in asdict(options).items() if key not in {"urls", "cookies"}}
        with nullcontext() if body.create_only else _generation_control(ctx):
            if not body.create_only:
                _check_generation_overlaps(ctx, urls)
            state = create_batch_state(urls, stored, project_root=ctx.store.project_root)
            if not body.create_only:
                options.batch_id = state.batch_id
                options.resume_mode = "resume-unfinished"
                generation.start_background_batch(options, project_root=ctx.store.project_root)
        return _find(ctx, "generation", state.batch_id)
    folders = list(dict.fromkeys(ctx.folder(recipe_id) for recipe_id in body.recipe_ids)) if body.recipe_ids is not None else None
    active = next((state for state in _ai_states(body.kind, config.out_dir) if state.get("active")), None)
    if active:
        return summarize(body.kind, {**active, "already_running": True}, detail=True)
    common = {"limit": body.limit or None, "only_pending": body.only_pending, "folders": folders}
    options = dict(body.options)
    if body.kind == "frames":
        state = frames.start_frame_selection_batch(config.out_dir, **common, cookies=config.cookies, **options)
    else:
        extra = {"web_search"} if body.kind == "dish-info" else {"max_attempts", "consecutive_failure_limit", "retry_base_seconds"}
        if set(options) - LLM_KEYS - extra:
            raise ValueError("未知模型参数")
        merged = {**_llm_options(config), **options}
        function = reviews.start_ai_review_batch if body.kind == "review" else introductions.start_dish_info_batch
        state = function(config.out_dir, **common, **merged)
    return summarize(body.kind, state, detail=True)


def _action(ctx, kind: str, task_id: str, action: str, body: TaskAction):
    current = _find(ctx, kind, task_id)
    if action not in {"pause", "resume", "retry"}:
        raise HTTPException(404, "任务操作不存在")
    config = ctx.config()
    if kind == "settings":
        from .admin_settings import resume_settings_operation
        if action not in {"resume", "retry"}:
            raise ValueError("该任务支持继续和重试；当前操作完成前无法暂停")
        return _settings_summary(resume_settings_operation(ctx, task_id), detail=True)
    if kind == "creators":
        from .admin_creator_worker import start_creator_task
        if action != "retry" or not current["can_retry"]:
            raise ValueError("UP 主清单更新仅支持失败后重试")
        state = start_creator_task(ctx.store.project_root, current["options"]["url"], task_id=task_id)
        return summarize(kind, state, detail=True)
    if kind == "generation":
        return _generation_action(ctx, task_id, action, body)
    if action == "pause":
        function = {"review": reviews.request_stop_ai_review_batch, "frames": frames.stop_frame_selection_batch,
                    "dish-info": introductions.stop_dish_info_batch}[kind]
        function(config.out_dir, task_id)
        return _find(ctx, kind, task_id)
    options = dict(body.options)
    if kind != "frames":
        if set(options) - LLM_KEYS:
            raise ValueError("未知模型参数；续跑保持原任务的处理范围和联网选项")
        options = {**(_llm_options(config) if body.use_current_config else {}), **options}
    else:
        options["cookies"] = config.cookies
    function = {"review": reviews.resume_ai_review_batch, "frames": frames.resume_frame_selection_batch,
                "dish-info": introductions.resume_dish_info_batch}[kind]
    state = function(config.out_dir, task_id, retry_failed=action == "retry", **options)
    return summarize(kind, {**state, "already_running": state["batch_id"] != task_id}, detail=True)


def _call(function, *args):
    try:
        return function(*args)
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


def create_router(ctx) -> APIRouter:
    router = APIRouter()

    @router.get("/tasks")
    def tasks():
        return {"items": _call(list_tasks, ctx)}

    @router.get("/tasks/{kind}/{task_id}")
    def task(kind: str, task_id: str):
        return _call(_find, ctx, kind, task_id)

    @router.post("/tasks")
    def start(body: StartTask):
        return _call(_start, ctx, body)

    @router.post("/tasks/{kind}/{task_id}/{action}")
    def action(kind: str, task_id: str, action: str, body: TaskAction = TaskAction()):
        return _call(_action, ctx, kind, task_id, action, body)

    @router.get("/creators")
    def creators():
        root = creators_dir(ctx.store.project_root)
        result = []
        for path in sorted(root.glob("*/video_links.txt")):
            if path.is_symlink() or path.parent.is_symlink() or (path.parent / "creator.json").is_symlink():
                continue
            try:
                manifest = read_json(path.parent / "creator.json", expected_type=dict)
                urls = canonical_video_urls(path.read_text(encoding="utf-8-sig").splitlines())
                result.append({"id": str(manifest.get("uid") or path.parent.name),
                               "name": manifest.get("uploader") or path.parent.name,
                               "count": len(urls), "updated_at": manifest.get("crawled_at"), "urls": urls,
                               "complete": manifest.get("complete", True), "warnings": manifest.get("warnings", []),
                               "source_url": manifest.get("source_url")})
            except (OSError, ValueError):
                continue
        return {"items": result}

    @router.post("/creators/refresh")
    def refresh_creator(body: RefreshCreator):
        from .admin_creator_worker import start_creator_task
        return summarize("creators", _call(start_creator_task, ctx.store.project_root, body.url), detail=True)

    return router
