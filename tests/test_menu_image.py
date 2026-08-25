from __future__ import annotations

import io
import zipfile
from pathlib import Path

from PIL import Image

from bili_recipe_notes.menu_image import MenuImageOptions, generate_menu_images, menu_category, menu_image_zip


def _recipe(
    title: str,
    *,
    category: str = "中餐",
    published: bool = True,
    recommended: bool = False,
    digest: str = "",
    cover_status: str | None = None,
) -> dict:
    return {
        "id": title,
        "title": title,
        "category": category,
        "published": published,
        "recommended": recommended,
        "ingredients": [],
        "steps": [{"image_sha256": digest}] if digest else [],
        "cover_image_status": cover_status,
        "cover_image_sha256": digest if cover_status else None,
    }


def test_menu_category_uses_customer_friendly_groups() -> None:
    assert menu_category(_recipe("风范汁焗大虾")) == "海鲜"
    assert menu_category(_recipe("干炒牛河", category="主食")) == "面条"
    assert menu_category(_recipe("港式萝卜焖牛腩")) == "肉类"
    assert menu_category(_recipe("清炒菜心")) == "素菜"
    assert menu_category(_recipe("杨枝甘露", category="饮品")) == "饮品"


def test_generate_share_menu_uses_only_published_recipes(tmp_path: Path) -> None:
    photo_path = tmp_path / "dish.jpg"
    Image.new("RGB", (640, 480), "#b95742").save(photo_path)
    recipes = [
        _recipe("推荐牛腩", recommended=True, digest="photo"),
        _recipe("下架菜", published=False),
        _recipe("杨枝甘露", category="饮品"),
    ]

    result = generate_menu_images(
        recipes,
        lambda digest: (photo_path, "image/jpeg") if digest == "photo" else None,
        MenuImageOptions(image_format="share"),
    )

    assert result.recipe_count == 2
    assert result.category_count == 2
    assert len(result.files) == 1
    assert result.files[0].name == "chef-zhai-menu-share.jpg"
    with Image.open(io.BytesIO(result.files[0].content)) as image:
        assert image.width == 1080
        assert image.height > 600
        assert image.format == "JPEG"


def test_generate_print_menu_paginates_and_zips() -> None:
    recipes = [_recipe(f"招牌菜 {index:02d}") for index in range(40)]
    result = generate_menu_images(
        recipes,
        lambda _digest: None,
        MenuImageOptions(image_format="print", include_photos=False),
    )

    assert result.recipe_count == 40
    assert len(result.files) >= 2
    assert all(file.width == 2480 and file.height == 3508 for file in result.files)
    with zipfile.ZipFile(io.BytesIO(menu_image_zip(result))) as archive:
        assert archive.namelist() == [file.name for file in result.files]


def test_menu_image_only_resolves_manually_approved_cover(tmp_path: Path) -> None:
    photo_path = tmp_path / "dish.jpg"
    Image.new("RGB", (640, 480), "#b95742").save(photo_path)
    requested: list[str] = []

    generate_menu_images(
        [
            _recipe("自动图", digest="auto", cover_status="auto_finished_dish"),
            _recipe("步骤图", digest="step"),
            _recipe("人工图", digest="manual", cover_status="manual_crop"),
        ],
        lambda digest: requested.append(digest) or (photo_path, "image/jpeg"),
        MenuImageOptions(image_format="share"),
    )

    assert requested == ["manual"]
