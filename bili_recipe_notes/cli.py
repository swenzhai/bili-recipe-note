from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console

from .pipeline import RecipeJobOptions, extract_creator_links, generate_recipe_note

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bili-recipe-notes", description="Generate personal recipe notes from Bilibili videos")
    parser.add_argument("url")
    parser.add_argument("--cookies", default=None)
    parser.add_argument("--out", default="outputs")
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--max-steps", type=int, default=10, choices=range(4, 13), metavar="4-12")
    parser.add_argument("--max-images", type=int, default=4, choices=range(1, 7), metavar="1-6")
    parser.add_argument("--review", action="store_true", help="Create recipe.review.json for item-by-item approval")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--keep-media", action="store_true")
    parser.add_argument("--no-llm-summary", action="store_true")
    parser.add_argument("--llm-provider", choices=["opencode", "codex", "openai", "local", "none"], default="opencode")
    parser.add_argument("--openai-model", default="gpt-5.5")
    parser.add_argument("--local-llm-command", default=None)
    parser.add_argument("--codex-model", default=None)
    parser.add_argument("--codex-profile", default=None)
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument(
        "--llm-extra-instructions",
        default=None,
        help="Trusted advanced instructions appended to OpenCode/Codex/local CLI prompts",
    )
    prompt_group.add_argument(
        "--llm-extra-instructions-file",
        default=None,
        help="UTF-8 file containing advanced CLI prompt instructions",
    )
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

    extra_instructions = getattr(args, "llm_extra_instructions", None)
    extra_file = getattr(args, "llm_extra_instructions_file", None)
    if extra_file:
        prompt_path = Path(extra_file)
        if not prompt_path.is_file():
            raise FileNotFoundError(f"LLM extra instructions file not found: {prompt_path}")
        extra_instructions = prompt_path.read_text(encoding="utf-8")

    options = RecipeJobOptions(
        url=args.url,
        cookies=args.cookies,
        out=args.out,
        no_screenshot=args.no_screenshot,
        whisper_model=args.whisper_model,
        language=args.language,
        keep_media=args.keep_media,
        no_llm_summary=args.no_llm_summary,
        llm_provider=getattr(args, "llm_provider", "opencode"),
        openai_model=getattr(args, "openai_model", "gpt-5.5"),
        local_llm_command=getattr(args, "local_llm_command", None),
        codex_model=getattr(args, "codex_model", None),
        codex_profile=getattr(args, "codex_profile", None),
        llm_cli_extra_instructions=extra_instructions,
        max_recipe_steps=getattr(args, "max_steps", 10),
        max_step_images=getattr(args, "max_images", 4),
        enable_recipe_review=getattr(args, "review", False),
    )
    result = generate_recipe_note(options, log=_log)
    console.print(f"[green]Done. Output saved to {result.output_folder}[/green]")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(args)
