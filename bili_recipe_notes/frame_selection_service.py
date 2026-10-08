"""Generate independent, validated frame candidates without changing recipes.

The app never imports the selector's ML dependencies. A small supervisor owns a
native process lock and watches its parent's stdin, so interrupted workers cannot
leave a model running alongside a resumed batch.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Callable

from . import frame_selection
from .data_paths import state_dir
from .review_policy import fingerprint_review_content, read_review_inputs
from .storage import atomic_write_json


SCHEMA_VERSION = 1
# Bump when sampling or judging changes can improve an earlier selection.
# Kept separate from the storage schema so usable old images remain available.
SELECTION_STRATEGY_VERSION = 4
MODEL_TIMEOUT_SECONDS = 6 * 60 * 60
DEFAULT_OPTIONS = {
    "requirement": frame_selection.DEFAULT_REQUIREMENT,
    "python_executable": None,
    "model": None,
    "device": "cpu",
    "threads": 16,
    "interval": 2.0,
    "coarse_limit": 24,
    "fine_fps": 5.0,
    "top_k": 3,
    "fine_limit": 8,
    "cookies": None,
    "keep_video": False,
}


class FrameSelectionError(RuntimeError):
    """Selection failed; an earlier valid candidate remains available."""


class FrameSelectionConfigurationError(FrameSelectionError):
    """The independent selector environment is not ready."""


def normalize_frame_options(options: dict | None = None) -> dict:
    """Validate cheap configuration before queuing work; never load the model."""
    if options is not None and not isinstance(options, dict):
        raise ValueError("选图参数必须是对象")
    unknown = set(options or {}) - set(DEFAULT_OPTIONS)
    if unknown:
        raise ValueError("未知选图参数：" + ", ".join(sorted(unknown)))
    values = {**DEFAULT_OPTIONS, **(options or {})}
    requirement = values["requirement"]
    if not isinstance(requirement, str) or not requirement.strip():
        raise ValueError("选图要求不能为空")
    values["requirement"] = requirement.strip()
    if values["device"] not in ("cpu", "cuda"):
        raise ValueError("选图设备必须是 cpu 或 cuda")
    for key in ("threads", "coarse_limit", "top_k", "fine_limit"):
        minimum = 2 if key == "coarse_limit" else 1
        if type(values[key]) is not int or values[key] < minimum:
            raise ValueError(f"{key} 必须是至少为 {minimum} 的整数")
    for key in ("interval", "fine_fps"):
        value = values[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} 必须为有限正数")
        values[key] = float(value)
    for key in ("python_executable", "model", "cookies"):
        value = values[key]
        if value is not None and not isinstance(value, (str, Path)):
            raise ValueError(f"{key} 必须为路径字符串")
        values[key] = (str(value).strip() or None) if value is not None else None
    if type(values["keep_video"]) is not bool:
        raise ValueError("keep_video 必须是布尔值")
    return values


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"选图路径超出允许目录：{path}")
    return resolved


def _locations(out_dir: str | Path, folder: str | Path) -> tuple[Path, Path, Path, str]:
    root = Path(out_dir).expanduser().resolve()
    recipe_folder = Path(folder).expanduser()
    if not recipe_folder.is_absolute():
        # Match the repository's APIs: a cwd-relative existing folder is valid;
        # otherwise callers may supply a folder relative to the output root.
        recipe_folder = recipe_folder if recipe_folder.is_dir() else root / recipe_folder
    recipe_folder = _inside(recipe_folder, root)
    relative = recipe_folder.relative_to(root).as_posix()
    if relative == ".":
        raise ValueError("请选择输出根目录内的菜谱文件夹")
    selection = _inside(root / ".frame-selection", root)
    if selection != root / ".frame-selection":
        raise ValueError("选图结果目录不能是符号链接")
    key = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:32]
    return root, recipe_folder, selection, key


def _fingerprint(folder: Path) -> tuple[str, dict]:
    inputs = read_review_inputs(folder)
    return fingerprint_review_content(inputs), inputs


def _time_ranges(value: object) -> list[dict] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise ValueError("菜品视频时间范围不能为空")
    ranges = []
    for span in value:
        if not isinstance(span, dict):
            raise ValueError("菜品视频时间范围无效")
        start, end = span.get("start"), span.get("end")
        if (any(type(number) not in (int, float) or not math.isfinite(number) for number in (start, end))
                or start < 0 or end <= start):
            raise ValueError("菜品视频时间范围必须为有限非负数，且 end 大于 start")
        ranges.append({"start": float(start), "end": float(end)})
    merged = []
    for span in sorted(ranges, key=lambda item: item["start"]):
        if merged and span["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], span["end"])
        else:
            merged.append(span)
    return merged


def _recipe_time_ranges(recipe: dict) -> list[dict] | None:
    value = recipe.get("source_time_ranges")
    if recipe.get("dish_id") and not value:
        raise ValueError("子菜谱缺少视频时间范围，不能搜索全片")
    return _time_ranges(value)


def _in_ranges(timestamp: float, ranges: list[dict] | None) -> bool:
    return ranges is None or any(span["start"] <= timestamp < span["end"] for span in ranges)


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"选图记录不是 JSON 对象：{path}")
    return value


def _image(path: Path, root: Path, dimensions: object) -> Path:
    from PIL import Image

    if path.is_symlink():
        raise ValueError("候选图片不能是符号链接")
    validated = _inside(path, root)
    if not validated.is_file():
        raise ValueError("候选图片不存在")
    if (not isinstance(dimensions, list) or len(dimensions) != 2
            or any(type(value) is not int or value <= 0 for value in dimensions)):
        raise ValueError("候选图片尺寸无效")
    try:
        with Image.open(validated) as picture:
            if picture.format != "PNG" or list(picture.size) != dimensions:
                raise ValueError("候选 PNG 的格式或尺寸与记录不一致")
            picture.verify()
        with Image.open(validated) as picture:
            picture.load()
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError(f"候选 PNG 无法完整解码：{exc}") from exc
    return validated


def _timestamp(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("选图相对时间戳无效")
    return float(value)


def _manifest_view(manifest: dict, selection: Path) -> dict:
    result = dict(manifest)
    for field in ("result_path", "stdout_path", "stderr_path"):
        relative = result.get(field)
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("选图日志路径无效")
        result[field] = str(_inside(selection / relative, selection))
    if result["status"] in {"ok", "needs_review"}:
        relative = result.get("image_path")
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("选图图片路径无效")
        result["image_path"] = str(_image(selection / relative, selection, result.get("dimensions")))
        result["timestamp"] = _timestamp(result.get("timestamp"))
        if not _in_ranges(result["timestamp"], _time_ranges(result.get("time_ranges"))):
            raise ValueError("候选图片时间超出菜品视频范围")
    elif any(result.get(key) is not None for key in ("image_path", "timestamp", "dimensions")):
        raise ValueError("没有匹配画面的记录不能包含图片")
    return result


def load_frame_candidate(out_dir: str | Path, folder: str | Path, *, include_stale: bool = False) -> dict | None:
    """Read a current candidate, rebasing its portable image path; never write."""
    try:
        root, folder, selection, key = _locations(out_dir, folder)
        path = _inside(selection / "candidates" / f"{key}.json", selection)
        manifest = _read_object(path)
        fingerprint, inputs = _fingerprint(folder)
        time_ranges = _recipe_time_ranges(inputs["recipe"])
        if (manifest.get("schema_version") != SCHEMA_VERSION
                or manifest.get("folder") != folder.relative_to(root).as_posix()
                or manifest.get("status") not in ("ok", "not_found", "needs_review")):
            return None
        for field in ("title", "source_url", "reason", "generated_at"):
            if not isinstance(manifest.get(field), str):
                return None
        outdated_search = (manifest["status"] in {"not_found", "needs_review"}
                           and manifest.get("selection_strategy_version") != SELECTION_STRATEGY_VERSION)
        if (manifest.get("fingerprint") != fingerprint or outdated_search
                or _time_ranges(manifest.get("time_ranges")) != time_ranges):
            if include_stale:
                return {**manifest, "original_status": manifest["status"], "status": "stale",
                        "image_path": None, "timestamp": None, "valid": False,
                        "result_path": None, "stdout_path": None, "stderr_path": None}
            return None
        return _manifest_view(manifest, selection)
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def validate_frame_environment(options: dict | None = None) -> dict:
    """Resolve an installed selector without starting it or downloading media."""
    options = normalize_frame_options(options)
    script = frame_selection.TOOL_ROOT / "select_frame.py"
    model = Path(options["model"] or frame_selection.TOOL_ROOT / "models" / "Qwen2.5-VL-3B-Instruct").expanduser().resolve()
    try:
        if not script.is_file():
            raise FileNotFoundError("缺少选图子模块；请运行 git submodule update --init external/video-frame-choose")
        # Retain the venv symlink but anchor relative overrides before the
        # supervisor changes cwd to import the application package.
        python = os.path.abspath(frame_selection._python_executable(options["python_executable"]))
        if not model.is_dir():
            raise FileNotFoundError(f"本地选图模型目录不存在：{model}")
    except OSError as exc:
        raise FrameSelectionConfigurationError(str(exc)) from exc
    return {**options, "python_executable": python, "model": str(model)}


def _command(video: Path, output: Path, options: dict) -> list[str]:
    options = validate_frame_environment(options)
    script = frame_selection.TOOL_ROOT / "select_frame.py"
    result = [options["python_executable"], str(script.resolve()), "--input", str(video), "--output", str(output),
              "--requirement", options["requirement"], "--model", options["model"]]
    for key in ("device", "threads", "interval", "coarse_limit", "fine_fps", "top_k", "fine_limit"):
        result.extend(("--" + key.replace("_", "-"), str(options[key])))
    return result


def _dish_context(inputs: dict) -> dict:
    ranges = _recipe_time_ranges(inputs["recipe"])
    segments = []
    for row in inputs["transcript"]:
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        start = row.get("start", row.get("start_time"))
        end = row.get("end", row.get("end_time", start))
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start):
            continue
        if ranges is None:
            segments.append({"start": start, "end": end, "text": text})
        else:
            for span in ranges:
                lo, hi = max(start, span["start"]), min(end, span["end"])
                if lo < hi or (start == end and span["start"] <= start < span["end"]):
                    segments.append({"start": lo, "end": hi, "text": text})
    title = str(inputs["recipe"].get("title") or "").strip() or "当前菜品"
    return {"schema_version": 1, "dish_title": title,
            "segments": segments}


def _existing_video(folder: Path) -> Path | None:
    for directory, pattern in ((folder / "media", "cover-video.*"),
                               (folder / "media", "video.*"), (folder, "cover-video.*")):
        for path in sorted(directory.glob(pattern)):
            if path.is_file() and path.suffix.lower() not in (".part", ".ytdl", ".json"):
                return _inside(path, folder)
    return None


class _Progress:
    def __init__(self, callback: Callable[[dict], None] | None, options: dict):
        self.callback = callback
        self.value = {"phase": "preparing", "frames_completed": 0,
                      "frames_total": options["coarse_limit"] + options["fine_limit"],
                      "phase_completed": 0, "phase_total": 0, "timestamp": None,
                      "message": "准备选图"}
        self.coarse_total = options["coarse_limit"]
        self.fine_total = options["fine_limit"]

    def emit(self, phase: str | None = None, message: str | None = None, **extra) -> None:
        if phase is not None:
            self.value["phase"] = phase
        if message is not None:
            self.value["message"] = message
        self.value.update(extra)
        if self.callback is not None:
            self.callback(dict(self.value))

    def line(self, line: str) -> None:
        message = line.rstrip()
        if "加载本地模型" in line:
            self.emit("loading", message)
        elif found := re.search(r"粗选\s+(\d+)\s+帧", line):
            self.coarse_total = int(found[1])
            self.emit("coarse", message, phase_completed=0, phase_total=self.coarse_total,
                      frames_total=self.coarse_total + self.fine_total)
        elif found := re.search(r"精选\s+(\d+)\s+帧", line):
            self.fine_total = int(found[1])
            self.emit("fine", message, phase_completed=0, phase_total=self.fine_total,
                      frames_total=self.coarse_total + self.fine_total)
        elif found := re.search(r"frame=\d+\s+time=([\d.]+)s\s+match=", line):
            self.emit(message=message, frames_completed=self.value["frames_completed"] + 1,
                      phase_completed=self.value["phase_completed"] + 1,
                      timestamp=float(found[1]))


def _stop_process(process: subprocess.Popen, *, group: bool = False) -> None:
    if process.poll() is not None:
        return
    if group and os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif group and os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if group and os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait()


def _supervise(config_path: Path, lock_path: Path) -> int:
    """Internal child: release the native lock only after the model is reaped."""
    stopped = threading.Event()

    def watch_parent() -> None:
        try:
            # BufferedReader locks held by a blocked daemon thread can abort
            # Python during interpreter shutdown. Raw fd reads hold no such lock.
            while os.read(sys.stdin.fileno(), 1):
                pass
        finally:
            stopped.set()

    threading.Thread(target=watch_parent, daemon=True).start()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    config = _read_object(config_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    child = None
    with lock_path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()

            def acquire() -> None:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            def acquire() -> None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        while not stopped.is_set():
            try:
                acquire()
                break
            except (BlockingIOError, PermissionError):
                stopped.wait(0.1)
        if stopped.is_set():
            return 130
        try:
            kwargs = {"stdin": subprocess.DEVNULL, "start_new_session": os.name == "posix"}
            if os.name == "posix":
                # Retain the lock even if the supervisor itself is forcibly
                # killed. A later model must wait for this model to exit.
                kwargs["pass_fds"] = (lock.fileno(),)
            child = subprocess.Popen(config["command"], **kwargs)
            deadline = time.monotonic() + config.get("timeout_seconds", MODEL_TIMEOUT_SECONDS)
            while child.poll() is None:
                if stopped.wait(0.1):
                    _stop_process(child, group=True)
                    return 130
                if time.monotonic() >= deadline:
                    print("选图运行超过时限，已终止本次模型进程。", file=sys.stderr, flush=True)
                    _stop_process(child, group=True)
                    return 124
            return child.returncode
        finally:
            if child is not None:
                _stop_process(child, group=True)


def _run_selector(command: list[str], run: Path, progress: _Progress) -> int:
    config = run / "command.json"
    atomic_write_json(config, {"command": command, "timeout_seconds": MODEL_TIMEOUT_SECONDS}, backup=False)
    lock = state_dir() / "frame-selection" / "model.lock"
    if getattr(sys, "frozen", False):
        supervisor = [sys.executable, "--frame-selection-supervisor", str(config), str(lock)]
    else:
        supervisor = [sys.executable, "-m", "bili_recipe_notes.frame_selection_service",
                      "--supervise", str(config), str(lock)]
    progress.emit("waiting_model", "等待本地视觉模型空闲")
    with (run / "stdout.log").open("w", encoding="utf-8") as stdout, (run / "stderr.log").open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(supervisor, stdin=subprocess.PIPE, stdout=stdout,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                   cwd=Path(__file__).resolve().parent.parent)
        try:
            assert process.stderr is not None
            for line in process.stderr:
                stderr.write(line)
                stderr.flush()
                progress.line(line)
            return process.wait()
        finally:
            # Closing stdin signals all failure paths, including callback errors.
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                _stop_process(process)
            if process.stderr is not None:
                stderr.write(process.stderr.read())
                process.stderr.close()


def _validate_dish_judgment(judgment: dict, status: str) -> None:
    """Recheck the selector's structured decision without importing ML code."""
    assessment = judgment.get("dish_assessment")
    if not isinstance(assessment, dict):
        raise ValueError("菜品候选缺少独立条件判断")
    if any(type(assessment.get(key)) is not bool for key in ("food_visible", "constraints_satisfied")):
        raise ValueError("菜品候选的布尔条件无效")
    for key, choices in (
        ("completion", {"finished", "raw", "cooking", "uncertain"}),
        ("identity", {"compatible", "conflict", "uncertain"}),
        ("presentation", {"serving", "demonstration", "process", "uncertain"}),
    ):
        if not isinstance(assessment.get(key), str) or assessment[key] not in choices:
            raise ValueError("菜品候选的分类条件无效：" + key)
    rejected = (not assessment["food_visible"] or not assessment["constraints_satisfied"]
                or assessment["completion"] in {"raw", "cooking"}
                or assessment["identity"] == "conflict" or assessment["presentation"] == "process")
    accepted = (not rejected and assessment["completion"] == "finished"
                and assessment["identity"] == "compatible"
                and assessment["presentation"] in {"serving", "demonstration"})
    decision = "reject" if rejected else "accept" if accepted else "needs_review"
    if (judgment.get("decision") != decision
            or judgment.get("matches_requirement") is not accepted
            or (status == "ok" and decision != "accept")
            or (status == "needs_review" and decision != "needs_review")):
        raise ValueError("菜品候选状态与独立条件判断不一致")


