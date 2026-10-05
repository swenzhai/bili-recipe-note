"""Independent review operations; never apply edits to generated recipes."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .admin_common import AdminContext
from .storage import file_lock
from .review_policy import (complete_manual_ai_review, get_review_state, list_review_history,
                            review_content_fingerprint, set_ai_review_classification,
                            set_ai_review_item_decision, set_ai_review_visibility)


class ReviewDecision(BaseModel):
    action: Literal["classification", "visibility", "item", "complete"]
    content_fingerprint: str
    classification: Literal["recipe", "technique", "non_recipe", "uncertain"] | None = None
    visibility: Literal["visible", "hidden"] | None = None
    item_id: str = ""
    verdict: Literal["supported", "needs_review"] = "needs_review"
    comment: str = Field(default="", max_length=10000)
    evidence: list[dict] = Field(default_factory=list, max_length=100)


def create_router(ctx: AdminContext) -> APIRouter:
    router = APIRouter()

    @router.get("/recipes/{recipe_id}/review-history")
    def history(recipe_id: str):
        return {"items": list_review_history(ctx.folder(recipe_id))}

    @router.post("/recipes/{recipe_id}/review")
    def decision(recipe_id: str, body: ReviewDecision):
        folder = ctx.folder(recipe_id)
        with file_lock(folder / "recipe.json"):
            if not body.content_fingerprint or body.content_fingerprint != review_content_fingerprint(folder):
                raise HTTPException(409, "菜谱或来源内容已变化，请刷新后重新核对")
            if body.action == "classification":
                set_ai_review_classification(folder, body.classification, comment=body.comment)
            elif body.action == "visibility":
                set_ai_review_visibility(folder, body.visibility)
            elif body.action == "item":
                set_ai_review_item_decision(folder, body.item_id, body.verdict, comment=body.comment,
                                           evidence=body.evidence, expected_content_fingerprint=body.content_fingerprint)
            else:
                complete_manual_ai_review(folder, expected_content_fingerprint=body.content_fingerprint)
        warning = ctx.index_after_save()
        return {"review": get_review_state(folder), "sync_warning": warning,
                "visibility": ctx.store.recipe_visibility_states([folder]).get(str(folder), {})}

    return router
