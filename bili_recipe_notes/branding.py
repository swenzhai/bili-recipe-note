from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .config import CONFIG_DIR_NAME, UIConfig
from .storage import atomic_write_bytes

BRANDING_DIR_NAME = "branding"
LOGO_FILE_NAME = "logo.png"
MAX_LOGO_BYTES = 5 * 1024 * 1024
MAX_LOGO_EDGE = 1600
SUPPORTED_LOGO_TYPES = {"image/png", "image/jpeg", "image/webp"}


def branding_dir(project_root: str | Path | None = None) -> Path:
    root = Path(project_root or Path.cwd()).resolve()
    return root / CONFIG_DIR_NAME / BRANDING_DIR_NAME


def default_logo_path(project_root: str | Path | None = None) -> Path:
    return branding_dir(project_root) / LOGO_FILE_NAME


def _safe_configured_logo(config: UIConfig, project_root: str | Path | None = None) -> Path | None:
    root = Path(project_root or Path.cwd()).resolve()
    directory = branding_dir(root).resolve()
    configured = config.restaurant_logo_path
    if not configured:
        candidate = default_logo_path(root)
    elif configured.replace("\\", "/").startswith(f"{BRANDING_DIR_NAME}/"):
        candidate = (root / CONFIG_DIR_NAME / configured).resolve()
    else:
        candidate = (directory / configured).resolve()
    try:
        candidate.relative_to(directory)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def configured_logo_path(config: UIConfig, project_root: str | Path | None = None) -> Path | None:
    return _safe_configured_logo(config, project_root)


def branding_payload(config: UIConfig, project_root: str | Path | None = None, *, logo_url: str | None = None) -> dict[str, Any]:
    logo = _safe_configured_logo(config, project_root)
    return {
        "name": config.restaurant_name.strip() or "Chef Zhai",
        "subtitle": config.restaurant_subtitle.strip() or "家庭厨房",
        "has_logo": logo is not None,
        "logo_url": logo_url if logo is not None else None,
    }


def save_logo(content: bytes, project_root: str | Path | None = None) -> Path:
    if len(content) > MAX_LOGO_BYTES:
        raise ValueError("Logo 文件不能超过 5 MiB")
    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Logo 仅支持 PNG、JPG 或 WebP")
            source.load()
            image = source.convert("RGBA")
            image.thumbnail((MAX_LOGO_EDGE, MAX_LOGO_EDGE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Logo 不是有效的图片文件") from exc
    if output.tell() > MAX_LOGO_BYTES:
        raise ValueError("Logo 压缩后仍超过 5 MiB")
    return atomic_write_bytes(default_logo_path(project_root), output.getvalue())


def remove_logo(project_root: str | Path | None = None) -> bool:
    path = default_logo_path(project_root)
    if not path.exists():
        return False
    path.unlink()
    return True
