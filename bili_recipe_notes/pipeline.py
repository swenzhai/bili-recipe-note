from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import parse_qs, urlparse

from .batch_queue import create_batch_state, load_batch_state, save_batch_state, selectable_items
from .history import find_history_by_url, find_history_record_by_url, is_raw_output
from .downloader import (
    CreatorCrawlResult,
    crawl_creator_videos,
    download_audio,
    download_lowres_video,
    download_subtitles,
    extract_creator_video_links,
    fetch_video_info,
)
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
from .utils import build_output_folder_name, ensure_dir, sanitize_filename

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
    require_llm: bool = False
    require_screenshot: bool = False
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
class RawJobResult:
    output_folder: Path
    source_path: Path
    transcript_path: Path
    job_path: Path
    stage_errors: list[str] | None = None


PipelineTarget = Literal["raw", "recipe"]


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
    require_llm: bool = False
    require_screenshot: bool = False
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
    target_stage: PipelineTarget = "recipe"


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


@dataclass
class CreatorArchiveResult:
    crawl: CreatorCrawlResult
    creator_dir: Path
    links_path: Path
    manifest_path: Path


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


def _has_existing_step_images(folder: Path, recipe: Recipe) -> bool:
    for step in recipe.steps:
        raw_path = str(step.screenshot_path or "").strip()
        if not raw_path:
            continue
        relative = Path(raw_path.replace("\\", "/"))
        if relative.is_absolute():
            continue
        image_path = folder / relative
        try:
            image_path.resolve().relative_to(folder.resolve())
        except ValueError:
            continue
        if image_path.is_file() and image_path.stat().st_size > 0:
            return True
    return False


def _recipe_output_meets_requirements(folder: Path, options: RecipeJobOptions) -> bool:
    if not (options.require_llm or options.require_screenshot):
        return True
    job = _read_json_object(folder / "job.json")
    stage_errors = job.get("stage_errors") if isinstance(job.get("stage_errors"), list) else []
    if options.require_llm:
        if any(str(error).startswith("extract_recipe_llm:") for error in stage_errors):
            return False
        try:
            recipe = _load_recipe(folder / "recipe.json")
        except Exception:
            return False
        if recipe.extraction_method != "llm":
            return False
    else:
        try:
            recipe = _load_recipe(folder / "recipe.json")
        except Exception:
            return False
    if options.require_screenshot and not _has_existing_step_images(folder, recipe):
        return False
    return True


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


