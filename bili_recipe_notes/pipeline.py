from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .batch_queue import create_batch_state, load_batch_state, save_batch_state, selectable_items
from .history import find_history_by_url
from .downloader import download_audio, download_lowres_video, download_subtitles, extract_creator_video_links, fetch_video_info
from .llm import (
    finalize_rewritten_note,
    get_last_llm_error,
    normalize_markdown_image_paths,
    summarize_note,
)
from .markdown_writer import render_markdown
from .quality import write_quality_report
from .recipe_extractor import Recipe, TranscriptSegment, extract_recipe_rule_based, extract_recipe_with_llm
from .recipe_review import create_recipe_review
from .screenshot import capture_screenshot_at, capture_step_screenshots
from .storage import atomic_write_json, atomic_write_text
from .subtitle import parse_subtitle_file
from .transcriber import transcribe_audio
from .utils import build_output_folder_name, ensure_dir

LogCallback = Callable[[str], None]


@dataclass
class RecipeJobOptions:
    url: str
    cookies: str | None = None
    out: str = "outputs"
    no_screenshot: bool = False
    whisper_model: str = "small"
    language: str = "zh"
    keep_media: bool = False
    no_llm_summary: bool = False
    llm_provider: str = "opencode"
    openai_model: str = "gpt-5.5"
    local_llm_command: str | None = None
    codex_model: str | None = None
    codex_profile: str | None = None
    llm_cli_extra_instructions: str | None = None
    max_recipe_steps: int = 10
    max_step_images: int = 4
    enable_recipe_review: bool = False


@dataclass
class RecipeJobResult:
    output_folder: Path
    note_path: Path
    recipe_path: Path
    transcript_path: Path
    final_note: str
    job_path: Path | None = None
    stage_errors: list[str] | None = None


@dataclass
class BatchJobOptions:
    urls: list[str]
    cookies: str | None = None
    out: str = "outputs"
    no_screenshot: bool = False
    whisper_model: str = "small"
    language: str = "zh"
    keep_media: bool = False
    no_llm_summary: bool = False
    llm_provider: str = "opencode"
    openai_model: str = "gpt-5.5"
    local_llm_command: str | None = None
    codex_model: str | None = None
    codex_profile: str | None = None
    llm_cli_extra_instructions: str | None = None
    max_recipe_steps: int = 10
    max_step_images: int = 4
    enable_recipe_review: bool = False
    skip_existing: bool = True
    batch_id: str | None = None
    resume_mode: str = "new"


@dataclass
class BatchJobItemResult:
    url: str
    status: str
    output_folder: Path | None = None
    note_path: Path | None = None
    error: str | None = None


@dataclass
class BatchJobResult:
    items: list[BatchJobItemResult]


class PipelineStageError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.message = message


