"""Explicit edits to generated artifacts, isolated from evidence-only AI review.

Slow work runs on a private snapshot. Applying its result rechecks the original
under the same recipe lock as the editor, so concurrent edits are never lost.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, fields
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import content_analysis, exports, optimizer, pipeline, recipe_review
from .admin_common import inside
from .admin_settings import enqueue_settings_operation, register_settings_download, register_settings_operation
from .recipe_extractor import Recipe, TranscriptSegment
from .review_policy import _semantic
from .screenshot import ScreenshotCandidate
from .storage import atomic_write_bytes, atomic_write_json, file_lock, read_json
from .utils import sanitize_filename

FILES = ("recipe.json", "note.md", "transcript.json", "source.json", "quality.json", "recipe.review.json", "extra_analysis.md", "extra_analysis.json")
ACTIONS = {"optimize", "rewrite", "reextract", "analysis", "candidates", "recapture", "clear-image", "upload-image", "choose-image", "create-review", "review-item", "apply-review", "export"}
LABELS = {"optimize": "优化菜谱笔记", "rewrite": "重写菜谱笔记", "reextract": "从字幕重新提取菜谱", "analysis": "二次分析",
          "candidates": "查找步骤候选图", "recapture": "重新截取步骤图片", "clear-image": "步骤不配图", "upload-image": "上传步骤图片",
          "choose-image": "采用步骤候选图", "create-review": "建立生成版审核", "review-item": "保存生成版审核意见", "apply-review": "应用生成版审核", "export": "导出菜谱"}


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(min_length=1, max_length=100)
    confirmed: StrictBool = False
    request: str = Field(default="提取视频中提到的通用烹饪技巧", max_length=20000)
    transcript: list[dict] | None = Field(default=None, max_length=50000)
    step_index: int = Field(default=1, ge=1, le=1000)
    timestamp: float = Field(default=0, ge=0, le=86400)
    content: str | None = Field(default=None, max_length=14 * 1024 * 1024)
    candidate: str | None = None
    item_id: str | None = Field(default=None, max_length=100)
    decision: str | None = None
    value: dict | None = None
    comment: str = Field(default="", max_length=20000)
    format: Literal["markdown", "obsidian", "pdf", "docx", "zip"] = "markdown"


def _paths(folder: Path):
    paths = [folder / name for name in FILES if (folder / name).is_file()]
    recipe = read_json(folder / "recipe.json", expected_type=dict)
    image_names = [recipe.get("cover_image_path"), *(step.get("screenshot_path") for step in recipe.get("steps", []) if isinstance(step, dict))]
    for value in image_names:
        if value:
            relative = Path(str(value))
            if relative.is_absolute() or ".." in relative.parts or (folder / relative).is_symlink():
                raise HTTPException(400, "菜谱图片必须使用 images 内的相对路径")
            path = inside(folder / relative, folder)
            if not path.is_relative_to(folder / "images"):
                raise HTTPException(400, "图片需保存在菜谱 images 目录")
            if path.is_file() and path not in paths:
                paths.append(path)
    for path in paths:
        if path.is_symlink() or inside(path, folder) != path:
            raise HTTPException(400, "菜谱工具不处理指向其他位置的文件")
    return paths


def tools_revision(folder: Path):
    digest = hashlib.sha256()
    for path in sorted(_paths(folder)):
        digest.update(path.relative_to(folder).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _check(folder, expected):
    if tools_revision(folder) != expected:
        raise HTTPException(409, "菜谱、字幕、笔记或图片已发生变化，请刷新后重新确认；本次结果没有覆盖原文件")


def _tools_root(ctx):
    root = ctx.root / ".admin-tools"
    if root.is_symlink():
        raise HTTPException(400, "工具任务目录不能是符号链接")
    return root


def _candidate(ctx, token):
    if not token or not re.fullmatch(r"[a-f0-9]{32}", token):
        raise HTTPException(404, "候选图片不存在")
    root = inside(_tools_root(ctx) / "candidates", _tools_root(ctx))
    return root / (token + ".json"), root / (token + ".jpg")


def _options(config):
    result = {field.name: getattr(config, field.name) for field in fields(optimizer.OptimizeOptions) if hasattr(config, field.name)}
    result["no_llm_summary"] = not config.enable_llm_summary or config.llm_provider == "none"
    return result


def _legacy_is_current(folder, review):
    current = read_json(folder / "recipe.json", expected_type=dict)
    # Compare only the original sections affected by applying this workflow.
    return all(_semantic(review.get("base_recipe", {}).get(key, [])) == _semantic(current.get(key, []))
               for key in recipe_review.REVIEW_SECTIONS)


def _export_paths(folder):
    # Capture only the recipe's artifacts and pictures, never media downloads,
    # secrets or repair history. The established exporter selects its own
    # portable bundle contents from this snapshot.
    result = set(_paths(folder))
    for name in ("recipe.ai-review.json", "recipe.dish-info.json", "job.json", "sync-meta.json"):
        path = folder / name
        if path.is_file():
            result.add(path)
    images = folder / "images"
    if images.is_symlink():
        raise HTTPException(400, "导出图片目录不能是符号链接")
    if images.is_dir():
        result.update(path for path in images.rglob("*") if path.is_file())
    for path in result:
        if path.is_symlink() or inside(path, folder) != path:
            raise HTTPException(400, "导出文件不能指向菜谱目录以外")
    return sorted(result)


def _export_snapshot(ctx, work, arguments):
    note = work / "note.md"
    if not note.is_file():
        raise HTTPException(422, "这道菜还没有笔记正文，请先根据菜谱生成笔记后再导出")
    kind = arguments.get("format", "markdown")
    recipe = read_json(work / "recipe.json", expected_type=dict)
    title = sanitize_filename(str(recipe.get("title") or "菜谱"), max_length=80)
    suffix = {"markdown": ".md", "obsidian": ".obsidian.md", "pdf": ".pdf", "docx": ".docx", "zip": ".recipe.zip"}[kind]
    destination = work / "exports" / (title + suffix)
    destination.parent.mkdir()
    warnings = []
    if kind in {"pdf", "docx"}:
        dependency = "reportlab.platypus" if kind == "pdf" else "docx"
        try:
            importlib.import_module(dependency)
        except (ImportError, ModuleNotFoundError) as exc:
            package = "reportlab" if kind == "pdf" else "python-docx"
            warnings.append(f"无法加载 {package}（{exc}）；已沿用原导出行为生成基础文字版，不包含图片。安装该依赖后可重新导出图文版。")
    if kind == "markdown":
        atomic_write_bytes(destination, note.read_bytes(), backup=False)
    else:
        {"obsidian": exports.export_obsidian, "pdf": exports.export_pdf, "docx": exports.export_docx,
         "zip": exports.export_recipe_bundle}[kind](note, destination)
    return {**register_settings_download(ctx, destination), "message": "菜谱导出已生成，可直接下载。",
            "recipe_id": arguments["recipe_id"], "format": kind, "warnings": warnings}


def _perform(ctx, folder, work, action, arguments):
    config = ctx.config()
    result = {"message": LABELS[action] + "完成", "recipe_id": arguments["recipe_id"], "action": action}
    changed = set()
    if action == "export":
        return changed, _export_snapshot(ctx, work, arguments)
    if action == "optimize":
        outcome = optimizer.optimize_existing_note(work, optimizer.OptimizeOptions(**_options(config)))
        result["quality_before"], result["quality_after"] = outcome.quality_before.score, outcome.quality_after.score
        changed.update(("note.md", "quality.json"))
    elif action == "rewrite":
        outcome = pipeline.regenerate_note_from_recipe(work, **_options(config))
        result["warnings"] = outcome.stage_errors or []
        changed.update(("note.md", "quality.json"))
    elif action == "reextract":
        transcript = arguments.get("transcript")
        if transcript is not None:
            atomic_write_json(work / "transcript.json", transcript)
            changed.add("transcript.json")
        pipeline.regenerate_recipe_from_transcript(work)
        changed.update(("recipe.json", "note.md", "quality.json"))
    elif action == "analysis":
        options = {field.name: getattr(config, field.name) for field in fields(content_analysis.ContentAnalysisOptions) if hasattr(config, field.name)}
        outcome = content_analysis.analyze_video_content(work, request=arguments["request"], options=content_analysis.ContentAnalysisOptions(**options))
        result["markdown"] = outcome.markdown
        metadata = read_json(work / "extra_analysis.json", expected_type=dict)
        metadata["analysis_path"] = "extra_analysis.md"
        atomic_write_json(work / "extra_analysis.json", metadata)
        changed.update(("extra_analysis.md", "extra_analysis.json"))
    elif action in {"candidates", "recapture", "clear-image", "upload-image", "choose-image"}:
        index = arguments["step_index"]
        video = next((inside(path, folder) for path in sorted((folder / "media").glob("video.*")) if path.is_file()), None)
        if action == "candidates":
            candidates = pipeline.suggest_step_screenshots(work, index, cookies=config.cookies, video_path=video, keep_video=False)
            result["candidates"] = []
            for candidate in candidates:
                token = uuid.uuid4().hex
                metadata_path, image_path = _candidate(ctx, token)
                metadata = {"recipe_id": arguments["recipe_id"], "revision": arguments["revision"], "step_index": index,
                            "timestamp": candidate.timestamp, "score": candidate.score, "id": token}
                atomic_write_bytes(image_path, candidate.content, backup=False)
                atomic_write_json(metadata_path, metadata, backup=False)
                result["candidates"].append({**metadata, "url": f"/api/admin/v1/recipes/{arguments['recipe_id']}/tools/media/{token}"})
            return changed, result
        if action == "recapture":
            pipeline.recapture_step_screenshot(work, index, arguments["timestamp"], cookies=config.cookies, video_path=video, keep_video=False)
        elif action == "clear-image":
            pipeline.clear_step_screenshot(work, index)
        elif action == "upload-image":
            pipeline.save_uploaded_step_screenshot(work, index, base64.b64decode(arguments["content"], validate=True))
        else:
            metadata_path, image_path = _candidate(ctx, arguments.get("candidate"))
            metadata = read_json(metadata_path, expected_type=dict)
            if metadata["recipe_id"] != arguments["recipe_id"] or metadata["step_index"] != index or metadata["revision"] != arguments["revision"]:
                raise HTTPException(409, "步骤候选图已经过期，请重新生成")
            pipeline.save_step_screenshot_candidate(work, index, ScreenshotCandidate(
                timestamp=metadata["timestamp"], score=metadata["score"], content=inside(image_path, _tools_root(ctx)).read_bytes()))
        changed.update(("recipe.json", "note.md", "quality.json"))
        if action != "clear-image":
            changed.add(f"images/step_{index:02d}.jpg")
    elif action == "create-review":
        recipe_review.create_recipe_review(Recipe.model_validate(read_json(work / "recipe.json", expected_type=dict)), work)
        changed.add("recipe.review.json")
    elif action == "review-item":
        review = recipe_review.load_recipe_review(work)
        if not _legacy_is_current(work, review):
            raise HTTPException(409, "原审核依据已过期，请根据当前菜谱重新建立生成版审核")
        recipe_review.decide_review_item(work, arguments["item_id"], arguments["decision"], value=arguments.get("value"), comment=arguments.get("comment", ""))
        changed.add("recipe.review.json")
    elif action == "apply-review":
        review = recipe_review.load_recipe_review(work)
        if not _legacy_is_current(work, review):
            raise HTTPException(409, "原审核依据已过期，不能覆盖当前菜谱")
        reviewed = recipe_review.recipe_from_completed_review(work).model_dump()
        current = read_json(work / "recipe.json", expected_type=dict)
        # Retain current cover, ratings, creator and other unrelated metadata.
        for section in recipe_review.REVIEW_SECTIONS:
            values = reviewed[section]
            if section == "steps":
                originals = [item for item in review["items"] if item.get("section") == "steps" and item.get("decision") != "skipped"]
                for value, item in zip(values, originals, strict=True):
                    old_step = current["steps"][item["index"]]
                    for key in tuple(value):
                        if key.startswith("screenshot_"):
                            value[key] = old_step.get(key)
            current[section] = values
        atomic_write_json(work / "recipe.json", current)
        pipeline.regenerate_note_from_recipe(work)
        changed.update(("recipe.json", "note.md", "quality.json", "recipe.review.json"))
    return changed, result


def run_tool_operation(ctx, arguments):
    folder = ctx.folder(arguments["recipe_id"])
    action = arguments["action"]
    root = _tools_root(ctx)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="work-", dir=root) as temporary:
        work = Path(temporary)
        with file_lock(folder / "recipe.json"):
            _check(folder, arguments["revision"])
            for source in _export_paths(folder) if action == "export" else _paths(folder):
                atomic_write_bytes(work / source.relative_to(folder), source.read_bytes(), backup=False)
        changed, result = _perform(ctx, folder, work, action, arguments)
        with file_lock(folder / "recipe.json"):
            _check(folder, arguments["revision"])
            if changed:
                backup = root / "history" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f-") + uuid.uuid4().hex[:8])
                originals = {}
                for name in changed:
                    destination = inside(folder / name, folder)
                    originals[name] = destination.read_bytes() if destination.exists() else None
                    if originals[name] is not None:
                        atomic_write_bytes(backup / name, originals[name], backup=False)
                atomic_write_json(backup / "operation.json", {"recipe_id": arguments["recipe_id"], "action": action, "revision": arguments["revision"], "changed": sorted(changed)}, backup=False)
                # All outputs were generated and validated before any original
                # is touched; rollback also handles a partial filesystem failure.
                written = []
                try:
                    for name in sorted(changed):
                        destination = inside(folder / name, folder)
                        atomic_write_bytes(destination, (work / name).read_bytes())
                        written.append(name)
                except Exception:
                    for name in reversed(written):
                        destination = inside(folder / name, folder)
                        if originals[name] is None:
                            destination.unlink(missing_ok=True)
                        else:
                            atomic_write_bytes(destination, originals[name], backup=False)
                    raise
                result["backup"] = backup.relative_to(ctx.root).as_posix()
        if changed:
            try:
                indexed = ctx.store.index_recipes()
                if indexed.get("errors"):
                    result.setdefault("warnings", []).append("修订已经保存；部分菜单索引未更新，后台索引会继续重试。")
            except Exception as exc:
                # The recipe commit succeeded. Do not present it as a failed
                # model task and encourage an unnecessary destructive retry.
                result.setdefault("warnings", []).append(f"修订已经保存；菜单索引暂未更新，后台会重试：{exc}")
        result["revision"] = tools_revision(folder)
        return result


def create_router(ctx):
    router = APIRouter()
    for action in ACTIONS:
        register_settings_operation("recipe_tool_" + action, run_tool_operation)

    @router.get("/recipes/{recipe_id}/tools")
    def details(recipe_id: str):
        folder = ctx.folder(recipe_id)
        legacy, legacy_error = None, None
        try:
            legacy = recipe_review.load_recipe_review(folder) if (folder / "recipe.review.json").is_file() else None
        except (OSError, ValueError, RuntimeError) as exc:
            legacy_error = str(exc)
        return {"id": recipe_id, "revision": tools_revision(folder), "recipe": read_json(folder / "recipe.json", expected_type=dict),
                "transcript": read_json(folder / "transcript.json") if (folder / "transcript.json").is_file() else [],
                "legacy_review": legacy, "legacy_error": legacy_error, "legacy_current": bool(legacy and _legacy_is_current(folder, legacy)),
                "analysis": (folder / "extra_analysis.md").read_text(encoding="utf-8") if (folder / "extra_analysis.md").is_file() else ""}

    @router.get("/recipes/{recipe_id}/tools/media/{token}")
    def candidate(recipe_id: str, token: str):
        ctx.folder(recipe_id)
        metadata_path, image_path = _candidate(ctx, token)
        metadata = read_json(metadata_path, expected_type=dict) if metadata_path.is_file() else {}
        if metadata.get("recipe_id") != recipe_id or not image_path.is_file():
            raise HTTPException(404, "候选图不存在")
        return FileResponse(inside(image_path, _tools_root(ctx)), media_type="image/jpeg", headers={"Cache-Control": "private, no-cache"})

    @router.post("/recipes/{recipe_id}/tools/{action}")
    def execute(recipe_id: str, action: str, body: ToolRequest):
        if action not in ACTIONS:
            raise HTTPException(404, "工具操作不存在")
        folder = ctx.folder(recipe_id)
        _check(folder, body.revision)
        if action not in {"candidates", "export"} and not body.confirmed:
            raise HTTPException(422, "请明确确认本次操作会修改生成版文件，并保留备份")
        if body.transcript is not None:
            for segment in body.transcript:
                try:
                    value = TranscriptSegment.model_validate(segment)
                except ValueError as exc:
                    raise HTTPException(422, "字幕需包含起止时间和文本") from exc
                if not math.isfinite(value.start) or not math.isfinite(value.end) or value.start < 0 or value.end < value.start:
                    raise HTTPException(422, "字幕起止时间无效")
        if action in {"candidates", "recapture", "clear-image", "upload-image", "choose-image"}:
            recipe = read_json(folder / "recipe.json", expected_type=dict)
            if body.step_index > len(recipe.get("steps", [])):
                raise HTTPException(422, "步骤不存在")
        if not math.isfinite(body.timestamp):
            raise HTTPException(422, "截图时间无效")
        if action == "upload-image":
            try:
                content = base64.b64decode(body.content or "", validate=True)
                if not content or len(content) > 10 * 1024 * 1024:
                    raise ValueError("图片过大")
                with Image.open(io.BytesIO(content)) as image:
                    image.verify()
            except Exception as exc:
                raise HTTPException(422, "请上传不超过 10 MB 的有效图片") from exc
        if action == "review-item" and (not body.item_id or body.decision not in {"accepted", "edited", "skipped"}):
            raise HTTPException(422, "请选择生成版审核项和处理意见")
        arguments = {**body.model_dump(), "recipe_id": recipe_id, "action": action}
        return enqueue_settings_operation(ctx, "recipe_tool_" + action, arguments)

    return router
