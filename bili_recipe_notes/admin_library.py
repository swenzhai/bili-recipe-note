"""Library browsing, explicit editing and media for the React administrator."""
from __future__ import annotations

import base64
import hashlib
import io
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from .admin_common import AdminContext, inside, read_object, revision
from .cover_policy import MANUALLY_APPROVED_COVER_STATUSES
from .dish_info import load_dish_info
from .frame_selection_service import load_frame_candidate
from .markdown_writer import render_markdown
from .obsidian_archive import archive_recipe_to_obsidian, recipe_archive_status
from .pipeline import (capture_recipe_cover_candidate, mark_recipe_cover_unavailable,
                       save_recipe_cover_content, suggest_recipe_cover_screenshots)
from .recipe_extractor import Recipe
from .review_policy import get_review_state, review_content_fingerprint
from .screenshot import crop_screenshot_box_content
from .storage import atomic_write_bytes, atomic_write_json, atomic_write_text, file_lock


class Publications(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=2000)
    published: bool


class EditRecipe(BaseModel):
    revision: str
    recipe: dict
    note: str | None = Field(default=None, max_length=2_000_000)
    regenerate_note: bool = False


class CoverRequest(BaseModel):
    revision: str
    source: str
    candidate_revision: str | None = None
    crop: dict[str, int] | None = None


class CaptureRequest(BaseModel):
    timestamp: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class UploadRequest(BaseModel):
    content: str = Field(max_length=16_000_000)


class ArchiveRequest(BaseModel):
    revision: str


COVER_DECIDED = MANUALLY_APPROVED_COVER_STATUSES | {"no_suitable", "unavailable"}


def media_url(recipe_id: str, kind: str, version: str = "") -> str:
    return f"/api/admin/v1/recipes/{quote(recipe_id, safe='')}/media/{kind}?v={quote(version, safe='')}"


def _frame_revision(candidate: dict, content: bytes) -> str:
    digest = hashlib.sha256(content)
    digest.update(str(candidate.get("image_path", "")).encode())
    digest.update(str(candidate.get("run_id", "")).encode())
    return digest.hexdigest()


def _image_path(folder: Path, raw: str | None) -> Path | None:
    if not raw or "://" in str(raw):
        return None
    path = inside(folder / raw, folder)
    return path if path.is_file() else None


def _cover_path(folder: Path, recipe: dict) -> Path | None:
    if recipe.get("cover_image_status") in {"no_suitable", "unavailable"}:
        return None
    selected = _image_path(folder, recipe.get("cover_image_path"))
    if selected:
        return selected
    return next((path for step in reversed(recipe.get("steps", []))
                 if (path := _image_path(folder, step.get("screenshot_path")))), None)


def _summary(row: dict) -> dict:
    recipe = row["payload"]
    state = row["review_state"]
    folder = Path(row["output_folder"])
    try:
        cover = _cover_path(folder, recipe)
    except HTTPException:
        cover = None
    return {key: row[key] for key in ("id", "title", "category", "creator_name", "published", "recommended", "hidden", "updated_at")} | {
        "cover_status": recipe.get("cover_image_status") or "unreviewed",
        "cover_url": media_url(row["id"], "cover", str(cover.stat().st_mtime_ns)) if cover else None,
        "review_status": "confirmed" if state["manual_completed"] else "needs_review" if state["needs_confirmation"] else "reviewed",
        "tags": recipe.get("tags", []), "cuisine": recipe.get("cuisine", ""),
    }


def _candidate_root(ctx: AdminContext, folder: Path) -> Path:
    key = hashlib.sha256(str(folder.relative_to(ctx.root)).encode()).hexdigest()[:24]
    return inside(ctx.root / ".admin" / "cover-candidates" / key, ctx.root)


def _save_candidate(ctx, recipe_id, content, *, timestamp=None, source_kind="video", label="视频候选", expected_fingerprint=None):
    folder = ctx.folder(recipe_id)
    fingerprint = review_content_fingerprint(folder)
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        raise HTTPException(409, "截图期间菜谱或来源已变化，请刷新后重新生成候选")
    root = _candidate_root(ctx, folder)
    token = uuid.uuid4().hex
    with Image.open(io.BytesIO(content)) as source:
        normalized = ImageOps.exif_transpose(source).convert("RGB")
        size = normalized.size
        buffer = io.BytesIO()
        normalized.save(buffer, format="JPEG", quality=95)
        content = buffer.getvalue()
    atomic_write_bytes(root / f"{token}.jpg", content, backup=False)
    record = {"id": token, "timestamp": timestamp, "source_kind": source_kind, "label": label,
              "dimensions": list(size), "revision": fingerprint}
    atomic_write_json(root / f"{token}.json", record, backup=False)
    return {**record, "url": media_url(recipe_id, f"candidate-{token}")}