def _emit(log: LogCallback | None, message: str) -> None:
    if log:
        log(message)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _model_dump(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.__dict__


def _model_validate_recipe(data: dict) -> Recipe:
    if hasattr(Recipe, "model_validate"):
        return Recipe.model_validate(data)
    return Recipe(**data)


def _model_validate_segment(data: dict) -> TranscriptSegment:
    if hasattr(TranscriptSegment, "model_validate"):
        return TranscriptSegment.model_validate(data)
    return TranscriptSegment(**data)


def _write_job(folder: Path, job: dict) -> Path:
    job_path = folder / "job.json"
    atomic_write_json(job_path, job)
    return job_path


def _load_recipe(recipe_path: Path) -> Recipe:
    return _model_validate_recipe(json.loads(recipe_path.read_text(encoding="utf-8")))


def _load_transcript(transcript_path: Path) -> list[TranscriptSegment]:
    raw = json.loads(transcript_path.read_text(encoding="utf-8"))
    return [_model_validate_segment(item) for item in raw]


def _usable_transcript(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    return [segment for segment in segments if str(segment.text or "").strip()]


def _stable_video_identity(info: dict, source_url: str) -> tuple[str | int | None, str | int | None, str | int | None, str]:
    bvid = info.get("bvid") or info.get("id")
    if not bvid:
        match = re.search(r"/(BV[0-9A-Za-z]+)(?:[/?#]|$)", source_url, flags=re.IGNORECASE)
        if match:
            bvid = match.group(1)

    cid = info.get("cid")
    if cid is None:
        cid = info.get("page_id")
    if cid is not None and str(cid).strip():
        return bvid, cid, cid, "cid"

    # Some yt-dlp metadata versions do not expose CID.  The Bilibili `p`
    # parameter still gives multipart videos a stable, non-colliding identity.
    candidate_urls = [info.get("webpage_url"), info.get("original_url"), source_url]
    for candidate in candidate_urls:
        if not isinstance(candidate, str):
            continue
        page = (parse_qs(urlparse(candidate).query).get("p") or [None])[0]
        if page is not None and str(page).strip():
            return bvid, None, page, "p"
    return bvid, None, None, "cid"


def _llm_extraction_enabled(options: RecipeJobOptions) -> bool:
    return not options.no_llm_summary and options.llm_provider.strip().lower() not in {"", "none", "off", "disabled"}


def _validate_generated_artifacts(
    transcript: list[TranscriptSegment],
    recipe: Recipe,
    final_note: str,
    transcript_path: Path,
    recipe_path: Path,
    note_path: Path,
) -> None:
    if not _usable_transcript(transcript):
        raise PipelineStageError("validate_output", "transcript contains no usable text")
    if not recipe.steps:
        raise PipelineStageError("validate_output", "recipe contains no cooking steps")
    if not final_note.strip():
        raise PipelineStageError("validate_output", "note is empty")
    for path in (transcript_path, recipe_path, note_path):
        if not path.is_file() or path.stat().st_size <= 0:
            raise PipelineStageError("validate_output", f"missing or empty artifact: {path.name}")


def _has_step_images(recipe: Recipe) -> bool:
    return any(bool(step.screenshot_path) for step in recipe.steps)


def _key_screenshot_timestamps(recipe: Recipe, duration: float | int | None) -> list[float]:
    candidates: list[float] = []
    for step in recipe.steps:
        candidates.append(max(0.0, step.start_time + 1.5))
    if isinstance(duration, (int, float)) and duration > 0:
        candidates.extend([max(0.0, duration * 0.3), max(0.0, duration * 0.5)])
    candidates.append(1.5)

    seen: set[float] = set()
    unique: list[float] = []
    for timestamp in candidates:
        rounded = round(timestamp, 1)
        if rounded in seen:
            continue
        seen.add(rounded)
        unique.append(timestamp)
    return unique


def _capture_fallback_key_screenshot(
    video_path: Path,
    recipe: Recipe,
    images_dir: Path,
    duration: float | int | None,
) -> Path:
    if not recipe.steps:
        raise ValueError("recipe has no steps")

    images_dir.mkdir(parents=True, exist_ok=True)
    output_path = images_dir / "key_01.jpg"
    last_error: Exception | None = None
    for timestamp in _key_screenshot_timestamps(recipe, duration):
        try:
            capture_screenshot_at(video_path, timestamp, output_path)
            recipe.steps[0].screenshot_path = f"images/{output_path.name}"
            return output_path
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"fallback key screenshot failed: {last_error}")


def generate_recipe_note(options: RecipeJobOptions, log: LogCallback | None = None) -> RecipeJobResult:
    started_at = _now()
    folder: Path | None = None
    media_dir: Path | None = None
    job: dict = {
        "source_url": options.url,
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "error": None,
    }
    stage_errors: list[str] = []
    try:
        _emit(log, "Fetching video info...")
        try:
            info = fetch_video_info(options.url, cookies=options.cookies)
        except Exception as exc:  # noqa: BLE001
            raise PipelineStageError("fetch_info", str(exc)) from exc

        title = info.get("title") or "untitled"
        bvid, cid, part_id, part_label = _stable_video_identity(info, options.url)
        folder_name = build_output_folder_name(
            title=title,
            uploader=info.get("uploader"),
            video_id=bvid,
            part_id=part_id,
            part_label=part_label,
            source_url=options.url,
        )
        folder = ensure_dir(Path(options.out) / folder_name)
        media_dir = ensure_dir(folder / "media")
        job.update(
            {
                "title": title,
                "video_title": title,
                "uploader": info.get("uploader"),
                "bvid": bvid,
                "cid": cid,
                "part_id": part_id,
                "part_label": part_label,
                "duration": info.get("duration"),
                "output_folder": str(folder),
            }
        )
        _write_job(folder, job)

        metadata = {
            "source_url": options.url,
            "video_title": title,
            "uploader": info.get("uploader"),
            "bvid": bvid,
            "cid": cid,
            "part_id": part_id,
            "part_label": part_label,
            "duration": info.get("duration"),
        }

        subtitle_files: list[Path] = []
        try:
            _emit(log, "Downloading subtitles...")
            subtitle_files = download_subtitles(options.url, media_dir, language=options.language, cookies=options.cookies)
        except Exception as exc:  # noqa: BLE001
            message = f"subtitle: {exc}"
            stage_errors.append(message)
            _emit(log, f"Subtitle download failed, fallback to whisper transcription: {exc}")

        transcript: list[TranscriptSegment] = []
        if subtitle_files:
            _emit(log, "Using subtitle path.")
            for sf in subtitle_files:
                try:
                    transcript = _usable_transcript(parse_subtitle_file(sf))
                    if transcript:
                        break
                except Exception as exc:  # noqa: BLE001
                    message = f"subtitle_parse: {sf}: {exc}"
                    stage_errors.append(message)
                    _emit(log, f"Subtitle parse failed {sf}: {exc}")

        if not transcript:
            _emit(log, "No subtitles found, fallback to whisper transcription.")
            try:
                audio = download_audio(options.url, media_dir, cookies=options.cookies)
                transcript = _usable_transcript(
                    transcribe_audio(audio, model_size=options.whisper_model, language=options.language)
                )
            except Exception as exc:  # noqa: BLE001
                raise PipelineStageError("transcribe_audio", str(exc)) from exc
            if not transcript:
                raise PipelineStageError("transcribe_audio", "transcription produced no usable text")

        _emit(log, "Extracting recipe structure...")
        recipe: Recipe | None = None
        if _llm_extraction_enabled(options):
            try:
                recipe = extract_recipe_with_llm(
                    transcript,
                    metadata,
                    provider=options.llm_provider,
                    openai_model=options.openai_model,
                    local_llm_command=options.local_llm_command,
                    codex_model=options.codex_model,
                    codex_profile=options.codex_profile,
                    cli_extra_instructions=options.llm_cli_extra_instructions,
                    max_steps=options.max_recipe_steps,
                )
                _emit(log, "Structured recipe extraction completed.")
            except Exception as exc:  # noqa: BLE001
                message = f"extract_recipe_llm: {exc}"
                stage_errors.append(message)
                _emit(log, f"Structured extraction failed, using rule-based fallback: {exc}")
        if recipe is None:
            try:
                recipe = extract_recipe_rule_based(transcript, metadata, max_steps=options.max_recipe_steps)
            except Exception as exc:  # noqa: BLE001
                raise PipelineStageError("extract_recipe", str(exc)) from exc

        if not options.no_screenshot and recipe.steps:
            try:
                _emit(log, "Capturing step screenshots...")
                video = download_lowres_video(options.url, media_dir, cookies=options.cookies)
                capture_step_screenshots(
                    video,
                    recipe.steps,
                    folder / "images",
                    max_images=options.max_step_images,
                )
                if not _has_step_images(recipe):
                    _emit(log, "No step screenshots captured, trying one fallback key screenshot...")
                    _capture_fallback_key_screenshot(video, recipe, folder / "images", info.get("duration"))
            except Exception as exc:  # noqa: BLE001
                message = f"screenshot: {exc}"
                stage_errors.append(message)
                _emit(log, f"Video download/screenshot skipped: {exc}")

        note_path = folder / "note.md"
        transcript_path = folder / "transcript.json"
        recipe_path = folder / "recipe.json"

        note_markdown = render_markdown(recipe)
        normalized_note = normalize_markdown_image_paths(note_markdown)

        _emit(log, "Writing output files...")
        atomic_write_json(transcript_path, [_model_dump(seg) for seg in transcript])
        atomic_write_json(recipe_path, _model_dump(recipe))
        if options.enable_recipe_review:
            create_recipe_review(recipe, folder)
        atomic_write_text(note_path, normalized_note)
        final_note = normalized_note

        if recipe.extraction_method == "llm":
            _emit(log, "Using deterministic Markdown rendered from the structured recipe.")
        elif _llm_extraction_enabled(options):
            _emit(log, "Using deterministic Markdown from the rule-based fallback; review is required.")

        _validate_generated_artifacts(
            transcript,
            recipe,
            final_note,
            transcript_path,
            recipe_path,
            note_path,
        )

        if not options.keep_media:
            _emit(log, "Cleaning temporary media files...")
            shutil.rmtree(media_dir, ignore_errors=True)

        job.update(
            {
                "status": "done",
                "finished_at": _now(),
                "error": None,
                "note_path": str(note_path),
                "recipe_path": str(recipe_path),
                "transcript_path": str(transcript_path),
                "stage_errors": stage_errors,
            }
        )
        try:
            write_quality_report(folder)
        except Exception as exc:  # noqa: BLE001
            message = f"quality: {exc}"
            stage_errors.append(message)
            _emit(log, f"Quality report skipped: {exc}")
        job_path = _write_job(folder, job)

        return RecipeJobResult(
            output_folder=folder,
            note_path=note_path,
            recipe_path=recipe_path,
            transcript_path=transcript_path,
            final_note=final_note,
            job_path=job_path,
            stage_errors=stage_errors,
        )
    except Exception as exc:
        if media_dir is not None and not options.keep_media:
            shutil.rmtree(media_dir, ignore_errors=True)
        if folder:
            job.update({"status": "failed", "finished_at": _now(), "error": str(exc), "stage_errors": stage_errors})
            _write_job(folder, job)
        raise


def extract_creator_links(
    url: str,
    cookies: str | None,
    out: str,
    filename: str,
    log: LogCallback | None = None,
) -> Path:
    _emit(log, "Extracting creator video links...")
    links = extract_creator_video_links(url, cookies=cookies)
    out_dir = ensure_dir(Path(out))
    requested_name = Path(filename.strip() or "creator_video_links.txt")
    if requested_name.is_absolute() or requested_name.name != str(requested_name):
        raise ValueError("creator links filename must be a plain filename inside the output directory")
    links_path = (out_dir / requested_name.name).resolve()
    try:
        links_path.relative_to(out_dir.resolve())
    except ValueError:
        raise ValueError("creator links filename escapes the output directory") from None
    atomic_write_text(links_path, "\n".join(links) + ("\n" if links else ""))
    _emit(log, f"Extracted {len(links)} video links to {links_path}")
    return links_path


def _process_batch_url(options: BatchJobOptions, url: str, log: LogCallback | None = None) -> BatchJobItemResult:
    if options.skip_existing:
        existing = find_history_by_url(options.out, url)
        if existing:
            _emit(log, f"Skipped existing output: {existing.output_folder}")
            return BatchJobItemResult(
                url=url,
                status="skipped",
                output_folder=existing.output_folder,
                note_path=existing.note_path,
            )
    try:
        result = generate_recipe_note(
            RecipeJobOptions(
                url=url,
                cookies=options.cookies,
                out=options.out,
                no_screenshot=options.no_screenshot,
                whisper_model=options.whisper_model,
                language=options.language,
                keep_media=options.keep_media,
                no_llm_summary=options.no_llm_summary,
                llm_provider=options.llm_provider,
                openai_model=options.openai_model,
                local_llm_command=options.local_llm_command,
                codex_model=options.codex_model,
                codex_profile=options.codex_profile,
                llm_cli_extra_instructions=options.llm_cli_extra_instructions,
                max_recipe_steps=options.max_recipe_steps,
                max_step_images=options.max_step_images,
                enable_recipe_review=options.enable_recipe_review,
            ),
            log=log,
        )
    except Exception as exc:  # noqa: BLE001
        _emit(log, f"Failed: {exc}")
        return BatchJobItemResult(url=url, status="failed", error=str(exc))
    return BatchJobItemResult(url=url, status="done", output_folder=result.output_folder, note_path=result.note_path)


def _run_persistent_batch(options: BatchJobOptions, log: LogCallback | None = None) -> BatchJobResult:
    if options.batch_id and options.resume_mode != "new":
        state = load_batch_state(options.batch_id)
    else:
        state = create_batch_state(options.urls, asdict(options), batch_id=options.batch_id)

    items_to_process = selectable_items(state, options.resume_mode)
    results: list[BatchJobItemResult] = []
    for idx, item in enumerate(items_to_process, start=1):
        _emit(log, f"[{idx}/{len(items_to_process)}] {item.url}")
        item.status = "running"
        item.error = None
        item.started_at = _now()
        item.finished_at = None
        save_batch_state(state)

        result = _process_batch_url(options, item.url, log=log)
        item.status = result.status
        item.output_folder = str(result.output_folder) if result.output_folder else None
        item.note_path = str(result.note_path) if result.note_path else None
        item.error = result.error
        item.finished_at = _now()
        save_batch_state(state)
        results.append(result)
    return BatchJobResult(items=results)


def run_batch(options: BatchJobOptions, log: LogCallback | None = None) -> BatchJobResult:
    if options.batch_id:
        return _run_persistent_batch(options, log=log)

    seen: set[str] = set()
    urls = []
    for url in options.urls:
        cleaned = url.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)

    items: list[BatchJobItemResult] = []
    for idx, url in enumerate(urls, start=1):
        _emit(log, f"[{idx}/{len(urls)}] {url}")
        items.append(_process_batch_url(options, url, log=log))
    return BatchJobResult(items=items)


