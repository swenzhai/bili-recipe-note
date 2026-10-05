"""Settings, portable downloads and cooking knowledge for the React admin.

All recipe references come from the current index. Uploaded/downloaded files have
server-generated identities; none of these endpoints serves a client path.
"""
from __future__ import annotations

import base64
import binascii
import io
import os
import re
import shutil
import threading
import uuid
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from PIL import Image

from . import branding, downloader, environment, knowledge_base as kb
from .batch_queue import list_batch_states
from .config import UIConfig, load_config, save_config
from .data_migration import plan_data_migration
from .data_paths import data_root, exports_dir, state_dir
from .deployment import export_deployment_bundle
from .handoff import export_batch_handoff, import_handoff_bundle
from .obsidian_archive import archive_recipe_to_obsidian
from .storage import atomic_write_bytes, atomic_write_json, read_json

_ID = re.compile(r"^[a-f0-9]{32}$")
_RUNNING: dict[str, threading.Thread] = {}
_RESUMERS: dict[str, Any] = {}
_STARTERS: dict[str, Any] = {}
_HANDLERS: dict[str, Any] = {}
_TASK_LOCK = threading.RLock()
_MAX_IMPORT = 256 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(value: str) -> str:
    if not _ID.fullmatch(value):
        raise HTTPException(404, "记录不存在")
    return value