def _validate_result(output: Path, video: Path, options: dict, exit_code: int,
                     *, require_dish_assessment: bool = False, time_ranges: list[dict] | None = None) -> dict:
    path = _inside(output / "result.json", output)
    result = _read_object(path)
    status = result.get("status")
    if (result.get("schema_version") != SCHEMA_VERSION
            or result.get("fallback") is not False
            or status not in ("ok", "not_found", "needs_review")
            or exit_code != (2 if status == "not_found" else 0)):
        raise ValueError(f"选图退出码或结果状态异常：exit={exit_code}, status={status}")
    if result.get("input") != str(video) or result.get("requirement") != options["requirement"]:
        raise ValueError("选图结果的输入视频或要求不一致")
    if result.get("metadata") != str(path) or not isinstance(result.get("reason"), str):
        raise ValueError("选图结果的记录路径或理由无效")
    parameters = result.get("parameters") or {}
    if not isinstance(parameters, dict) or _time_ranges(parameters.get("time_ranges")) != time_ranges:
        raise ValueError("选图结果的视频时间范围与本次菜品不一致")
    if status in {"ok", "needs_review"}:
        if not isinstance(result.get("image"), str) or not isinstance(result.get("selected"), dict):
            raise ValueError("选图结果缺少图片或所选帧")
        image = Path(result["image"])
        if not image.is_absolute():
            raise ValueError("选图结果图片必须使用绝对路径")
        result["image"] = str(_image(image, output, result.get("dimensions")))
        selected = result["selected"]
        result["timestamp"] = _timestamp(selected.get("relative_timestamp_seconds"))
        if not _in_ranges(result["timestamp"], time_ranges):
            raise ValueError("所选帧超出菜品视频时间范围")
        if (type(selected.get("frame_index")) is not int or selected["frame_index"] < 0
                or type(selected.get("pts")) is not int
                or not isinstance(selected.get("time_base"), str)
                or not isinstance(selected.get("judgment"), dict)):
            raise ValueError("所选帧的定位或匹配信息无效")
        judgment = selected["judgment"]
        if status == "ok" and judgment.get("matches_requirement") is not True:
            raise ValueError("所选帧的定位或匹配信息无效")
        if status == "needs_review" and not require_dish_assessment:
            raise ValueError("自定义选图要求未启用菜品待确认候选")
        if require_dish_assessment or "dish_assessment" in judgment or "decision" in judgment:
            _validate_dish_judgment(judgment, status)
        time_base = Fraction(selected["time_base"])
        timestamp = selected.get("timestamp_seconds")
        if (time_base <= 0 or type(timestamp) not in (int, float) or not math.isfinite(timestamp)
                or not math.isclose(float(selected["pts"] * time_base), timestamp, abs_tol=1e-6)):
            raise ValueError("所选帧的 PTS 与时间戳不一致")
    else:
        if result.get("image") is not None or result.get("selected") is not None or any(output.rglob("*.png")):
            raise ValueError("未找到匹配画面的结果不能包含候选图片")
        result["dimensions"] = None
        result["timestamp"] = None
    return result


