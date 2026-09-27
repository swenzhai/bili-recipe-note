"""Visibility policy for AI reviews of generated recipes.

Reviews are sidecars bound to the exact recipe bytes. Missing, malformed, or
outdated reviews have no effect on the existing recipe library.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import atomic_write_json, file_lock

AI_REVIEW_FILE_NAME = "recipe.ai-review.json"
AI_REVIEW_SCHEMA_VERSION = 1
AI_REVIEW_CLASSIFICATIONS = frozenset({"recipe", "non_recipe", "technique", "uncertain"})
AI_REVIEW_STATUSES = frozenset({"completed", "needs_review"})


def _valid_evidence(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            return False
        if "start" in item:
            start = item["start"]
            if type(start) not in (int, float) or start < 0 or not math.isfinite(start):
                return False
    return True


def review_recipe_hash(output_folder: str | Path) -> str:
    return hashlib.sha256((Path(output_folder) / "recipe.json").read_bytes()).hexdigest()


def load_ai_review(output_folder: str | Path) -> dict[str, Any] | None:
    """Return a valid review for the current recipe, without modifying any file."""

    folder = Path(output_folder)
    try:
        review = json.loads((folder / AI_REVIEW_FILE_NAME).read_text(encoding="utf-8"))
        if not isinstance(review, dict):
            return None
        if type(review.get("schema_version")) is not int or review["schema_version"] != AI_REVIEW_SCHEMA_VERSION:
            return None
        if review.get("status") not in AI_REVIEW_STATUSES:
            return None
        if review.get("classification") not in AI_REVIEW_CLASSIFICATIONS:
            return None
        if review.get("visibility") not in {"visible", "hidden"}:
            return None
        if review.get("visibility_override") not in {None, "visible", "hidden"}:
            return None
        confidence = review.get("confidence")
        if type(confidence) not in (int, float) or not 0 <= confidence <= 1 or not math.isfinite(confidence):
            return None
        tags = review.get("tags")
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            return None
        if "reason" in review and not isinstance(review["reason"], str):
            return None
        if "evidence" in review and not _valid_evidence(review["evidence"]):
            return None
        if "item_checks" in review:
            checks = review["item_checks"]
            if not isinstance(checks, list):
                return None
            seen: set[str] = set()
            for item in checks:
                if not isinstance(item, dict):
                    return None
                item_id = item.get("id")
                if not isinstance(item_id, str) or not re.fullmatch(r"(?:ingredients|seasonings|steps):\d+", item_id):
                    return None
                if item_id in seen or item.get("verdict") not in {"supported", "needs_review"}:
                    return None
                seen.add(item_id)
                if not isinstance(item.get("reason"), str):
                    return None
                if "evidence" in item and not _valid_evidence(item["evidence"]):
                    return None
        if review.get("source_recipe_sha256") != review_recipe_hash(folder):
            return None
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
        return None
    return review


def ai_review_is_hidden(review: dict[str, Any] | None) -> bool:
    """Apply manual visibility first, then the conservative AI hiding rule."""

    if review is None:
        return False
    override = review.get("visibility_override")
    if override is not None:
        return override == "hidden"
    return bool(
        review.get("status") == "completed"
        and review.get("classification") in {"non_recipe", "technique"}
        and review.get("confidence", 0) >= 0.9
        and review.get("visibility") == "hidden"
    )


def is_recipe_hidden(output_folder: str | Path) -> bool:
    return ai_review_is_hidden(load_ai_review(output_folder))


def set_ai_review_visibility(output_folder: str | Path, visibility: str | None) -> dict[str, Any]:
    """Record an explicit human override in the sidecar, keeping the AI result."""

    if visibility not in {None, "visible", "hidden"}:
        raise ValueError("展示状态必须是 visible、hidden 或 None")
    folder = Path(output_folder)
    path = folder / AI_REVIEW_FILE_NAME
    with file_lock(path):
        review = load_ai_review(folder)
        if review is None:
            raise ValueError("当前菜谱没有有效的 AI 审核结果，请先重新审核")
        review["visibility_override"] = visibility
        review["override_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(path, review, backup=False)
    return review