def _read_json_object(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _ensure_job_stages(job: dict) -> dict:
    stages = job.get("stages")
    if not isinstance(stages, dict):
        stages = {}
        job["stages"] = stages
    for name in ("raw", "recipe"):
        if not isinstance(stages.get(name), dict):
            stages[name] = {
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "error": None,
            }
    return stages


def _set_job_stage(job: dict, name: str, status: str, error: str | None = None) -> None:
    stage = _ensure_job_stages(job)[name]
    stage["status"] = status
    stage["error"] = error
    if status == "running":
        stage["started_at"] = _now()
        stage["finished_at"] = None
    elif status in {"done", "failed"}:
        stage["finished_at"] = _now()


def capture_raw_material(
    options: RecipeJobOptions,
    log: LogCallback | None = None,
    *,
    preserve_media: bool = False,
) -> RawJobResult:
    """Fetch metadata and create a durable transcript without generating a recipe."""
    folder: Path | None = None
    media_dir: Path | None = None
    stage_errors: list[str] = []
    job: dict = {
        "source_url": options.url,
        "status": "raw_running",
        "started_at": _now(),
        "finished_at": None,
        "error": None,
    }
    _ensure_job_stages(job)
    _set_job_stage(job, "raw", "running")
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
        existing_job = _read_json_object(folder / "job.json")
        if existing_job:
            job.update(existing_job)
            job.update({"status": "raw_running", "error": None, "finished_at": None})
            _ensure_job_stages(job)
            job["stages"]["recipe"].update(
                {"status": "pending", "error": None, "started_at": None, "finished_at": None}
            )
            _set_job_stage(job, "raw", "running")

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
        job.update(
            {
                "title": title,
                **metadata,
                "output_folder": str(folder),
            }
        )
        _write_job(folder, job)

        subtitle_files: list[Path] = []
        try:
            _emit(log, "Downloading subtitles...")
            subtitle_files = download_subtitles(
                options.url,
                media_dir,
                language=options.language,
                cookies=options.cookies,
            )
        except Exception as exc:  # noqa: BLE001
            stage_errors.append(f"subtitle: {exc}")
            _emit(log, f"Subtitle download failed, fallback to whisper transcription: {exc}")

        transcript: list[TranscriptSegment] = []
        for subtitle_file in subtitle_files:
            try:
                transcript = _usable_transcript(parse_subtitle_file(subtitle_file))
                if transcript:
                    _emit(log, "Using subtitle path.")
                    break
            except Exception as exc:  # noqa: BLE001
                stage_errors.append(f"subtitle_parse: {subtitle_file}: {exc}")
                _emit(log, f"Subtitle parse failed {subtitle_file}: {exc}")

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

        source_path = folder / "source.json"
        transcript_path = folder / "transcript.json"
        _emit(log, "Writing raw source and transcript...")
        atomic_write_json(source_path, metadata)
        atomic_write_json(transcript_path, [_model_dump(segment) for segment in transcript])
        _set_job_stage(job, "raw", "done")
        recipe_done = _ensure_job_stages(job)["recipe"].get("status") == "done"
        job.update(
            {
                "status": "done" if recipe_done else "raw_ready",
                "finished_at": _now(),
                "error": None,
                "source_path": str(source_path),
                "transcript_path": str(transcript_path),
                "stage_errors": stage_errors,
            }
        )
        job_path = _write_job(folder, job)
        return RawJobResult(folder, source_path, transcript_path, job_path, stage_errors)
    except Exception as exc:
        if folder:
            _set_job_stage(job, "raw", "failed", str(exc))
            job.update({"status": "failed", "finished_at": _now(), "error": str(exc), "stage_errors": stage_errors})
            _write_job(folder, job)
        raise
    finally:
        if media_dir is not None and not preserve_media:
            shutil.rmtree(media_dir, ignore_errors=True)


def _load_source_metadata(folder: Path, fallback_url: str = "") -> dict:
    metadata = _read_json_object(folder / "source.json")
    if not metadata:
        job = _read_json_object(folder / "job.json")
        metadata = {
            key: job.get(key)
            for key in ("source_url", "video_title", "uploader", "bvid", "cid", "part_id", "part_label", "duration")
        }
    if not metadata.get("source_url"):
        metadata["source_url"] = fallback_url
    return metadata


def generate_recipe_from_raw(
    output_folder: str | Path,
    options: RecipeJobOptions,
    log: LogCallback | None = None,
) -> RecipeJobResult:
    """Generate recipe artifacts from a previously completed raw stage."""
    folder = Path(output_folder)
    transcript_path = folder / "transcript.json"
    transcript = _usable_transcript(_load_transcript(transcript_path))
    if not transcript:
        raise PipelineStageError("load_raw", "transcript contains no usable text")
    metadata = _load_source_metadata(folder, options.url)
    source_url = str(metadata.get("source_url") or options.url)
    media_dir = ensure_dir(folder / "media")
    stage_errors: list[str] = []
    job = _read_json_object(folder / "job.json") or {
        "source_url": source_url,
        "started_at": _now(),
    }
    _ensure_job_stages(job)
    if job["stages"]["raw"].get("status") != "done":
        _set_job_stage(job, "raw", "done")
    _set_job_stage(job, "recipe", "running")
    job.update({"status": "recipe_running", "error": None, "finished_at": None})
    _write_job(folder, job)
    try:
        _emit(log, "Extracting recipe structure from saved transcript...")
        recipe: Recipe | None = None
        if options.require_llm and not _llm_extraction_enabled(options):
            raise PipelineStageError("extract_recipe_llm", "LLM extraction is required but disabled")
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
                stage_errors.append(f"extract_recipe_llm: {exc}")
                if options.require_llm:
                    raise PipelineStageError("extract_recipe_llm", str(exc)) from exc
                _emit(log, f"Structured extraction failed, using rule-based fallback: {exc}")
        if recipe is None:
            try:
                recipe = extract_recipe_rule_based(transcript, metadata, max_steps=options.max_recipe_steps)
            except Exception as exc:  # noqa: BLE001
                raise PipelineStageError("extract_recipe", str(exc)) from exc

        if options.require_screenshot and options.no_screenshot:
            raise PipelineStageError("screenshot", "step screenshots are required but disabled")
        if not options.no_screenshot and recipe.steps:
            try:
                _emit(log, "Capturing step screenshots...")
                video = download_lowres_video(source_url, media_dir, cookies=options.cookies)
                capture_step_screenshots(video, recipe.steps, folder / "images", max_images=options.max_step_images)
                if not _has_step_images(recipe):
                    _capture_fallback_key_screenshot(video, recipe, folder / "images", metadata.get("duration"))
            except Exception as exc:  # noqa: BLE001
                stage_errors.append(f"screenshot: {exc}")
                if options.require_screenshot:
                    raise PipelineStageError("screenshot", str(exc)) from exc
                _emit(log, f"Video download/screenshot skipped: {exc}")
        if options.require_screenshot and not _has_existing_step_images(folder, recipe):
            raise PipelineStageError("screenshot", "no usable step screenshot was produced")

        recipe_path = folder / "recipe.json"
        note_path = folder / "note.md"
        final_note = normalize_markdown_image_paths(render_markdown(recipe))
        _emit(log, "Writing recipe output files...")
        atomic_write_json(recipe_path, _model_dump(recipe))
        if options.enable_recipe_review:
            create_recipe_review(recipe, folder)
        atomic_write_text(note_path, final_note)
        _validate_generated_artifacts(transcript, recipe, final_note, transcript_path, recipe_path, note_path)
        try:
            write_quality_report(folder)
        except Exception as exc:  # noqa: BLE001
            stage_errors.append(f"quality: {exc}")
            _emit(log, f"Quality report skipped: {exc}")

        _set_job_stage(job, "recipe", "done")
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
        job_path = _write_job(folder, job)
        return RecipeJobResult(folder, note_path, recipe_path, transcript_path, final_note, job_path, stage_errors)
    except Exception as exc:
        _set_job_stage(job, "recipe", "failed", str(exc))
        job.update({"status": "failed", "finished_at": _now(), "error": str(exc), "stage_errors": stage_errors})
        _write_job(folder, job)
        raise
    finally:
        if not options.keep_media:
            shutil.rmtree(media_dir, ignore_errors=True)


def generate_recipe_note(options: RecipeJobOptions, log: LogCallback | None = None) -> RecipeJobResult:
    raw = capture_raw_material(options, log=log, preserve_media=options.keep_media)
    return generate_recipe_from_raw(raw.output_folder, options, log=log)


def crawl_and_archive_creator(
    url: str,
    cookies: str | None,
    out: str,
    log: LogCallback | None = None,
) -> CreatorArchiveResult:
    """Crawl a creator and atomically archive a stable text list plus manifest."""
    _emit(log, "Extracting creator videos and nested collections...")
    crawl = crawl_creator_videos(url, cookies=cookies)
    creator_name = sanitize_filename(crawl.uploader, max_length=70)
    creators_root = ensure_dir(Path(out) / "creators")
    existing_dirs = sorted(path for path in creators_root.glob(f"{crawl.uid}-*") if path.is_dir())
    creator_dir = existing_dirs[0] if existing_dirs else ensure_dir(creators_root / f"{crawl.uid}-{creator_name}")
    links_path = creator_dir / "video_links.txt"
    manifest_path = creator_dir / "creator.json"
    atomic_write_text(links_path, "".join(f"{video.url}\n" for video in crawl.videos))
    atomic_write_json(
        manifest_path,
        {
            "uid": crawl.uid,
            "uploader": crawl.uploader,
            "source_url": url,
            "crawled_at": _now(),
            "complete": crawl.complete,
            "warnings": crawl.warnings,
            "video_count": len(crawl.videos),
            "videos": [asdict(video) for video in crawl.videos],
        },
    )
    _emit(log, f"Archived {len(crawl.videos)} video links to {links_path}")
    return CreatorArchiveResult(crawl, creator_dir, links_path, manifest_path)


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


StageCallback = Callable[[str, str, Path | None, str | None], None]


def _recipe_options(options: BatchJobOptions, url: str) -> RecipeJobOptions:
    return RecipeJobOptions(
        url=url,
        cookies=options.cookies,
        out=options.out,
        no_screenshot=options.no_screenshot,
        whisper_model=options.whisper_model,
        language=options.language,
        keep_media=options.keep_media,
        no_llm_summary=options.no_llm_summary,
        require_llm=options.require_llm,
        require_screenshot=options.require_screenshot,
        llm_provider=options.llm_provider,
        openai_model=options.openai_model,
        local_llm_command=options.local_llm_command,
        codex_model=options.codex_model,
        codex_profile=options.codex_profile,
        llm_cli_extra_instructions=options.llm_cli_extra_instructions,
        max_recipe_steps=options.max_recipe_steps,
        max_step_images=options.max_step_images,
        enable_recipe_review=options.enable_recipe_review,
    )


def _process_batch_url(
    options: BatchJobOptions,
    url: str,
    log: LogCallback | None = None,
    *,
    output_folder: Path | None = None,
    stage_callback: StageCallback | None = None,
) -> BatchJobItemResult:
    def notify(stage: str, status: str, folder: Path | None = None, error: str | None = None) -> None:
        if stage_callback:
            stage_callback(stage, status, folder, error)

    job_options = _recipe_options(options, url)
    complete_existing = find_history_by_url(options.out, url) if options.skip_existing else None
    if (
        complete_existing
        and options.target_stage == "recipe"
        and _recipe_output_meets_requirements(complete_existing.output_folder, job_options)
    ):
        notify("raw", "done", complete_existing.output_folder)
        notify("recipe", "done", complete_existing.output_folder)
        _emit(log, f"Skipped existing recipe output: {complete_existing.output_folder}")
        return BatchJobItemResult(
            url=url,
            status="skipped",
            output_folder=complete_existing.output_folder,
            note_path=complete_existing.note_path,
        )
    existing = find_history_record_by_url(options.out, url) if options.skip_existing else None
    folder = output_folder or (existing.output_folder if existing else None)
    try:
        if folder and is_raw_output(folder):
            notify("raw", "done", folder)
        else:
            notify("raw", "running", folder)
            if options.target_stage == "recipe":
                result = generate_recipe_note(job_options, log=log)
                folder = result.output_folder
                notify("raw", "done", folder)
                notify("recipe", "done", folder)
                return BatchJobItemResult(url=url, status="done", output_folder=folder, note_path=result.note_path)
            raw = capture_raw_material(job_options, log=log)
            folder = raw.output_folder
            notify("raw", "done", folder)

        if options.target_stage == "raw":
            _emit(log, f"Raw material ready: {folder}")
            return BatchJobItemResult(url=url, status="raw_ready", output_folder=folder)

        complete = find_history_by_url(options.out, url) if options.skip_existing else None
        if (
            complete
            and complete.output_folder == folder
            and _recipe_output_meets_requirements(folder, job_options)
        ):
            notify("recipe", "done", folder)
            _emit(log, f"Skipped existing recipe output: {folder}")
            return BatchJobItemResult(url=url, status="skipped", output_folder=folder, note_path=complete.note_path)

        notify("recipe", "running", folder)
        result = generate_recipe_from_raw(folder, job_options, log=log)
        notify("recipe", "done", folder)
        return BatchJobItemResult(url=url, status="done", output_folder=folder, note_path=result.note_path)
    except Exception as exc:  # noqa: BLE001
        failed_stage = "recipe" if folder and is_raw_output(folder) and options.target_stage == "recipe" else "raw"
        notify(failed_stage, "failed", folder, str(exc))
        _emit(log, f"Failed: {exc}")
        return BatchJobItemResult(url=url, status="failed", output_folder=folder, error=str(exc))


def _batch_options_snapshot(options: BatchJobOptions) -> dict:
    snapshot = asdict(options)
    # URLs already live in items. Repeating a large creator list in every
    # options snapshot makes each atomic checkpoint unnecessarily expensive.
    snapshot.pop("urls", None)
    return snapshot


def _run_persistent_batch(options: BatchJobOptions, log: LogCallback | None = None) -> BatchJobResult:
    if options.batch_id and options.resume_mode != "new":
        state = load_batch_state(options.batch_id)
    else:
        state = create_batch_state(options.urls, _batch_options_snapshot(options), batch_id=options.batch_id)

    state.options.pop("urls", None)  # compact batches created by older versions
    stage_options = state.options.setdefault("stage_options", {})
    if isinstance(stage_options, dict):
        stage_options[options.target_stage] = _batch_options_snapshot(options)

    if options.target_stage == "recipe" and (options.require_llm or options.require_screenshot):
        for item in state.items:
            if (
                item.stages["raw"].status != "done"
                or item.stages["recipe"].status != "done"
                or not item.output_folder
            ):
                continue
            folder = Path(item.output_folder)
            job_options = _recipe_options(options, item.url)
            if _recipe_output_meets_requirements(folder, job_options):
                continue
            recipe_stage = item.stages["recipe"]
            recipe_stage.status = "pending"
            recipe_stage.error = None
            recipe_stage.started_at = None
            recipe_stage.finished_at = None
            item.status = "raw_ready"
            item.error = None
            item.note_path = None
            item.finished_at = None
    save_batch_state(state)

    items_to_process = selectable_items(state, options.resume_mode, options.target_stage)
    results: list[BatchJobItemResult] = []
    for idx, item in enumerate(items_to_process, start=1):
        _emit(log, f"[{idx}/{len(items_to_process)}] {item.url}")
        active_stage = "raw" if item.stages["raw"].status != "done" else "recipe"
        item.status = f"{active_stage}_running"
        item.error = None
        item.started_at = _now()
        item.finished_at = None
        item.stages[active_stage].status = "running"
        item.stages[active_stage].started_at = _now()
        item.stages[active_stage].finished_at = None
        save_batch_state(state)

        def update_stage(stage_name: str, status: str, folder: Path | None, error: str | None) -> None:
            stage = item.stages[stage_name]
            stage.status = status
            stage.error = error
            if status == "running":
                stage.started_at = _now()
                stage.finished_at = None
                item.status = f"{stage_name}_running"
            elif status in {"done", "failed"}:
                stage.finished_at = _now()
                if status == "failed":
                    item.status = "failed"
            if folder:
                item.output_folder = str(folder)
            item.error = error

        result = _process_batch_url(
            options,
            item.url,
            log=log,
            output_folder=Path(item.output_folder) if item.output_folder else None,
            stage_callback=update_stage,
        )
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
