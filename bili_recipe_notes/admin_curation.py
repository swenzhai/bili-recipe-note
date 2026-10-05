"""Read-only recipe comparisons, independent curation decisions and archive lists."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .admin_common import AdminContext, inside, read_object, revision
from .curation import (
    CURATION_DECISION_VALUES, CURATION_REVIEW_FILE, DEFAULT_CURATION_REVIEW_DIR,
    build_curation_review, curation_decision_conflicts, load_curation_decisions,
    load_curation_review, save_curation_decision,
)
from .obsidian_archive import load_archive_manifest, recipe_archive_status
from .output_layout import iter_output_folders
from .storage import file_lock


class DecisionInput(BaseModel):
    item_id: str = Field(min_length=1, max_length=500)
    decision: Literal["pending", "keep_primary", "keep_variant", "merge_clip", "exclude", "review"]
    final_title: str = Field(default="", max_length=500)
    variant_name: str = Field(default="", max_length=500)
    review_notes: str = Field(default="", max_length=10000)
    revision: str = Field(min_length=1)
    content_revision: str = Field(min_length=1)


class ArchiveItem(BaseModel):
    id: str
    revision: str


class ArchiveInput(BaseModel):
    items: list[ArchiveItem] = Field(min_length=1, max_length=200)


def _content_revision(folder: Path) -> str:
    value = hashlib.sha256()
    for name in ("recipe.json", "source.json", "transcript.json"):
        path = inside(folder / name, folder)
        value.update(name.encode())
        value.update(path.read_bytes() if path.is_file() else b"missing")
    return value.hexdigest()


def create_router(ctx: AdminContext) -> APIRouter:
    router = APIRouter()
    directory = inside(ctx.root / DEFAULT_CURATION_REVIEW_DIR, ctx.root)

    def report():
        if not (directory / CURATION_REVIEW_FILE).is_file():
            return {"groups": [], "generated_at": None}
        return load_curation_review(directory)

    def state_revision():
        digest = hashlib.sha256()
        for name in (CURATION_REVIEW_FILE, "curation-decisions.json"):
            path = inside(directory / name, directory)
            digest.update(path.read_bytes() if path.is_file() else b"missing")
        return digest.hexdigest()

    def item_rows():
        return {str(Path(row["output_folder"]).resolve()): row for row in ctx.rows()}

    def enriched(item, decisions, rows, *, details=False):
        folder = inside(Path(str(item.get("output_folder") or "")), ctx.root)
        row = rows.get(str(folder))
        saved = decisions.get("items", {}).get(str(item.get("item_id")), {})
        result = {key: value for key, value in item.items() if key != "output_folder"}
        result.update({"recipe_id": row["id"] if row else None, "saved": saved,
                       "decision": saved.get("decision", "pending")})
        if details:
            result["recipe"] = read_object(inside(folder / "recipe.json", folder), {})
            result["transcript"] = read_object(inside(folder / "transcript.json", folder), [])
            result["content_revision"] = _content_revision(folder)
        return result

    @router.get("/curation")
    def listing(q: str = "", status: str = "all"):
        current = report()
        decisions = load_curation_decisions(directory)
        rows = item_rows()
        groups = []
        total = completed = 0
        for group in current["groups"]:
            items = [enriched(item, decisions, rows) for item in group.get("items", [])]
            done = sum(item["decision"] in {"keep_primary", "keep_variant", "merge_clip", "exclude"} for item in items)
            total += len(items)
            completed += done
            searchable = " ".join([group["title"], *(str(item.get("video_title", "")) + " " + str(item.get("bvid", "")) for item in items)]).casefold()
            if q.casefold() not in searchable:
                continue
            if status == "pending" and done == len(items):
                continue
            if status == "completed" and done != len(items):
                continue
            groups.append({"title": group["title"], "similar_titles": group.get("similar_titles", []),
                           "total": len(items), "completed": done, "items": items})
        return {"groups": groups, "total": total, "completed": completed,
                "generated_at": current.get("generated_at"), "revision": state_revision(),
                "conflicts": curation_decision_conflicts(current, decisions)}

    @router.post("/curation/scan")
    def scan():
        with file_lock(directory / ".admin.lock"):
            # The existing report builder writes only its own JSON/CSV reports.
            # Decisions remain in a separate file and survive every rescan.
            load_curation_decisions(directory)
            for folder in iter_output_folders(ctx.root):
                inside(folder, ctx.root)
                for name in ("recipe.json", "source.json", "transcript.json", "quality.json", "job.json"):
                    inside(folder / name, folder)
            build_curation_review(ctx.root, directory)
        return listing()

    @router.get("/curation/group")
    def group(title: str):
        current = report()
        selected = next((item for item in current["groups"] if item["title"] == title), None)
        if selected is None:
            raise HTTPException(404, "菜名组不存在，请更新整理清单")
        decisions = load_curation_decisions(directory)
        rows = item_rows()
        names = {title, *selected.get("similar_titles", [])}
        items = [enriched(item, decisions, rows, details=True)
                 for candidate in current["groups"] if candidate["title"] in names
                 for item in candidate.get("items", [])]
        return {"title": title, "similar_titles": selected.get("similar_titles", []),
                "items": items, "revision": state_revision()}

    @router.post("/curation/decisions")
    def decide(payload: DecisionInput):
        if payload.decision not in CURATION_DECISION_VALUES:
            raise HTTPException(422, "不支持的整理决定")
        with file_lock(directory / ".admin.lock"):
            if payload.revision != state_revision():
                raise HTTPException(409, "整理清单或人工决定已变化，请重新加载后保存")
            current = report()
            item = next((item for group in current["groups"] for item in group.get("items", [])
                         if item.get("item_id") == payload.item_id), None)
            if item is None:
                raise HTTPException(404, "该来源不在当前整理清单中")
            folder = inside(Path(item["output_folder"]), ctx.root)
            if payload.content_revision != _content_revision(folder):
                raise HTTPException(409, "菜谱或字幕来源已变化，请重新查看后确认")
            save_curation_decision(directory, payload.item_id, decision=payload.decision,
                                   final_title=payload.final_title, variant_name=payload.variant_name,
                                   review_notes=payload.review_notes)
        return group(item["group_title"])

    @router.get("/curation/export")
    def export(kind: Literal["csv", "decisions"] = "csv"):
        if kind == "decisions":
            return JSONResponse(load_curation_decisions(directory), headers={"Content-Disposition": 'attachment; filename="curation-decisions.json"'})
        path = inside(directory / "recipe-review.csv", directory)
        if not path.is_file():
            raise HTTPException(404, "请先生成整理清单")
        return FileResponse(path, media_type="text/csv", filename=path.name)

    @router.get("/drafts")
    def drafts(q: str = "", status: str = "all", page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100)):
        values = []
        counts = {key: 0 for key in ("draft", "archived", "stale", "archive_error")}
        for row in ctx.rows():
            folder = inside(Path(row["output_folder"]), ctx.root)
            for name in ("recipe.json", "note.md", "archive.json"):
                inside(folder / name, folder)
            workflow_status = recipe_archive_status(folder)
            counts[workflow_status] += 1
            if status != "all" and workflow_status != status:
                continue
            if q.casefold() not in f'{row.get("title", "")} {row.get("creator_name", "")} {row.get("category", "")}'.casefold():
                continue
            values.append((row, folder, workflow_status))
        items = []
        for row, folder, workflow_status in values[(page - 1) * page_size:page * page_size]:
            try:
                manifest = load_archive_manifest(folder) or {}
            except (ValueError, RuntimeError, OSError):
                manifest = {}
            items.append({"id": row["id"], "title": row.get("title"), "creator_name": row.get("creator_name"),
                          "category": row.get("category"), "archive_status": workflow_status,
                          "archive_note_path": manifest.get("note_path"), "archived_at": manifest.get("archived_at"),
                          "error": manifest.get("error"), "revision": revision(folder)})
        return {"items": items, "total": len(values), "counts": counts, "page": page,
                "page_size": page_size, "vault": ctx.config().obsidian_vault_dir}

    @router.post("/drafts/archive")
    def archive(payload: ArchiveInput):
        from .admin_settings import start_settings_operation

        seen = set()
        items = []
        for item in payload.items:
            if item.id in seen:
                continue
            seen.add(item.id)
            folder = ctx.folder(item.id)
            ctx.check_revision(folder, item.revision)
            items.append({"recipe_id": item.id, "revision": item.revision})
        return start_settings_operation(ctx, "recipe_archive", {}, items=items)

    return router
