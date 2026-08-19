from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mobile_sync import MobileSyncStore
from .storage import atomic_write_bytes


WEB_LIBRARY_SCHEMA_VERSION = 1
DEFAULT_WEB_LIBRARY_NAME = "bili-recipe-web-library.json"
WEB_IMAGE_MODES = ("all", "first", "none")


@dataclass(frozen=True)
class WebLibraryExportResult:
    path: Path
    recipe_count: int
    asset_count: int
    practice_log_count: int
    size_bytes: int


def _asset_data_url(path: Path, mime_type: str) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _clear_step_image(step: dict[str, Any]) -> None:
    for key in (
        "image_path",
        "image_sha256",
        "screenshot_path",
        "screenshot_time",
        "screenshot_status",
        "screenshot_score",
    ):
        step.pop(key, None)


def build_web_library_payload(store: MobileSyncStore, *, image_mode: str = "all") -> dict[str, Any]:
    """Build the device-local PWA import document from an indexed mobile store."""

    if image_mode not in WEB_IMAGE_MODES:
        raise ValueError(f"unsupported web image mode: {image_mode}")

    recipes = store.list_recipes()
    assets: dict[str, str] = {}
    exported_recipes: list[dict[str, Any]] = []
    for source in recipes:
        recipe = json.loads(json.dumps(source, ensure_ascii=False))
        recipe_assets = recipe.get("assets") if isinstance(recipe.get("assets"), list) else []
        allowed_digests: set[str] | None = None
        if image_mode == "none":
            allowed_digests = set()
        elif image_mode == "first":
            first_digest = next(
                (
                    str(step.get("image_sha256") or "").strip().lower()
                    for step in recipe.get("steps") or []
                    if isinstance(step, dict)
                    and str(step.get("image_sha256") or "").strip()
                    and store.asset_path(str(step.get("image_sha256") or "").strip().lower())
                ),
                "",
            )
            allowed_digests = {first_digest} if first_digest else set()
        asset_keys: dict[str, str] = {}
        for metadata in recipe_assets:
            if not isinstance(metadata, dict):
                continue
            digest = str(metadata.get("sha256") or "").strip().lower()
            if not digest or (allowed_digests is not None and digest not in allowed_digests):
                continue
            found = store.asset_path(digest)
            if not found:
                continue
            path, mime_type = found
            key = f"asset:{digest}"
            if key not in assets:
                assets[key] = _asset_data_url(path, mime_type)
            asset_keys[digest] = key

        exported_asset_metadata = []
        for metadata in recipe_assets:
            if not isinstance(metadata, dict):
                continue
            digest = str(metadata.get("sha256") or "").strip().lower()
            if digest in asset_keys:
                exported_asset_metadata.append(metadata)
        recipe["assets"] = exported_asset_metadata

        attached_first_image = False
        for step in recipe.get("steps") or []:
            if not isinstance(step, dict):
                continue
            digest = str(step.get("image_sha256") or "").strip().lower()
            if digest in asset_keys and (image_mode != "first" or not attached_first_image):
                step["image_path"] = asset_keys[digest]
                attached_first_image = True
            else:
                _clear_step_image(step)
        exported_recipes.append(recipe)

    practice_logs = [
        {
            "id": str(item["id"]),
            "recipeId": str(item["recipe_id"]),
            "cookedOn": str(item["cooked_on"]),
            "notes": str(item.get("notes") or ""),
            **({"rating": int(item["rating"])} if item.get("rating") is not None else {}),
        }
        for item in store.list_practice_logs()
    ]
    return {
        "schema_version": WEB_LIBRARY_SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "generator": "bili-recipe-notes",
        "image_export_mode": image_mode,
        "recipes": exported_recipes,
        "assets": assets,
        "practice_logs": practice_logs,
    }


def web_library_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def export_web_library(
    project_root: str | Path,
    out_dir: str | Path,
    destination: str | Path,
    *,
    image_mode: str = "all",
) -> WebLibraryExportResult:
    store = MobileSyncStore(project_root, out_dir=out_dir)
    store.index_recipes()
    payload = build_web_library_payload(store, image_mode=image_mode)
    content = web_library_bytes(payload)
    path = atomic_write_bytes(Path(destination).expanduser(), content, backup=False).resolve()
    return WebLibraryExportResult(
        path=path,
        recipe_count=len(payload["recipes"]),
        asset_count=len(payload["assets"]),
        practice_log_count=len(payload["practice_logs"]),
        size_bytes=len(content),
    )
