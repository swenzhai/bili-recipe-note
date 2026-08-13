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


def build_web_library_payload(store: MobileSyncStore) -> dict[str, Any]:
    """Build the device-local PWA import document from an indexed mobile store."""

    recipes = store.list_recipes()
    assets: dict[str, str] = {}
    exported_recipes: list[dict[str, Any]] = []
    for source in recipes:
        recipe = json.loads(json.dumps(source, ensure_ascii=False))
        recipe_assets = recipe.get("assets") if isinstance(recipe.get("assets"), list) else []
        asset_keys: dict[str, str] = {}
        for metadata in recipe_assets:
            if not isinstance(metadata, dict):
                continue
            digest = str(metadata.get("sha256") or "").strip().lower()
            if not digest:
                continue
            found = store.asset_path(digest)
            if not found:
                continue
            path, mime_type = found
            key = f"asset:{digest}"
            if key not in assets:
                assets[key] = _asset_data_url(path, mime_type)
            asset_keys[digest] = key

        for step in recipe.get("steps") or []:
            if not isinstance(step, dict):
                continue
            digest = str(step.get("image_sha256") or "").strip().lower()
            if digest in asset_keys:
                step["image_path"] = asset_keys[digest]
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
) -> WebLibraryExportResult:
    store = MobileSyncStore(project_root, out_dir=out_dir)
    store.index_recipes()
    payload = build_web_library_payload(store)
    content = web_library_bytes(payload)
    path = atomic_write_bytes(Path(destination).expanduser(), content, backup=False).resolve()
    return WebLibraryExportResult(
        path=path,
        recipe_count=len(payload["recipes"]),
        asset_count=len(payload["assets"]),
        practice_log_count=len(payload["practice_logs"]),
        size_bytes=len(content),
    )
