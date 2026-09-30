from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .data_paths import ensure_data_layout, exports_dir, recipes_dir, relocate_value, state_dir
from .storage import CorruptDataError, atomic_write_json, file_lock, read_json

# Kept for recognizing legacy migration/archive paths only.
CONFIG_DIR_NAME = ".bili-recipe-notes"
CONFIG_FILE_NAME = "config.json"


@dataclass
class UIConfig:
    out_dir: str = field(default_factory=lambda: str(recipes_dir()))
    cookies: str | None = None
    language: str = "zh"
    whisper_model: str = "small"
    enable_screenshot: bool = True
    enable_llm_summary: bool = True
    keep_media: bool = False
    llm_provider: str = "opencode"
    openai_model: str = "gpt-5.5"
    local_llm_command: str | None = None
    codex_model: str | None = None
    codex_profile: str | None = None
    llm_cli_extra_instructions: str | None = None
    max_recipe_steps: int = 10
    max_step_images: int = 3
    enable_recipe_review: bool = False
    obsidian_vault_dir: str = field(default_factory=lambda: str(exports_dir() / "obsidian-vault"))
    auto_archive_after_generation: bool = False
    archive_knowledge_with_recipe: bool = True
    restaurant_name: str = "Chef Zhai"
    restaurant_subtitle: str = "家庭厨房"
    restaurant_logo_path: str | None = None


def config_path(project_root: Path | None = None) -> Path:
    return state_dir(project_root) / CONFIG_FILE_NAME


def load_config(project_root: Path | None = None) -> UIConfig:
    path = config_path(project_root)
    default_config = UIConfig(
        out_dir=str(recipes_dir(project_root)),
        obsidian_vault_dir=str(exports_dir(project_root) / "obsidian-vault"),
    )
    if not path.exists():
        return default_config
    raw = read_json(path, expected_type=dict)

    defaults = asdict(default_config)
    cleaned: dict[str, Any] = {}
    for key, default in defaults.items():
        value = raw.get(key, default)
        valid = isinstance(value, type(default)) if default is not None else value is None or isinstance(value, str)
        if not valid:
            raise CorruptDataError(
                f"Invalid value for {key!r} in {path}: expected {type(default).__name__}, "
                f"got {type(value).__name__}."
            )
        cleaned[key] = value
    for key in ("out_dir", "cookies", "obsidian_vault_dir", "restaurant_logo_path"):
        cleaned[key] = relocate_value(cleaned[key], project_root)
    return UIConfig(**cleaned)


def save_config(config: UIConfig, project_root: Path | None = None) -> Path:
    ensure_data_layout(project_root)
    path = config_path(project_root)
    with file_lock(path):
        if path.exists():
            load_config(project_root)
        atomic_write_json(path, asdict(config))
    return path
