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


def _add_second_image(folder: Path) -> None:
    (folder / "images" / "step-2.jpg").write_bytes(b"second-image")
    recipe_path = folder / "recipe.json"
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    recipe["steps"].append(
        {
            "title": "翻炒",
            "start_time": 10,
            "action": "翻炒均匀",
            "screenshot_path": "images/step-2.jpg",
        }
    )
    recipe_path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")


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


def test_web_library_can_keep_only_first_image_per_recipe(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    folder = outputs / "demo"
    _write_recipe(folder)
    _add_second_image(folder)
    store = MobileSyncStore(tmp_path, out_dir=outputs)
    store.index_recipes()

    payload = build_web_library_payload(store, image_mode="first")
    recipe = payload["recipes"][0]

    assert payload["image_export_mode"] == "first"
    assert len(payload["assets"]) == 1
    assert len(recipe["assets"]) == 1
    assert recipe["steps"][0]["image_path"].startswith("asset:")
    assert "image_path" not in recipe["steps"][1]
    assert "image_sha256" not in recipe["steps"][1]
    assert "screenshot_path" not in recipe["steps"][1]


def test_web_library_can_export_text_without_image_data_or_references(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    folder = outputs / "demo"
    _write_recipe(folder)
    _add_second_image(folder)
    store = MobileSyncStore(tmp_path, out_dir=outputs)
    store.index_recipes()

    payload = build_web_library_payload(store, image_mode="none")
    recipe = payload["recipes"][0]

    assert payload["image_export_mode"] == "none"
    assert payload["assets"] == {}
    assert recipe["assets"] == []
    for step in recipe["steps"]:
        assert "image_path" not in step
        assert "image_sha256" not in step
        assert "screenshot_path" not in step
