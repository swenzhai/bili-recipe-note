from __future__ import annotations

import importlib.util
import inspect
import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class EnvironmentCheck:
    name: str
    ok: bool
    detail: str
    suggestion: str | None = None


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _yt_dlp_bilibili_ready() -> tuple[bool, str]:
    try:
        import yt_dlp.version
        from yt_dlp.extractor.bilibili import BiliBiliIE

        source = inspect.getsource(BiliBiliIE._download_playinfo)
        return "_dm_params" in source, yt_dlp.version.__version__
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _windows_launcher_command(command: str) -> str | None:
    if os.name == "nt":
        for name in (f"{command}.cmd", f"{command}.exe", f"{command}.bat", command):
            path = shutil.which(name)
            if path:
                return path
    return shutil.which(command)


def _cli_help_ready(command: str, args: list[str]) -> tuple[bool, str]:
    command_path = _windows_launcher_command(command)
    if not command_path:
        return False, "not found in PATH"
    try:
        subprocess.run(
            [command_path, *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, command_path


def _codex_cli_ready() -> tuple[bool, str]:
    return _cli_help_ready("codex", ["exec", "--help"])


def _opencode_cli_ready() -> tuple[bool, str]:
    return _cli_help_ready("opencode", ["run", "--help"])


def run_environment_checks() -> list[EnvironmentCheck]:
    checks: list[EnvironmentCheck] = []

    for module in ["yt_dlp", "pydantic", "rich", "streamlit"]:
        checks.append(
            EnvironmentCheck(
                name=f"Python package: {module}",
                ok=_module_available(module),
                detail="installed" if _module_available(module) else "missing",
                suggestion="Run: python -m pip install -r requirements.txt",
            )
        )

    whisper_ok = _module_available("faster_whisper")
    checks.append(
        EnvironmentCheck(
            name="Whisper transcription",
            ok=whisper_ok,
            detail="faster-whisper installed" if whisper_ok else "faster-whisper missing",
            suggestion="Run: python -m pip install -r requirements.txt",
        )
    )

    ffmpeg_path = shutil.which("ffmpeg")
    checks.append(
        EnvironmentCheck(
            name="ffmpeg",
            ok=bool(ffmpeg_path),
            detail=ffmpeg_path or "not found in PATH",
            suggestion="Install ffmpeg, then restart the UI.",
        )
    )

    opencode_ready, opencode_detail = _opencode_cli_ready()
    checks.append(
        EnvironmentCheck(
            name="opencode",
            ok=opencode_ready,
            detail=opencode_detail,
            suggestion="Install opencode or disable LLM rewrite.",
        )
    )

    codex_ready, codex_detail = _codex_cli_ready()
    checks.append(
        EnvironmentCheck(
            name="codex CLI",
            ok=codex_ready,
            detail=codex_detail,
            suggestion="Install and log in to Codex CLI, or choose another LLM provider.",
        )
    )

    ready, detail = _yt_dlp_bilibili_ready()
    checks.append(
        EnvironmentCheck(
            name="yt-dlp Bilibili support",
            ok=ready,
            detail=detail,
            suggestion="Run the start-ui script again or reinstall requirements to update yt-dlp.",
        )
    )

    return checks