def _try_operation_lock(path: Path):
    """Keep a native lock for the whole worker lifetime, including retries.

    An OS lock is released on process death. Unlike an age-based file lock it
    cannot expire while a slow model is still running in another API process.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.with_suffix(".run-lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, 2)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except (OSError, BlockingIOError):
        handle.close()
        return None


def _operation_running(path: Path) -> bool:
    worker = _RUNNING.get(str(path))
    if worker is not None and worker.is_alive():
        return True
    if not path.with_suffix(".run-lock").exists():
        return False
    handle = _try_operation_lock(path)
    if handle is None:
        return True
    handle.close()
    return False


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except HTTPException:
        raise
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


def _decode(content: Any, maximum: int) -> bytes:
    if not isinstance(content, str) or len(content) > ((maximum + 2) // 3) * 4:
        raise HTTPException(413, "文件超过允许大小")
    try:
        result = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "文件编码无效") from exc
    if not result or len(result) > maximum:
        raise HTTPException(413, "文件为空或超过允许大小")
    return result


def _config_updates(current: UIConfig, payload: dict) -> UIConfig:
    if not isinstance(payload, dict):
        raise HTTPException(422, "配置需为对象")
    values = asdict(current)
    defaults = asdict(UIConfig())
    unknown = set(payload) - set(values)
    if unknown:
        raise HTTPException(422, f"未知配置项：{', '.join(sorted(unknown))}")
    for key, value in payload.items():
        default = defaults[key]
        valid = (value is None or isinstance(value, str)) if default is None else type(value) is type(default)
        if not valid:
            raise HTTPException(422, f"配置项 {key} 类型不正确")
        if isinstance(value, str) and len(value) > 20000:
            raise HTTPException(422, f"配置项 {key} 过长")
        values[key] = value
    if values["llm_provider"] not in {"opencode", "codex", "openai", "local", "none"}:
        raise HTTPException(422, "不支持的文本模型提供方")
    if values["whisper_model"] not in {"tiny", "base", "small", "medium", "large-v3"}:
        raise HTTPException(422, "不支持的转写模型")
    if not 4 <= values["max_recipe_steps"] <= 12 or not 1 <= values["max_step_images"] <= 4:
        raise HTTPException(422, "菜谱步骤需为 4–12，步骤图片需为 1–4")
    if not values["out_dir"].strip() or not values["language"].strip():
        raise HTTPException(422, "菜谱目录和字幕语言不能为空")
    if values["restaurant_logo_path"] != current.restaurant_logo_path:
        raise HTTPException(422, "请使用 Logo 上传功能修改品牌图片")
    return UIConfig(**values)


def _knowledge_updates(payload: dict, *, partial: bool = False) -> dict:
    text_fields = {"title", "category", "content", "rationale", "evidence", "source_title", "source_url", "source_excerpt"}
    allowed = text_fields | {"tags", "applicable_to", "review_status"}
    if set(payload) - allowed:
        raise HTTPException(422, "包含不允许修改的知识字段")
    clean = {}
    for key, value in payload.items():
        if key in text_fields:
            if not isinstance(value, str) or len(value) > 100000:
                raise HTTPException(422, f"{key} 需为有效文本")
            clean[key] = value.strip()
        elif key in {"tags", "applicable_to"}:
            if not isinstance(value, list) or len(value) > 100 or any(not isinstance(item, str) or len(item) > 1000 for item in value):
                raise HTTPException(422, f"{key} 需为文本列表")
            clean[key] = [item.strip() for item in value if item.strip()]
        else:
            if value not in {"draft", "approved"}:
                raise HTTPException(422, "无效的确认状态")
            clean[key] = value
    for key in ("title", "content"):
        if (key in clean or not partial) and not clean.get(key):
            raise HTTPException(422, "标题和知识内容不能为空")
    return clean


def create_router(ctx) -> APIRouter:
    router = APIRouter()
    root = ctx.store.project_root
    operation_dir = state_dir(root) / "admin-operations"
    download_dir = exports_dir(root) / "admin-downloads"
    upload_dir = data_root(root) / "sources" / "admin-imports"

    def backup_knowledge(action: str) -> None:
        source = kb.knowledge_base_path(root)
        if source.is_file():
            suffix = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            atomic_write_bytes(source.with_name(f"{source.stem}.before-{action}-{suffix}{source.suffix}"), source.read_bytes(), backup=False)

    def register_download(path: Path) -> dict:
        return register_settings_download(ctx, path)

    def operation_path(operation_id: str) -> Path:
        return operation_dir / f"{_identifier(operation_id)}.json"

    def load_operation(operation_id: str) -> dict:
        path = operation_path(operation_id)
        if not path.is_file():
            raise HTTPException(404, "任务不存在")
        result = read_json(path, expected_type=dict)
        if result.get("status") in {"queued", "running"} and not _operation_running(path):
            result = {**result, "status": "interrupted", "error": "服务已重启，可以继续此操作"}
        return result

    def extraction_options() -> kb.KnowledgeExtractionOptions:
        config = ctx.config()
        return kb.KnowledgeExtractionOptions(**{field.name: getattr(config, field.name) for field in fields(kb.KnowledgeExtractionOptions)})

    def run_operation(operation_id: str, lifetime_lock) -> None:
        try:
            execute_operation(operation_id)
        finally:
            lifetime_lock.close()

    def execute_operation(operation_id: str) -> None:
        path = operation_path(operation_id)
        state = read_json(path, expected_type=dict)
        state.update(status="running", error="", updated_at=_now())
        atomic_write_json(path, state)
        try:
            kind, arguments = state["kind"], state["arguments"]
            if kind in {"knowledge_extract", "recipe_archive"}:
                options = extraction_options() if kind == "knowledge_extract" else None
                for item in state["items"]:
                    if item["status"] in {"done", "skipped"}:
                        continue
                    recipe_id = item.get("recipe_id") or item["id"]
                    state["current"] = recipe_id
                    item["status"] = "running"
                    atomic_write_json(path, state)
                    try:
                        folder = ctx.folder(recipe_id)
                        if kind == "recipe_archive":
                            ctx.check_revision(folder, item.get("revision", ""))
                            result = archive_recipe_to_obsidian(folder, arguments.get("vault_dir") or ctx.config().obsidian_vault_dir, conflict="update")
                            item.update(status="done", result=jsonable_encoder(asdict(result)))
                        else:
                            result = kb.extract_knowledge_from_folders([folder], options, root, skip_existing=arguments.get("skip_existing", True))
                            outcome = result.items[0]
                            item.update(jsonable_encoder(asdict(outcome)))
                            item["status"] = outcome.status
                    except Exception as exc:
                        item.update(status="failed", error=str(exc))
                    state["processed"] = sum(part["status"] in {"done", "skipped", "failed"} for part in state["items"])
                    state["updated_at"] = _now()
                    atomic_write_json(path, state)
                state["status"] = "completed_with_errors" if any(item["status"] == "failed" for item in state["items"]) else "completed"
            elif kind == "knowledge_document":
                uploaded = upload_dir / arguments["upload_name"]
                if not uploaded.resolve().is_relative_to(upload_dir.resolve()):
                    raise ValueError("无效的导入文档")
                result = kb.extract_knowledge_from_document(uploaded, extraction_options(), root)
                state["result"] = jsonable_encoder(asdict(result))
                state.update(status="completed", processed=1)
            elif kind == "deployment_export":
                destination = download_dir / "work" / f"{operation_id}.deployment.zip"
                result = export_deployment_bundle(ctx.store.out_dir, destination, project_root=root)
                state["result"] = register_download(result.path)
                destination.unlink(missing_ok=True)
                state.update(status="completed", processed=1)
            elif kind == "handoff_export":
                destination = download_dir / "work" / f"{operation_id}.handoff.zip"
                result = export_batch_handoff(arguments["batch_id"], ctx.store.out_dir, destination, project_root=root)
                state["result"] = register_download(result.path)
                destination.unlink(missing_ok=True)
                state.update(status="completed", processed=1)
            elif kind == "handoff_import":
                uploaded = upload_dir / arguments["upload_name"]
                if not uploaded.resolve().is_relative_to(upload_dir.resolve()):
                    raise ValueError("无效的导入包")
                result = import_handoff_bundle(uploaded, ctx.store.out_dir, project_root=root)
                state["result"] = jsonable_encoder(asdict(result))
                state.update(status="completed", processed=1)
            elif kind in _HANDLERS:
                state["result"] = jsonable_encoder(_HANDLERS[kind](ctx, arguments))
                state.update(status="completed", processed=1)
            else:
                raise ValueError("不支持的后台操作")
        except Exception as exc:
            state.update(status="failed", error=str(exc))
        finally:
            state["updated_at"] = _now()
            atomic_write_json(path, state)

    def start_operation(kind: str, arguments: dict, *, items: list[dict] | None = None) -> dict:
        operation_id = uuid.uuid4().hex
        state = {"id": operation_id, "kind": kind, "status": "queued", "created_at": _now(), "updated_at": _now(), "out_dir": str(ctx.store.out_dir),
                 "arguments": arguments, "items": [{"status": "pending", **item} for item in (items or [])], "total": len(items) if items is not None else 1, "processed": 0}
        path = operation_path(operation_id)
        with _TASK_LOCK:
            lifetime_lock = _try_operation_lock(path)
            if lifetime_lock is None:
                raise HTTPException(409, "任务已在另一服务进程运行")
            try:
                atomic_write_json(path, state)
                worker = threading.Thread(target=run_operation, args=(operation_id, lifetime_lock), daemon=True, name=f"admin-{operation_id[:8]}")
                _RUNNING[str(path)] = worker
                worker.start()
            except Exception:
                lifetime_lock.close()
                raise
        return state

    @router.get("/settings")
    def get_settings():
        config = load_config(root)
        configured = Path(config.out_dir).expanduser()
        configured = (configured if configured.is_absolute() else root / configured).resolve()
        return {"config": asdict(config), "active_out_dir": str(ctx.store.out_dir), "restart_required": configured != ctx.store.out_dir,
                "data_root": str(data_root(root)), "indexing": ctx.indexer.snapshot(),
                "self_join_enabled": ctx.store.self_join_enabled(),
                "branding": branding.branding_payload(config, root, logo_url="/api/v1/branding/logo")}

    @router.put("/settings")
    def put_settings(payload: dict = Body(...)):
        current = load_config(root)
        config = _config_updates(current, payload.get("config", payload))
        _call(save_config, config, root)
        return get_settings()

    @router.post("/settings/environment")
    def check_environment():
        return {"checks": [asdict(item) for item in _call(environment.run_environment_checks)]}

    @router.get("/settings/devices")
    def devices():
        return {"devices": ctx.store.list_devices(), "self_join_enabled": ctx.store.self_join_enabled()}

    @router.put("/settings/devices/self-join")
    def self_join(payload: dict = Body(...)):
        if type(payload.get("enabled")) is not bool:
            raise HTTPException(422, "enabled 需为布尔值")
        ctx.store.set_self_join_enabled(payload["enabled"])
        return devices()

    @router.delete("/settings/devices/{device_id}")
    def revoke_device(device_id: str):
        if not any(item["id"] == device_id for item in ctx.store.list_devices()):
            raise HTTPException(404, "设备不存在")
        ctx.store.revoke_device(device_id)
        return devices()

    @router.post("/settings/cookies/{action}")
    def cookies(action: str):
        config = load_config(root)
        if action == "validate":
            return {"valid": bool(config.cookies and _call(downloader.validate_bilibili_cookie_file, config.cookies))}
        if action == "import":
            config.cookies = str(_call(downloader.import_edge_cookies, root))
        elif action == "clear":
            _call(downloader.remove_imported_cookies, root)
            config.cookies = None
        else:
            raise HTTPException(404, "操作不存在")
        save_config(config, root)
        return {"configured": bool(config.cookies)}

    @router.post("/settings/logo")
    def upload_logo(payload: dict = Body(...)):
        content = _decode(payload.get("content"), branding.MAX_LOGO_BYTES)
        try:
            with Image.open(io.BytesIO(content)) as source:
                if source.width * source.height > 25_000_000:
                    raise ValueError("Logo 像素过大，最多 2500 万像素")
            path = branding.save_logo(content, root)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        config = load_config(root)
        config.restaurant_logo_path = str(path)
        save_config(config, root)
        return get_settings()

    @router.delete("/settings/logo")
    def delete_logo():
        branding.remove_logo(root)
        config = load_config(root)
        config.restaurant_logo_path = None
        save_config(config, root)
        return get_settings()

    @router.get("/downloads/{download_id}")
    def download(download_id: str):
        manifest = download_dir / f"{_identifier(download_id)}.json"
        if not manifest.is_file():
            raise HTTPException(404, "下载文件不存在")
        data = read_json(manifest, expected_type=dict)
        target = (download_dir / data["file"]).resolve()
        if not target.is_relative_to(download_dir.resolve()) or not target.is_file():
            raise HTTPException(404, "下载文件不存在")
        return FileResponse(target, filename=data["name"])

    @router.get("/settings/operations")
    def operations():
        states = [load_operation(path.stem) for path in operation_dir.glob("*.json") if _ID.fullmatch(path.stem)]
        return {"items": sorted(states, key=lambda item: item["created_at"], reverse=True)}

    @router.get("/settings/operations/{operation_id}")
    def operation(operation_id: str):
        return load_operation(operation_id)

    @router.post("/settings/operations/{operation_id}/resume")
    def resume_operation(operation_id: str):
        path = operation_path(operation_id)
        with _TASK_LOCK:
            state = load_operation(operation_id)
            if state.get("out_dir", str(ctx.store.out_dir)) != str(ctx.store.out_dir):
                raise HTTPException(409, "此任务属于另一菜谱库，请切换回原菜谱库并重启服务后继续")
            if state["status"] in {"queued", "running", "completed"}:
                return state
            lifetime_lock = _try_operation_lock(path)
            if lifetime_lock is None:
                return load_operation(operation_id)
            state.update(status="queued", error="", updated_at=_now())
            try:
                atomic_write_json(path, state)
                worker = threading.Thread(target=run_operation, args=(operation_id, lifetime_lock), daemon=True)
                _RUNNING[str(path)] = worker
                worker.start()
            except Exception:
                lifetime_lock.close()
                raise
        return state

    @router.get("/settings/handoff/batches")
    def handoff_batches():
        return {"items": [{"id": item.batch_id, "count": len(item.items), "updated_at": item.updated_at} for item in list_batch_states(root)]}

    @router.post("/settings/export")
    def deployment_export():
        return start_operation("deployment_export", {})

    @router.post("/settings/handoff/export")
    def handoff_export(payload: dict = Body(...)):
        batch_id = payload.get("batch_id")
        if not isinstance(batch_id, str) or batch_id not in {item.batch_id for item in list_batch_states(root)}:
            raise HTTPException(404, "批次不存在")
        return start_operation("handoff_export", {"batch_id": batch_id})

    @router.post("/settings/handoff/import")
    async def handoff_import(request: Request):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > _MAX_IMPORT:
                raise HTTPException(413, "交接包不能超过 256 MiB")
        if not content or content[:2] != b"PK":
            raise HTTPException(422, "请上传 ZIP 格式的工作交接包")
        name = f"{uuid.uuid4().hex}.handoff.zip"
        atomic_write_bytes(upload_dir / name, bytes(content))
        return start_operation("handoff_import", {"upload_name": name})

    @router.get("/settings/migration")
    def migration_preview():
        return {"plan": _call(plan_data_migration, root), "command": "python -m bili_recipe_notes --migrate-data apply",
                "requires_offline": True, "message": "迁移会移动数据库与菜谱。先停止管理服务、点餐服务和后台任务，再在服务器终端执行。"}

    @router.get("/knowledge")
    def knowledge(query: str = "", category: str = "", status: str = "", due: bool = False, offset: int = 0, limit: int = 50):
        entries = _call(kb.search_knowledge_entries, query, category, root)
        due_ids = {entry.id for entry in _call(kb.due_review_entries, root, 100000)}
        if status:
            entries = [entry for entry in entries if entry.review_status == status]
        if due:
            entries = [entry for entry in entries if entry.id in due_ids]
        offset, limit = max(0, offset), max(1, min(limit, 100))
        return {"items": [{**asdict(entry), "issues": kb.knowledge_quality_issues(entry), "due": entry.id in due_ids} for entry in entries[offset:offset + limit]],
                "total": len(entries), "categories": list(kb.KNOWLEDGE_CATEGORIES), "due_count": len(due_ids)}

    @router.post("/knowledge")
    def add_knowledge(payload: dict = Body(...)):
        values = _knowledge_updates(payload)
        entry = kb.CookingKnowledgeEntry(id=uuid.uuid4().hex, title=values.pop("title"), content=values.pop("content"),
                                         category=values.pop("category", "其他"), source_kind="manual", **values)
        _, added, updated = _call(kb.upsert_knowledge_entries, [entry], root)
        return {"added": added, "updated": updated}

    @router.post("/knowledge/export")
    def export_knowledge(payload: dict = Body(...)):
        kind = payload.get("kind", "markdown")
        if kind not in {"markdown", "anki", "csv"}:
            raise HTTPException(422, "导出格式应为 markdown、anki 或 csv")
        path = _call(kb.export_knowledge_base, kind, str(payload.get("category", "")), root)
        return register_download(path)

    @router.post("/knowledge/extract")
    def extract_knowledge(payload: dict = Body(...)):
        recipe_ids = payload.get("recipe_ids")
        if not isinstance(recipe_ids, list) or not 1 <= len(recipe_ids) <= 500 or any(not isinstance(item, str) for item in recipe_ids):
            raise HTTPException(422, "请选择 1–500 道菜谱")
        if type(payload.get("skip_existing", True)) is not bool:
            raise HTTPException(422, "skip_existing 需为布尔值")
        for recipe_id in recipe_ids:
            ctx.folder(recipe_id)
        return start_operation("knowledge_extract", {"skip_existing": payload.get("skip_existing", True)},
                               items=[{"id": recipe_id, "status": "pending"} for recipe_id in dict.fromkeys(recipe_ids)])

    @router.post("/knowledge/documents")
    def extract_document(payload: dict = Body(...)):
        filename = Path(str(payload.get("name", ""))).name
        if Path(filename).suffix.lower() not in {".txt", ".md", ".pdf", *kb.DOCUMENT_IMAGE_SUFFIXES}:
            raise HTTPException(422, "仅支持文本、Markdown、PDF 或图片")
        content = _decode(payload.get("content"), 20 * 1024 * 1024)
        name = f"{uuid.uuid4().hex}-{Path(filename).stem[:60]}{Path(filename).suffix.lower()}"
        atomic_write_bytes(upload_dir / name, content)
        return start_operation("knowledge_document", {"upload_name": name})

    @router.get("/knowledge/backups")
    def knowledge_backups():
        return {"items": [{"id": path.name, "size": path.stat().st_size} for path in kb.list_knowledge_backups(root)]}

    @router.post("/knowledge/backups/restore")
    def restore_knowledge(payload: dict = Body(...)):
        by_name = {path.name: path for path in kb.list_knowledge_backups(root)}
        name = payload.get("id")
        if not isinstance(name, str) or name not in by_name:
            raise HTTPException(404, "备份不存在")
        backup_knowledge("knowledge-restore")
        _call(kb.restore_knowledge_backup, by_name[name], root)
        return {"restored": True}

    @router.get("/knowledge/duplicates")
    def knowledge_duplicates():
        return {"groups": jsonable_encoder(kb.suggest_duplicate_groups(project_root=root))}

    @router.post("/knowledge/merge")
    def merge_knowledge(payload: dict = Body(...)):
        primary, others = payload.get("primary_id"), payload.get("duplicate_ids")
        if not isinstance(primary, str) or not isinstance(others, list) or not others or any(not isinstance(item, str) for item in others):
            raise HTTPException(422, "请明确选择主条目和待合并条目")
        backup_knowledge("knowledge-merge")
        return asdict(_call(kb.merge_knowledge_entries, primary, others, root))

    @router.put("/knowledge/{entry_id}")
    def update_knowledge(entry_id: str, payload: dict = Body(...)):
        updates = _knowledge_updates(payload, partial=True)
        backup_knowledge("knowledge-edit")
        return asdict(_call(kb.update_knowledge_entry, entry_id, updates, root))

    @router.delete("/knowledge/{entry_id}")
    def delete_knowledge(entry_id: str):
        backup_knowledge("knowledge-delete")
        _call(kb.delete_knowledge_entry, entry_id, root)
        return {"deleted": True}

    @router.post("/knowledge/{entry_id}/review")
    def review_knowledge(entry_id: str, payload: dict = Body(...)):
        mastery = payload.get("mastery")
        if mastery not in {"已掌握", "还模糊", "需要实践"}:
            raise HTTPException(422, "请选择掌握情况")
        return asdict(_call(kb.record_knowledge_review, entry_id, mastery, root))

    @router.post("/knowledge/{entry_id}/practice")
    def practice_knowledge(entry_id: str, payload: dict = Body(...)):
        for key in ("dish", "outcome", "notes"):
            if not isinstance(payload.get(key, ""), str) or len(payload.get(key, "")) > 20000:
                raise HTTPException(422, "实践记录需为文本")
        if not str(payload.get("dish", "")).strip() or not str(payload.get("notes", "")).strip():
            raise HTTPException(422, "请填写菜品和实践心得")
        return asdict(_call(kb.add_practice_record, entry_id, payload["dish"], payload.get("outcome", ""), payload["notes"], project_root=root))

    _RESUMERS[f"{operation_dir}:{ctx.store.out_dir}"] = resume_operation
    _STARTERS[f"{operation_dir}:{ctx.store.out_dir}"] = start_operation
    return router


def get_settings_operation(ctx, operation_id: str) -> dict:
    path = state_dir(ctx.store.project_root) / "admin-operations" / f"{_identifier(operation_id)}.json"
    if not path.is_file():
        raise HTTPException(404, "任务不存在")
    result = read_json(path, expected_type=dict)
    if result.get("status") in {"queued", "running"} and not _operation_running(path):
        result = {**result, "status": "interrupted", "error": "服务已重启，可以继续此操作"}
    return result


def list_settings_operations(ctx) -> list[dict]:
    directory = state_dir(ctx.store.project_root) / "admin-operations"
    return sorted((get_settings_operation(ctx, path.stem) for path in directory.glob("*.json") if _ID.fullmatch(path.stem)),
                  key=lambda item: item["created_at"], reverse=True)


def resume_settings_operation(ctx, operation_id: str) -> dict:
    directory = f"{state_dir(ctx.store.project_root) / 'admin-operations'}:{ctx.store.out_dir}"
    if directory not in _RESUMERS:
        create_router(ctx)
    return _RESUMERS[directory](operation_id)


def register_settings_operation(kind: str, handler) -> None:
    """Register a restart-safe handler; its arguments are persisted as JSON.

    Register handlers during router creation, before accepting requests. The
    callable must be idempotent or explicitly reject a stale expected revision.
    """
    if not kind or kind in {"knowledge_extract", "knowledge_document", "deployment_export", "handoff_export", "handoff_import", "recipe_archive"}:
        raise ValueError("Reserved or empty operation kind")
    _HANDLERS[kind] = handler


def enqueue_settings_operation(ctx, kind: str, arguments: dict) -> dict:
    if kind not in _HANDLERS:
        raise HTTPException(422, "后台操作尚未注册")
    directory = f"{state_dir(ctx.store.project_root) / 'admin-operations'}:{ctx.store.out_dir}"
    if directory not in _STARTERS:
        create_router(ctx)
    return _STARTERS[directory](kind, arguments)


def start_settings_operation(ctx, kind: str, arguments: dict, *, items: list[dict] | None = None) -> dict:
    """Start a known operation after the calling endpoint validates its scope."""
    if kind not in _HANDLERS and kind not in {"recipe_archive", "knowledge_extract", "knowledge_document", "deployment_export", "handoff_export", "handoff_import"}:
        raise HTTPException(422, "后台操作尚未注册")
    directory = f"{state_dir(ctx.store.project_root) / 'admin-operations'}:{ctx.store.out_dir}"
    if directory not in _STARTERS:
        create_router(ctx)
    return _STARTERS[directory](kind, arguments, items=items)


def register_settings_download(ctx, path: Path) -> dict:
    """Copy a service-produced file into the controlled admin download store."""
    directory = exports_dir(ctx.store.project_root) / "admin-downloads"
    token = uuid.uuid4().hex
    path = Path(path)
    target = directory / f"{token}{path.suffix}"
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    atomic_write_json(directory / f"{token}.json", {"name": path.name, "file": target.name})
    return {"id": token, "name": path.name, "url": f"/api/admin/v1/downloads/{token}", "size": target.stat().st_size}
