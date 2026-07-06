from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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


def config_path(project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    return root / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def load_config(project_root: Path | None = None) -> UIConfig:
    path = config_path(project_root)
    if not path.exists():
        return UIConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return UIConfig()
    if not isinstance(raw, dict):
        return UIConfig()

    defaults = asdict(UIConfig())
    cleaned: dict[str, Any] = {key: raw.get(key, value) for key, value in defaults.items()}
    return UIConfig(**cleaned)


def save_config(config: UIConfig, project_root: Path | None = None) -> Path:
    path = config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