def regenerate_note_from_recipe(
    output_folder: str | Path,
    no_llm_summary: bool = True,
    llm_provider: str = "opencode",
    openai_model: str = "gpt-5.5",
    local_llm_command: str | None = None,
    codex_model: str | None = None,
    codex_profile: str | None = None,
    llm_cli_extra_instructions: str | None = None,
) -> RecipeJobResult:
    folder = Path(output_folder)
    recipe_path = folder / "recipe.json"
    transcript_path = folder / "transcript.json"
    note_path = folder / "note.md"
    recipe = _load_recipe(recipe_path)
    note_markdown = normalize_markdown_image_paths(render_markdown(recipe))
    final_note = note_markdown
    stage_errors: list[str] = []
    if not no_llm_summary:
        summary = summarize_note(
            note_markdown,
            provider=llm_provider,
            openai_model=openai_model,
            local_llm_command=local_llm_command,
            codex_model=codex_model,
            codex_profile=codex_profile,
            cli_extra_instructions=llm_cli_extra_instructions,
        )
        if summary:
            final_note = finalize_rewritten_note(summary, note_markdown)
        else:
            message = f"llm: {llm_provider} unavailable or failed"
            detail = get_last_llm_error()
            if detail:
                message = f"{message}: {detail}"
            stage_errors.append(message)
    atomic_write_text(note_path, final_note)
    write_quality_report(folder)
    return RecipeJobResult(folder, note_path, recipe_path, transcript_path, final_note, stage_errors=stage_errors)