def create_router(ctx: AdminContext) -> APIRouter:
    router = APIRouter()

    @router.get("/overview")
    def overview():
        rows = ctx.rows()
        recent = sorted(rows, key=lambda row: row["updated_at"], reverse=True)[:6]
        return {"counts": {
            "total": len(rows), "published": sum(row["published"] and not row["hidden"] for row in rows),
            "hidden": sum(row["hidden"] for row in rows),
            "cover_pending": sum(not row["hidden"] and row["payload"].get("cover_image_status") not in COVER_DECIDED for row in rows),
            "review_pending": sum(row["review_state"]["needs_confirmation"] for row in rows),
        }, "recent": [_summary(row) for row in recent], "indexing": ctx.indexer.snapshot()}

    @router.get("/recipes")
    def recipes(q: str = "", category: str = "", visibility: Literal["visible", "hidden", "all"] = "visible",
                published: Literal["all", "true", "false"] = "all", page: int = Query(1, ge=1),
                page_size: int = Query(24, ge=1, le=100), review_status: str = "", cover_status: str = ""):
        rows = ctx.rows()
        categories = sorted({row["category"] for row in rows if row["category"]})
        terms = q.casefold().split()
        rows = [row for row in rows if (visibility == "all" or row["hidden"] == (visibility == "hidden"))
                and (not category or row["category"] == category)
                and (published == "all" or row["published"] == (published == "true"))
                and (not review_status or row["review_state"]["manual_completed"] == (review_status == "confirmed"))
                and (not cover_status or (row["payload"].get("cover_image_status") in COVER_DECIDED) == (cover_status == "confirmed"))
                and all(term in " ".join([row["title"], row["creator_name"], row["category"], str(row["payload"].get("tags", [])),
                                          str(row["payload"].get("ingredients", []))]).casefold() for term in terms)]
        rows.sort(key=lambda row: (row["title"], row["id"]))
        start = (page - 1) * page_size
        return {"items": [_summary(row) for row in rows[start:start + page_size]], "total": len(rows),
                "page": page, "page_size": page_size, "categories": categories, "indexing": ctx.indexer.snapshot()}

    @router.post("/recipes/publication")
    def publication(body: Publications):
        rows = {row["id"]: row for row in ctx.rows()}
        for recipe_id in body.ids:
            row = rows.get(recipe_id)
            if row is None:
                raise HTTPException(404, "菜谱不存在或尚未完成索引")
            if body.published and row["hidden"]:
                raise HTTPException(409, "请先在审核工作台恢复内容展示，再上架菜单")
        count = ctx.store.set_recipe_publications({recipe_id: body.published for recipe_id in body.ids})
        return {"updated": count}

    @router.get("/recipes/{recipe_id}")
    def detail(recipe_id: str):
        folder = ctx.folder(recipe_id)
        version = revision(folder)
        recipe = read_object(folder / "recipe.json")
        state = get_review_state(folder)
        policy = ctx.store.recipe_visibility_states([folder]).get(str(folder), {})
        candidate = load_frame_candidate(ctx.root, folder, include_stale=True)
        if candidate:
            candidate = {key: candidate.get(key) for key in ("status", "timestamp", "reason", "dimensions", "run_id", "fingerprint")}
            if candidate["status"] in {"ok", "needs_review"}:
                image = load_frame_candidate(ctx.root, folder)
                candidate["url"] = media_url(recipe_id, "ai", str(image.get("run_id", "")))
                image_path = inside(Path(image["image_path"]), ctx.root / ".frame-selection")
                candidate["candidate_revision"] = _frame_revision(image, image_path.read_bytes())
        note_path = inside(folder / "note.md", folder)
        result = {"id": recipe_id, "recipe": recipe, "revision": version, "review": state,
                "visibility": policy, "note": note_path.read_text(encoding="utf-8") if note_path.is_file() else "",
                "source": read_object(inside(folder / "source.json", folder), {}),
                "transcript": read_object(inside(folder / "transcript.json", folder), []),
                "cover_url": media_url(recipe_id, "cover", version) if _cover_path(folder, recipe) else None,
                "step_images": [{"index": i, "url": media_url(recipe_id, f"step-{i}", version)}
                                for i, step in enumerate(recipe.get("steps", [])) if _image_path(folder, step.get("screenshot_path"))],
                "frame_candidate": candidate, "dish_info": load_dish_info(folder),
                "archive_status": recipe_archive_status(folder)}
        ctx.check_revision(folder, version)
        return result

    @router.get("/recipes/{recipe_id}/media/{kind}")
    def media(recipe_id: str, kind: str):
        folder = ctx.folder(recipe_id)
        recipe = read_object(folder / "recipe.json")
        path = None
        if kind == "cover":
            path = _cover_path(folder, recipe)
        elif kind.startswith("step-") and kind[5:].isdigit():
            index = int(kind[5:])
            steps = recipe.get("steps", [])
            if index < len(steps):
                path = _image_path(folder, steps[index].get("screenshot_path"))
        elif kind == "ai":
            candidate = load_frame_candidate(ctx.root, folder)
            if candidate and candidate["status"] in {"ok", "needs_review"}:
                path = inside(Path(candidate["image_path"]), ctx.root / ".frame-selection")
        elif kind.startswith("candidate-") and len(kind) == 42 and all(ch in "0123456789abcdef" for ch in kind[10:]):
            path = _candidate_root(ctx, folder) / f"{kind[10:]}.jpg"
        if path is None or not path.is_file():
            raise HTTPException(404, "图片不存在或候选已失效")
        return FileResponse(inside(path, ctx.root), headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"})

    @router.put("/recipes/{recipe_id}")
    def edit(recipe_id: str, body: EditRecipe):
        folder = ctx.folder(recipe_id)
        allowed = {"title", "category", "cuisine", "tags", "servings", "total_time", "difficulty", "ingredients",
                   "seasonings", "steps", "tools", "prep_items", "summary_tips", "uncertain_points",
                   "taste_rating", "difficulty_rating", "time_rating"}
        unknown = set(body.recipe) - allowed
        if unknown:
            raise HTTPException(422, "不支持编辑的字段：" + "、".join(sorted(unknown)))
        with file_lock(folder / "recipe.json"):
            ctx.check_revision(folder, body.revision)
            merged = {**read_object(folder / "recipe.json"), **body.recipe}
            model = Recipe.model_validate(merged)
            if not model.title.strip():
                raise HTTPException(422, "菜名不能为空")
            atomic_write_json(folder / "recipe.json", merged)
            if body.regenerate_note or body.note is not None:
                atomic_write_text(inside(folder / "note.md", folder), render_markdown(model) if body.regenerate_note else body.note)
        warning = ctx.index_after_save()
        return {**detail(recipe_id), "sync_warning": warning}

    @router.post("/recipes/{recipe_id}/archive")
    def archive(recipe_id: str, body: ArchiveRequest):
        folder = ctx.folder(recipe_id)
        with file_lock(folder / "recipe.json"):
            ctx.check_revision(folder, body.revision)
            result = archive_recipe_to_obsidian(folder, ctx.config().obsidian_vault_dir)
        return {"result": {key: str(value) for key, value in asdict(result).items()}}

    @router.get("/recipes/{recipe_id}/cover-candidates")
    def candidates(recipe_id: str):
        folder = ctx.folder(recipe_id)
        fingerprint = review_content_fingerprint(folder)
        items = []
        for path in sorted(_candidate_root(ctx, folder).glob("*.json"), reverse=True):
            record = read_object(inside(path, ctx.root), {})
            if record.get("revision") == fingerprint:
                items.append({**record, "url": media_url(recipe_id, "candidate-" + path.stem)})
        return {"items": items}

    @router.post("/recipes/{recipe_id}/cover-candidates")
    def capture(recipe_id: str, body: CaptureRequest):
        folder = ctx.folder(recipe_id)
        fingerprint = review_content_fingerprint(folder)
        config = ctx.config()
        if body.timestamp is None:
            found = suggest_recipe_cover_screenshots(folder, cookies=config.cookies, keep_video=config.keep_media)
        else:
            found = [capture_recipe_cover_candidate(folder, body.timestamp, cookies=config.cookies, keep_video=config.keep_media)]
        return {"items": [_save_candidate(ctx, recipe_id, item.content, timestamp=item.timestamp,
                                           expected_fingerprint=fingerprint) for item in found]}

    @router.post("/recipes/{recipe_id}/cover-upload")
    def upload(recipe_id: str, body: UploadRequest):
        try:
            content = base64.b64decode(body.content, validate=True)
            with Image.open(io.BytesIO(content)) as source:
                source.verify()
        except Exception as exc:
            raise HTTPException(422, "请上传有效的 PNG/JPEG/WebP 图片") from exc
        return _save_candidate(ctx, recipe_id, content, source_kind="upload", label="上传照片")

    @router.post("/recipes/{recipe_id}/cover")
    def save_cover(recipe_id: str, body: CoverRequest):
        folder = ctx.folder(recipe_id)
        with file_lock(folder / "recipe.json"):
            ctx.check_revision(folder, body.revision)
            if body.source == "unavailable":
                mark_recipe_cover_unavailable(folder)
            else:
                recipe = read_object(folder / "recipe.json")
                timestamp = None
                step_index = None
                source_kind, label = "video", "人工确认的菜品特写"
                if body.source == "ai":
                    candidate = load_frame_candidate(ctx.root, folder)
                    if not candidate or candidate["status"] not in {"ok", "needs_review"}:
                        raise HTTPException(409, "AI 候选已失效，请重新选图")
                    content = inside(Path(candidate["image_path"]), ctx.root / ".frame-selection").read_bytes()
                    if _frame_revision(candidate, content) != body.candidate_revision:
                        raise HTTPException(409, "AI 候选已更新，请刷新后重新裁剪")
                    timestamp, label = candidate.get("timestamp"), "AI 候选，经人工裁剪确认"
                elif body.source.startswith("candidate-"):
                    token = body.source[10:]
                    if len(token) != 32 or not all(ch in "0123456789abcdef" for ch in token):
                        raise HTTPException(422, "候选标识无效")
                    root = _candidate_root(ctx, folder)
                    record = read_object(inside(root / f"{token}.json", root), {})
                    if record.get("revision") != review_content_fingerprint(folder):
                        raise HTTPException(409, "菜谱或来源已变化，请重新生成候选")
                    content = inside(root / f"{token}.jpg", root).read_bytes()
                    timestamp, source_kind, label = record.get("timestamp"), record["source_kind"], record["label"]
                elif body.source.startswith("step-") and body.source[5:].isdigit():
                    step_index = int(body.source[5:])
                    steps = recipe.get("steps", [])
                    if step_index >= len(steps):
                        raise HTTPException(422, "步骤不存在")
                    path = _image_path(folder, steps[step_index].get("screenshot_path"))
                    if path is None:
                        raise HTTPException(404, "步骤图片不存在")
                    content, timestamp = path.read_bytes(), steps[step_index].get("screenshot_time")
                    source_kind, label = "step", f"第 {step_index + 1} 步"
                elif body.source == "cover":
                    path = _cover_path(folder, recipe)
                    if path is None:
                        raise HTTPException(404, "封面不存在")
                    content, timestamp = path.read_bytes(), recipe.get("cover_image_time")
                else:
                    raise HTTPException(422, "封面来源无效")
                with Image.open(io.BytesIO(content)) as image:
                    width, height = ImageOps.exif_transpose(image).size
                crop = body.crop or {"left": 0, "top": 0, "width": width, "height": height}
                if set(crop) != {"left", "top", "width", "height"} or crop["left"] < 0 or crop["top"] < 0 or min(crop["width"], crop["height"]) < 1 or crop["left"] + crop["width"] > width or crop["top"] + crop["height"] > height:
                    raise HTTPException(422, "裁剪范围超出图片")
                content = crop_screenshot_box_content(content, crop)
                save_recipe_cover_content(folder, content, timestamp=timestamp, status="manual_crop",
                                          source_kind=source_kind, source_label=label, source_url=recipe.get("source_url"),
                                          source_step_index=step_index, original_size={"width": width, "height": height}, crop_box=crop)
        warning = ctx.index_after_save()
        return {**detail(recipe_id), "sync_warning": warning}

    return router
