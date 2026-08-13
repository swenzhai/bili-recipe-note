from __future__ import annotations

import base64
import json
from pathlib import Path

from bili_recipe_notes.mobile_sync import MobileSyncStore
from bili_recipe_notes.web_export import build_web_library_payload, export_web_library


def _write_recipe(folder: Path) -> None:
    (folder / "images").mkdir(parents=True)
    (folder / "images" / "step.jpg").write_bytes(b"offline-image")
    (folder / "recipe.json").write_text(
        json.dumps(
            {
                "title": "离线测试菜",
                "source_url": "https://www.bilibili.com/video/BV1offline",
                "ingredients": [{"name": "土豆", "amount": "2个"}],
                "seasonings": [],
                "tools": [],
                "steps": [
                    {
                        "title": "切配",
                        "start_time": 0,
                        "action": "切成小块",
                        "screenshot_path": "images/step.jpg",
                    }
                ],
                "summary_tips": [],
                "uncertain_points": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_build_web_library_embeds_recipe_images(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    _write_recipe(outputs / "demo")
    store = MobileSyncStore(tmp_path, out_dir=outputs)
    assert store.index_recipes()["indexed"] == 1

    payload = build_web_library_payload(store)

    assert payload["schema_version"] == 1
    assert len(payload["recipes"]) == 1
    image_key = payload["recipes"][0]["steps"][0]["image_path"]
    assert image_key.startswith("asset:")
    prefix, encoded = payload["assets"][image_key].split(",", 1)
    assert prefix == "data:image/jpeg;base64"
    assert base64.b64decode(encoded) == b"offline-image"


def test_export_web_library_writes_importable_json(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    _write_recipe(outputs / "demo")
    destination = tmp_path / "transfer" / "library.json"

    result = export_web_library(tmp_path, outputs, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))

    assert result.path == destination.resolve()
    assert result.recipe_count == 1
    assert result.asset_count == 1
    assert result.size_bytes == destination.stat().st_size
    assert payload["recipes"][0]["title"] == "离线测试菜"
