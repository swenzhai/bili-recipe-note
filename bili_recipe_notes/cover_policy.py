from __future__ import annotations

from typing import Any

MANUALLY_APPROVED_COVER_STATUSES = frozenset(
    {"manual_video", "manual_step", "manual_timestamp", "manual_crop", "uploaded"}
)


def has_manually_approved_cover(recipe: dict[str, Any]) -> bool:
    return str(recipe.get("cover_image_status") or "").strip() in MANUALLY_APPROVED_COVER_STATUSES