def regenerate_recipe_from_transcript(output_folder: str | Path) -> RecipeJobResult:
    folder = Path(output_folder)
    recipe_path = folder / "recipe.json"
    transcript_path = folder / "transcript.json"
    note_path = folder / "note.md"
    transcript = _load_transcript(transcript_path)
    old_recipe = _load_recipe(recipe_path) if recipe_path.exists() else None
    metadata = {
        "source_url": old_recipe.source_url if old_recipe else "",
        "video_title": old_recipe.video_title if old_recipe else folder.name,
        "uploader": old_recipe.uploader if old_recipe else None,
        "recipe_title": old_recipe.title if old_recipe else folder.name,
    }
    recipe = extract_recipe_rule_based(transcript, metadata)
    atomic_write_json(recipe_path, _model_dump(recipe))
    final_note = normalize_markdown_image_paths(render_markdown(recipe))
    atomic_write_text(note_path, final_note)
    write_quality_report(folder)
    return RecipeJobResult(folder, note_path, recipe_path, transcript_path, final_note)


def recapture_step_screenshot(
    output_folder: str | Path,
    step_index: int,
    timestamp: float,
    cookies: str | None = None,
    video_path: str | Path | None = None,
) -> Path:
    folder = Path(output_folder)
    recipe_path = folder / "recipe.json"
    recipe = _load_recipe(recipe_path)
    if step_index < 1 or step_index > len(recipe.steps):
        raise IndexError("step_index is out of range")

    media_dir = ensure_dir(folder / "media")
    if video_path:
        video = Path(video_path)
    else:
        existing = next(iter(sorted(media_dir.glob("video.*"))), None)
        if existing:
            video = existing
        elif recipe.source_url:
            video = download_lowres_video(recipe.source_url, media_dir, cookies=cookies)
        else:
            raise FileNotFoundError("No source video is available for recapturing screenshots")

    image_path = folder / "images" / f"step_{step_index:02d}.jpg"
    capture_screenshot_at(video, timestamp, image_path)
    step = recipe.steps[step_index - 1]
    step.start_time = timestamp
    step.screenshot_path = f"images/{image_path.name}"
    atomic_write_json(recipe_path, _model_dump(recipe))
    regenerate_note_from_recipe(folder)
    return image_path
