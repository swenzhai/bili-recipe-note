from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from rich.console import Console

from .downloader import download_audio, download_lowres_video, download_subtitles, fetch_video_info
from .llm import normalize_markdown_image_paths, summarize_note_with_opencode
from .markdown_writer import render_markdown
from .recipe_extractor import TranscriptSegment, extract_recipe_rule_based
from .screenshot import capture_step_screenshots
from .subtitle import parse_subtitle_file
from .transcriber import transcribe_audio
from .utils import build_output_folder_name, ensure_dir

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bili-recipe-notes", description="Generate personal recipe notes from Bilibili videos")
    parser.add_argument("url")
    parser.add_argument("--cookies", default=None)
    parser.add_argument("--out", default="outputs")
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--keep-media", action="store_true")
    parser.add_argument("--no-llm-summary", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    info = fetch_video_info(args.url, cookies=args.cookies)
    title = info.get("title") or "untitled"
    folder_name = build_output_folder_name(title=title, uploader=info.get("uploader"))
    folder = ensure_dir(Path(args.out) / folder_name)
    media_dir = ensure_dir(folder / "media")

    metadata = {
        "source_url": args.url,
        "video_title": title,
        "uploader": info.get("uploader"),
        "bvid": info.get("id"),
        "duration": info.get("duration"),
    }

    subtitle_files: list[Path] = []
    try:
        subtitle_files = download_subtitles(args.url, media_dir, language=args.language, cookies=args.cookies)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Subtitle download failed, fallback to whisper transcription: {exc}[/yellow]")
    transcript: list[TranscriptSegment] = []

    if subtitle_files:
        console.print("[green]Using subtitle path[/green]")
        for sf in subtitle_files:
            try:
                transcript = parse_subtitle_file(sf)
                if transcript:
                    break
            except Exception as exc:  # noqa: BLE001
                console.print(f"[yellow]Subtitle parse failed {sf}: {exc}[/yellow]")

    if not transcript:
        console.print("[yellow]No subtitles found, fallback to whisper transcription[/yellow]")
        audio = download_audio(args.url, media_dir, cookies=args.cookies)
        transcript = transcribe_audio(audio, model_size=args.whisper_model, language=args.language)

    recipe = extract_recipe_rule_based(transcript, metadata)

    if not args.no_screenshot and recipe.steps:
        try:
            video = download_lowres_video(args.url, media_dir, cookies=args.cookies)
            capture_step_screenshots(video, recipe.steps, folder / "images")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Video download/screenshot skipped: {exc}[/yellow]")

    note_markdown = render_markdown(recipe)
    (folder / "transcript.json").write_text(
        json.dumps([seg.model_dump() for seg in transcript], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (folder / "recipe.json").write_text(recipe.model_dump_json(indent=2), encoding="utf-8")
    final_note = normalize_markdown_image_paths(note_markdown)

    if not args.no_llm_summary:
        llm_summary = summarize_note_with_opencode(note_markdown)
        if llm_summary:
            final_note = normalize_markdown_image_paths(llm_summary).rstrip() + "\n"
        else:
            console.print("[yellow]LLM summary skipped: opencode unavailable or failed[/yellow]")

    (folder / "note.md").write_text(final_note, encoding="utf-8")

    if not args.keep_media:
        shutil.rmtree(media_dir, ignore_errors=True)

    console.print(f"[green]Done. Output saved to {folder}[/green]")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(args)
