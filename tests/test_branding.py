from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from bili_recipe_notes.branding import branding_payload, configured_logo_path, save_logo
from bili_recipe_notes.config import UIConfig, load_config, save_config


def _image_bytes(image_format: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (2000, 1000), (180, 40, 30, 128)).save(buffer, image_format)
    return buffer.getvalue()


def test_branding_defaults_remain_compatible_with_old_config(tmp_path):
    path = save_config(UIConfig(out_dir="recipes"), tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in ("restaurant_name", "restaurant_subtitle", "restaurant_logo_path"):
        raw.pop(key)
    path.write_text(json.dumps(raw), encoding="utf-8")

    config = load_config(tmp_path)

    assert config.restaurant_name == "Chef Zhai"
    assert branding_payload(config, tmp_path)["has_logo"] is False


def test_save_logo_normalizes_and_limits_dimensions(tmp_path):
    path = save_logo(_image_bytes(), tmp_path)
    config = UIConfig(restaurant_logo_path="logo.png")

    assert path == configured_logo_path(config, tmp_path)
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert max(image.size) == 1600


def test_invalid_logo_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="有效的图片"):
        save_logo(b"not an image", tmp_path)


def test_logo_path_cannot_escape_branding_directory(tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(_image_bytes())

    assert configured_logo_path(UIConfig(restaurant_logo_path="../../outside.png"), tmp_path) is None
