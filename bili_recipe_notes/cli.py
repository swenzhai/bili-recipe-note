from __future__ import annotations

import argparse

from rich.console import Console

from .pipeline import RecipeJobOptions, extract_creator_links, generate_recipe_note

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
    parser.add_argument("--creator-home", action="store_true", help="Treat URL as creator homepage and extract all video links")
    parser.add_argument("--creator-links-file", default="creator_video_links.txt")
    return parser


def run(args: argparse.Namespace) -> int:
    def _log(message: str) -> None:
        console.print(message)

    if args.creator_home:
        extract_creator_links(
            url=args.url,
            cookies=args.cookies,
            out=args.out,
            filename=args.creator_links_file,
            log=_log,
        )
        return 0

    options = RecipeJobOptions(
        url=args.url,
        cookies=args.cookies,
        out=args.out,
        no_screenshot=args.no_screenshot,
        whisper_model=args.whisper_model,
        language=args.language,
        keep_media=args.keep_media,
        no_llm_summary=args.no_llm_summary,
    )
    result = generate_recipe_note(options, log=_log)
    console.print(f"[green]Done. Output saved to {result.output_folder}[/green]")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(args)