def generate_frame_candidate(folder: str | Path, *, out_dir: str | Path, options: dict,
                             progress: Callable[[dict], None] | None = None) -> dict:
    """Generate one independent candidate and atomically publish its manifest."""
    options = normalize_frame_options(options)
    root, folder, selection, key = _locations(out_dir, folder)
    fingerprint, inputs = _fingerprint(folder)
    recipe, source = inputs["recipe"], inputs["source"]
    time_ranges = _recipe_time_ranges(recipe)
    source_url = str(recipe.get("source_url") or source.get("webpage_url") or source.get("source_url") or source.get("url") or "")
    run = _inside(selection / "runs" / key / uuid.uuid4().hex, selection)
    run.mkdir(parents=True, exist_ok=False)
    work = run / "download"
    output = run / "result"
    reporter = _Progress(progress, options)
    public_options = {key: value for key, value in options.items() if key != "cookies"}
    # Dish identity belongs to the separate context. Repeating it as a mandatory
    # phrase in the request makes generic visual names look like mismatches.
    effective_requirement = options["requirement"]
    attempt = {"schema_version": SCHEMA_VERSION, "selection_strategy_version": SELECTION_STRATEGY_VERSION,
               "folder": folder.relative_to(root).as_posix(),
               "fingerprint": fingerprint, "started_at": _now(), "options": public_options,
               "effective_requirement": effective_requirement, "time_ranges": time_ranges}
    atomic_write_json(run / "run.json", attempt, backup=False)
    try:
        reporter.emit()
        # Verify installation before a potentially expensive network download.
        options = validate_frame_environment(options)
        public_options = {key: value for key, value in options.items() if key != "cookies"}
        attempt["options"] = public_options
        atomic_write_json(run / "run.json", attempt, backup=False)
        effective_options = {**options, "requirement": effective_requirement}
        command = _command(Path("/unused"), output, effective_options)
        if time_ranges is not None:
            ranges_path = run / "time-ranges.json"
            atomic_write_json(ranges_path, {"schema_version": 1, "ranges": time_ranges}, backup=False)
            command.extend(("--time-ranges", str(ranges_path)))
        if options["requirement"] == frame_selection.DEFAULT_REQUIREMENT:
            context_path = run / "dish-context.json"
            atomic_write_json(context_path, _dish_context(inputs), backup=False)
            command.extend(("--dish-context", str(context_path), "--review-uncertain"))
        video = _existing_video(folder)
        if video is None:
            if not source_url:
                raise FrameSelectionError("菜谱没有本地视频或来源链接")
            from .downloader import download_cover_video

            work.mkdir(exist_ok=False)
            reporter.emit("downloading", "下载用于选图的视频")
            video = _inside(Path(download_cover_video(source_url, work, cookies=options["cookies"])), work)
            if not video.is_file():
                raise FrameSelectionError("视频下载没有生成可读取的文件")
        command[command.index("--input") + 1] = str(video)
        exit_code = _run_selector(command, run, reporter)
        if exit_code not in (0, 2):
            details = (run / "stderr.log").read_text(encoding="utf-8", errors="replace")[-4000:]
            error_class = FrameSelectionConfigurationError if any(marker in details for marker in (
                "ModuleNotFoundError", "ImportError", "No module named", "本地模型目录不存在",
                "no file named pytorch_model", "does not appear to have a file named",
            )) else FrameSelectionError
            raise error_class(f"选图进程失败（退出码 {exit_code}）。日志：{run / 'stderr.log'}\n{details.strip()}")
        reporter.emit("validating", "校验独立候选图片")
        result = _validate_result(output, video, effective_options, exit_code,
                                  require_dish_assessment=options["requirement"] == frame_selection.DEFAULT_REQUIREMENT,
                                  time_ranges=time_ranges)
        current, _ = _fingerprint(folder)
        if current != fingerprint:
            raise FrameSelectionError("选图期间菜谱、字幕或来源已改变，结果未应用")
        manifest = {"schema_version": SCHEMA_VERSION, "status": result["status"],
                    "selection_strategy_version": SELECTION_STRATEGY_VERSION,
                    "time_ranges": time_ranges,
                    "folder": folder.relative_to(root).as_posix(), "fingerprint": fingerprint,
                    "title": str(recipe.get("title") or folder.name), "source_url": source_url,
                    "image_path": Path(result["image"]).relative_to(selection).as_posix() if result.get("image") else None,
                    "timestamp": result["timestamp"], "dimensions": result["dimensions"],
                    "reason": result["reason"], "generated_at": _now(), "options": public_options,
                    "run_id": run.name, "run_dir": run.relative_to(selection).as_posix(),
                    "result_path": (output / "result.json").relative_to(selection).as_posix(),
                    "stdout_path": (run / "stdout.log").relative_to(selection).as_posix(),
                    "stderr_path": (run / "stderr.log").relative_to(selection).as_posix()}
        atomic_write_json(run / "candidate.json", manifest, backup=False)
        atomic_write_json(run / "run.json", {**attempt, "status": result["status"], "finished_at": _now()}, backup=False)
        latest = _inside(selection / "candidates" / f"{key}.json", selection)
        reporter.emit("completed", result["reason"])
        atomic_write_json(latest, manifest)
        return _manifest_view(manifest, selection)
    except BaseException as exc:
        atomic_write_json(run / "run.json", {**attempt, "status": "failed", "finished_at": _now(),
                                             "error": str(exc)}, backup=False)
        if isinstance(exc, (FrameSelectionError, KeyboardInterrupt, SystemExit)):
            raise
        raise FrameSelectionError(str(exc)) from exc
    finally:
        if not options["keep_video"] and work.exists():
            # This directory was created exclusively for this attempt. Never
            # remove media found in the recipe, or any user-provided input.
            shutil.rmtree(work)


def supervisor_main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        raise ValueError("选图 supervisor 需要配置路径和锁路径")
    return _supervise(Path(arguments[0]), Path(arguments[1]))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--supervise":
        raise SystemExit(supervisor_main(sys.argv[2:]))
    raise SystemExit("This module's command line is reserved for its subprocess supervisor.")
