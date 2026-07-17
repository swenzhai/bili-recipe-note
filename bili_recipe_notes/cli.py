from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from rich.console import Console

from .batch_queue import (
    BatchQueueState,
    create_batch_id,
    create_batch_state,
    list_batch_states,
    load_batch_state,
)
from .pipeline import BatchJobOptions, RecipeJobOptions, extract_creator_links, generate_recipe_note, run_batch

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bili-recipe-notes",
        description="Generate personal recipe notes from one Bilibili video or a persistent batch",
    )
    parser.add_argument("url", nargs="?", help="Single video URL, creator homepage, or one URL for --batch")
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
    batch_mode = parser.add_mutually_exclusive_group()
    batch_mode.add_argument("--batch", action="store_true", help="Create and synchronously run a persistent batch")
    batch_mode.add_argument("--resume-batch", metavar="BATCH_ID", help="Continue missing stages in an existing batch")
    batch_mode.add_argument("--retry-batch", metavar="BATCH_ID", help="Retry only failed stages in an existing batch")
    batch_mode.add_argument("--show-batch", metavar="BATCH_ID", help="Show one persisted batch and exit")
    batch_mode.add_argument("--list-batches", action="store_true", help="List persisted batches and exit")
    parser.add_argument(
        "--batch-url",
        action="append",
        default=[],
        help="Add one batch video URL; may be repeated",
    )
    parser.add_argument(
        "--batch-file",
        action="append",
        default=[],
        metavar="PATH",
        help="Read batch URLs from a UTF-8 text file, one per line; use - for stdin; may be repeated",
    )
    parser.add_argument("--batch-id", default=None, help="Optional stable ID for a newly created batch")
    parser.add_argument(
        "--target-stage",
        choices=["raw", "recipe"],
        default="recipe",
        help="Stop after metadata/transcript (raw) or generate the complete recipe (recipe)",
    )
    parser.add_argument(
        "--create-only",
        action="store_true",
        help="With --batch, save a pending batch without downloading or processing anything",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Do not reuse already complete output when running a batch",
    )
    return parser


def _log(message: str) -> None:
    console.print(message)


