"""Read-only eligibility for the app's default finished-dish frame search."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .frame_selection import DEFAULT_REQUIREMENT
from .review_policy import (
    ai_review_is_hidden,
    load_ai_review,
    load_review_record,
    resolve_recipe_visibility,
)


def frame_selection_eligibility(
    folder: str | Path,
    requirement: str | None = None,
    visibility: Mapping | None = None,
) -> str | None:
    """Return a skip reason for confirmed non-dishes, without changing display.

    ``visibility`` must be a current result from ``recipe_visibility_states`` or
    ``review_policy``. Custom requests can target tools or technique demonstrations.
    A provisional or stale AI classification never excludes an otherwise visible
    item from default selection.
    """
    if requirement is not None and requirement.strip() != DEFAULT_REQUIREMENT:
        return None
    state = visibility if visibility is not None else resolve_recipe_visibility(folder)
    classification = state.get("classification")
    if classification not in {"technique", "non_recipe"}:
        return None
    record = load_review_record(folder) or {}
    if record.get("classification_override") == classification:
        return "not_a_dish"
    # With no valid AI classification the unified policy can supply a non-dish
    # classification only from an existing manual/source decision.
    if not state.get("ai_valid") or state.get("reason_code") == "source_classification":
        return "not_a_dish"
    review = load_ai_review(folder)
    if review and review.get("classification") == classification:
        # Reuse the established confirmation threshold. A manual display restore
        # keeps the content visible but does not turn a technique into a dish.
        confirmed = {**review, "visibility_override": None}
        if ai_review_is_hidden(confirmed):
            return "not_a_dish"
    return None
