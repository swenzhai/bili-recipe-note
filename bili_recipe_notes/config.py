from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .storage import CorruptDataError, atomic_write_json, file_lock, read_json

CONFIG_DIR_NAME = ".bili-recipe-notes"
CONFIG_FILE_NAME = "config.json"


@dataclass
class UIConfig:
    out_dir: str = "outputs"
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
    max_step_images: int = 4
    enable_recipe_review: bool = False
    obsidian_vault_dir: str = "obsidian-vault"
    auto_archive_after_generation: bool = False
    archive_knowledge_with_recipe: bool = True


def config_path(project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    return root / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def load_config(project_root: Path | None = None) -> UIConfig:
    path = config_path(project_root)
    if not path.exists():
        return UIConfig()
    raw = read_json(path, expected_type=dict)

    defaults = asdict(UIConfig())
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
    return UIConfig(**cleaned)


def save_config(config: UIConfig, project_root: Path | None = None) -> Path:
    path = config_path(project_root)
    with file_lock(path):
        if path.exists():
            load_config(project_root)
        atomic_write_json(path, asdict(config))
    return path