def _extra_instructions(args: argparse.Namespace) -> str | None:
    value = getattr(args, "llm_extra_instructions", None)
    extra_file = getattr(args, "llm_extra_instructions_file", None)
    if not extra_file:
        return value
    prompt_path = Path(extra_file).expanduser()
    if not prompt_path.is_file():
        raise FileNotFoundError(f"LLM extra instructions file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _single_options(args: argparse.Namespace, url: str, extra_instructions: str | None) -> RecipeJobOptions:
    return RecipeJobOptions(
        url=url,
        cookies=getattr(args, "cookies", None),
        out=getattr(args, "out", "outputs"),
        no_screenshot=getattr(args, "no_screenshot", False),
        whisper_model=getattr(args, "whisper_model", "small"),
        language=getattr(args, "language", "zh"),
        keep_media=getattr(args, "keep_media", False),
        no_llm_summary=getattr(args, "no_llm_summary", False),
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


def _batch_options(
    args: argparse.Namespace,
    urls: list[str],
    batch_id: str,
    resume_mode: str,
    extra_instructions: str | None,
) -> BatchJobOptions:
    return BatchJobOptions(
        urls=urls,
        cookies=getattr(args, "cookies", None),
        out=getattr(args, "out", "outputs"),
        no_screenshot=getattr(args, "no_screenshot", False),
        whisper_model=getattr(args, "whisper_model", "small"),
        language=getattr(args, "language", "zh"),
        keep_media=getattr(args, "keep_media", False),
        no_llm_summary=getattr(args, "no_llm_summary", False),
        llm_provider=getattr(args, "llm_provider", "opencode"),
        openai_model=getattr(args, "openai_model", "gpt-5.5"),
        local_llm_command=getattr(args, "local_llm_command", None),
        codex_model=getattr(args, "codex_model", None),
        codex_profile=getattr(args, "codex_profile", None),
        llm_cli_extra_instructions=extra_instructions,
        max_recipe_steps=getattr(args, "max_steps", 10),
        max_step_images=getattr(args, "max_images", 4),
        enable_recipe_review=getattr(args, "review", False),
        skip_existing=not getattr(args, "no_skip_existing", False),
        batch_id=batch_id,
        resume_mode=resume_mode,
        target_stage=getattr(args, "target_stage", "recipe"),
    )


def _read_batch_urls(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    positional = getattr(args, "url", None)
    if positional:
        values.append(str(positional))
    values.extend(str(value) for value in (getattr(args, "batch_url", None) or []))
    for raw_path in getattr(args, "batch_file", None) or []:
        if raw_path == "-":
            text = sys.stdin.read()
        else:
            path = Path(raw_path).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"Batch URL file not found: {path}")
            text = path.read_text(encoding="utf-8-sig")
        values.extend(
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _print_batch_state(state: BatchQueueState, *, include_items: bool = True) -> None:
    counts = Counter(item.status for item in state.items)
    summary = ", ".join(f"{name}={count}" for name, count in sorted(counts.items())) or "empty"
    console.print(
        f"[bold]{state.batch_id}[/bold] | items={len(state.items)} | updated={state.updated_at} | {summary}"
    )
    if not include_items:
        return
    for index, item in enumerate(state.items, start=1):
        raw = item.stages.get("raw")
        recipe = item.stages.get("recipe")
        output = f" | output={item.output_folder}" if item.output_folder else ""
        error = f" | error={item.error}" if item.error else ""
        console.print(
            f"{index:04d} | {item.status} | raw={raw.status if raw else 'pending'} "
            f"| recipe={recipe.status if recipe else 'pending'} | {item.url}{output}{error}"
        )


def _run_batch_mode(args: argparse.Namespace, extra_instructions: str | None) -> int:
    supplied_urls = bool(
        getattr(args, "url", None) or getattr(args, "batch_url", None) or getattr(args, "batch_file", None)
    )
    show_batch = getattr(args, "show_batch", None)
    if show_batch:
        if supplied_urls or getattr(args, "batch_id", None) or getattr(args, "create_only", False):
            raise ValueError("--show-batch does not accept new batch URLs, --batch-id, or --create-only")
        _print_batch_state(load_batch_state(show_batch))
        return 0
    if getattr(args, "list_batches", False):
        if supplied_urls or getattr(args, "batch_id", None) or getattr(args, "create_only", False):
            raise ValueError("--list-batches does not accept new batch URLs, --batch-id, or --create-only")
        states = list_batch_states()
        if not states:
            console.print("No persisted batches.")
        for state in states:
            _print_batch_state(state, include_items=False)
        return 0

    resume_batch = getattr(args, "resume_batch", None)
    retry_batch = getattr(args, "retry_batch", None)
    if resume_batch or retry_batch:
        if supplied_urls:
            raise ValueError("Resume/retry uses URLs already saved in the batch; do not provide new batch URLs")
        if getattr(args, "batch_id", None):
            raise ValueError("--batch-id is only for creating a new batch")
        batch_id = str(resume_batch or retry_batch)
        if getattr(args, "create_only", False):
            raise ValueError("--create-only can only be used with --batch")
        options = _batch_options(
            args,
            [],
            batch_id,
            "resume-unfinished" if resume_batch else "retry-failed",
            extra_instructions,
        )
    else:
        urls = _read_batch_urls(args)
        if not urls:
            raise ValueError("Batch mode needs a positional URL, --batch-url, or --batch-file")
        batch_id = getattr(args, "batch_id", None) or create_batch_id()
        options = _batch_options(args, urls, batch_id, "new", extra_instructions)
        if getattr(args, "create_only", False):
            snapshot = asdict(options)
            snapshot.pop("urls", None)
            state = create_batch_state(urls, snapshot, batch_id=batch_id)
            console.print(f"[green]Pending batch created: {state.batch_id} ({len(state.items)} items)[/green]")
            return 0

    console.print(
        f"[bold]Running batch {batch_id}[/bold] | target={options.target_stage} | mode={options.resume_mode}"
    )
    result = run_batch(options, log=_log)
    failed = sum(item.status == "failed" for item in result.items)
    done = len(result.items) - failed
    console.print(
        f"[green]Batch finished: {batch_id} | processed={len(result.items)} | ok={done} | failed={failed}[/green]"
    )
    _print_batch_state(load_batch_state(batch_id))
    return 1 if failed else 0


def run(args: argparse.Namespace) -> int:
    batch_requested = any(
        (
            getattr(args, "batch", False),
            getattr(args, "resume_batch", None),
            getattr(args, "retry_batch", None),
            getattr(args, "show_batch", None),
            getattr(args, "list_batches", False),
        )
    )
    if batch_requested:
        if getattr(args, "creator_home", False):
            raise ValueError("--creator-home cannot be combined with batch mode")
        return _run_batch_mode(args, _extra_instructions(args))

    if any(
        (
            getattr(args, "batch_url", None),
            getattr(args, "batch_file", None),
            getattr(args, "batch_id", None),
            getattr(args, "create_only", False),
            getattr(args, "no_skip_existing", False),
            getattr(args, "target_stage", "recipe") != "recipe",
        )
    ):
        raise ValueError("Batch-only options require --batch, --resume-batch, or --retry-batch")

    url = getattr(args, "url", None)
    if not url:
        raise ValueError("A video URL is required unless a batch/status option is used")
    if getattr(args, "creator_home", False):
        extract_creator_links(
            url=url,
            cookies=getattr(args, "cookies", None),
            out=getattr(args, "out", "outputs"),
            filename=getattr(args, "creator_links_file", "creator_video_links.txt"),
            log=_log,
        )
        return 0

    options = _single_options(args, str(url), _extra_instructions(args))
    result = generate_recipe_note(options, log=_log)
    console.print(f"[green]Done. Output saved to {result.output_folder}[/green]")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Persisted batch progress can be continued with --resume-batch.[/yellow]")
        return 130
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error: {exc}[/red]")
        return 2
