from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bili_recipe_notes import frame_selection, frame_selection_service as service


SELECTOR = r'''
import argparse, json, os, sys, time
from pathlib import Path
from PIL import Image
p = argparse.ArgumentParser()
p.add_argument('--input')
p.add_argument('--output')
p.add_argument('--requirement')
a, extra = p.parse_known_args()
assert '--allow-fallback' not in extra
config = json.loads((Path(__file__).parent / 'scenario.json').read_text())
mode = config.get('mode', 'ok')
if mode == 'argument_error':
    print('argument error', file=sys.stderr, flush=True)
    raise SystemExit(2)
event_path = config.get('events')
def event(name):
    if event_path:
        with open(event_path, 'a') as f:
            f.write(json.dumps({'event': name, 'pid': os.getpid(), 'time': time.time()}) + '\n')
event('start')
if mode == 'hang':
    def quit(*args):
        event('terminated')
        raise SystemExit(130)
    import signal
    signal.signal(signal.SIGTERM, quit)
print('加载本地模型：synthetic', file=sys.stderr, flush=True)
print('粗选 2 帧；要求：' + a.requirement, file=sys.stderr, flush=True)
print('frame=0 time=0.000s match=False missing', file=sys.stderr, flush=True)
if mode == 'hang':
    while True:
        time.sleep(0.1)
time.sleep(config.get('delay', 0))
print('frame=20 time=2.000s match=True clear', file=sys.stderr, flush=True)
print('精选 1 帧', file=sys.stderr, flush=True)
print('frame=25 time=2.500s match=True best', file=sys.stderr, flush=True)
out = Path(a.output)
out.mkdir(parents=True)
image = out / 'best_frame.png'
status = 'not_found' if mode == 'not_found' else ('fallback' if mode == 'fallback' else 'ok')
if mode == 'escape':
    image = Path(__file__).parent / 'outside.png'
result = dict(schema_version=1, status=status, fallback=mode == 'fallback',
              input=a.input, requirement=a.requirement, metadata=str(out / 'result.json'),
              selected=None, image=None, reason='synthetic candidate', dimensions=None)
if status != 'not_found':
    Image.new('RGB', (24, 12), '#55aa11').save(image)
    result.update(image=str(image), dimensions=[24, 12], selected={
        'relative_timestamp_seconds': 2.5, 'timestamp_seconds': 2.5,
        'frame_index': 25, 'pts': 2500, 'time_base': '1/1000',
        'judgment': {'matches_requirement': True, 'reason': 'synthetic candidate'}})
if mode == 'dimensions':
    result['dimensions'] = [25, 12]
if mode == 'timestamp':
    result['selected']['relative_timestamp_seconds'] = float('nan')
if mode == 'mismatch':
    result['selected']['judgment']['matches_requirement'] = False
if mode == 'pts':
    result['selected']['pts'] = 99
(out / 'result.json').write_text(json.dumps(result))
print(json.dumps(result), flush=True)
event('end')
raise SystemExit(config.get('exit', 2 if status == 'not_found' else 0))
'''


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tool = tmp_path / "tool with spaces"
    tool.mkdir()
    (tool / "select_frame.py").write_text(SELECTOR, encoding="utf-8")
    scenario = tool / "scenario.json"
    scenario.write_text("{}", encoding="utf-8")
    model = tool / "model"
    model.mkdir()
    monkeypatch.setattr(frame_selection, "TOOL_ROOT", tool)
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", str(tmp_path / "shared data"))
    root = tmp_path / "recipes"
    folder = root / "creator" / "dish"
    folder.mkdir(parents=True)
    (folder / "recipe.json").write_text(json.dumps({"title": "合成菜谱", "source_url": "https://example.test/video", "ingredients": [], "seasonings": [], "steps": []}), encoding="utf-8")
    (folder / "source.json").write_text('{"title":"source"}', encoding="utf-8")
    (folder / "transcript.json").write_text('[{"text":"做法","start":0}]', encoding="utf-8")
    (folder / "media").mkdir()
    (folder / "media" / "cover-video.mp4").write_bytes(b"synthetic video")
    (folder / "cover.jpg").write_bytes(b"original cover")
    return root, folder, {"python_executable": sys.executable, "model": str(model)}, scenario


def _snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def _events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    pytest.fail("Synthetic subprocess did not reach the expected state")


def test_generation_is_independent_unique_portable_and_reports_live_progress(setup, tmp_path):
    root, folder, options, _ = setup
    before = _snapshot(folder)
    progress = []
    first = service.generate_frame_candidate(folder, out_dir=root, options=options, progress=progress.append)
    assert first["status"] == "ok" and first["timestamp"] == 2.5
    assert first["dimensions"] == [24, 12]
    assert Path(first["image_path"]).is_relative_to(root / ".frame-selection")
    assert service.load_frame_candidate(root, folder) == first
    assert _snapshot(folder) == before
    frames = [p for p in progress if p["message"].startswith("frame=")]
    assert [p["frames_completed"] for p in frames] == [1, 2, 3]
    assert frames[-1]["phase"] == "fine" and frames[-1]["frames_total"] == 3
    assert frames[-1]["phase_completed"] == 1 and frames[-1]["phase_total"] == 1
    assert {p["phase"] for p in progress} >= {"waiting_model", "loading", "coarse", "fine", "validating", "completed"}
    run = root / ".frame-selection" / first["run_dir"]
    assert "粗选 2 帧" in (run / "stderr.log").read_text()
    assert json.loads((run / "stdout.log").read_text())["status"] == "ok"
    second = service.generate_frame_candidate(folder, out_dir=root, options=options)
    assert first["image_path"] != second["image_path"]
    assert Path(first["image_path"]).is_file()
    assert (run / "candidate.json").is_file()
    moved = tmp_path / "moved recipes"
    shutil.copytree(root, moved)
    loaded = service.load_frame_candidate(moved, moved / folder.relative_to(root))
    assert loaded["status"] == "ok" and Path(loaded["image_path"]).is_relative_to(moved)


def test_semantic_fingerprint_ignores_display_edits_but_expires_changed_content(setup):
    root, folder, options, _ = setup
    original = service.generate_frame_candidate(folder, out_dir=root, options=options)
    recipe = json.loads((folder / "recipe.json").read_text())
    recipe.update(cover_image="new.jpg", taste_rating=5, categories=["肉类"])
    (folder / "recipe.json").write_text(json.dumps(recipe))
    assert service.load_frame_candidate(root, folder)["image_path"] == original["image_path"]
    recipe["title"] = "改变核心内容"
    (folder / "recipe.json").write_text(json.dumps(recipe))
    assert service.load_frame_candidate(root, folder) is None
    stale = service.load_frame_candidate(root, folder, include_stale=True)
    assert stale["status"] == "stale" and stale["image_path"] is None and not stale["valid"]
    assert Path(original["image_path"]).is_file()


@pytest.mark.parametrize("filename,contents", [("source.json", '{"title":"new source"}'), ("transcript.json", '[{"text":"new transcript"}]')])
def test_source_and_transcript_changes_invalidate_candidate(setup, filename, contents):
    root, folder, options, _ = setup
    service.generate_frame_candidate(folder, out_dir=root, options=options)
    (folder / filename).write_text(contents)
    assert service.load_frame_candidate(root, folder) is None


def test_valid_not_found_is_normal_and_has_no_image(setup):
    root, folder, options, scenario = setup
    scenario.write_text('{"mode":"not_found"}')
    result = service.generate_frame_candidate(folder, out_dir=root, options=options)
    assert result["status"] == "not_found"
    assert result["image_path"] is None and result["timestamp"] is None
    assert service.load_frame_candidate(root, folder) == result
    assert not list((root / ".frame-selection").rglob("*.png"))


@pytest.mark.parametrize("scenario_value", [
    {"mode": "argument_error"}, {"mode": "fallback"}, {"mode": "dimensions"},
    {"mode": "escape"}, {"mode": "timestamp"}, {"mode": "mismatch"}, {"mode": "pts"},
    {"exit": 2}, {"mode": "not_found", "exit": 0},
])
def test_invalid_subprocess_results_preserve_previous_candidate(setup, scenario_value):
    root, folder, options, scenario = setup
    original = service.generate_frame_candidate(folder, out_dir=root, options=options)
    scenario.write_text(json.dumps(scenario_value))
    with pytest.raises(service.FrameSelectionError):
        service.generate_frame_candidate(folder, out_dir=root, options=options)
    assert service.load_frame_candidate(root, folder) == original
    assert any(json.loads(p.read_text())["status"] == "failed" for p in (root / ".frame-selection").rglob("run.json"))


@pytest.mark.parametrize("keep", [False, True])
def test_download_uses_owned_directory_and_cleans_only_its_video(setup, monkeypatch, keep):
    root, folder, options, _ = setup
    (folder / "media" / "cover-video.mp4").unlink()
    before = _snapshot(folder)
    downloads = []

    def download(url, output_dir, cookies=None):
        assert url == "https://example.test/video" and cookies == "private cookies.txt"
        path = output_dir / "cover-video.mp4"
        path.write_bytes(b"downloaded synthetic video")
        downloads.append(path)
        return path

    monkeypatch.setattr("bili_recipe_notes.downloader.download_cover_video", download)
    result = service.generate_frame_candidate(folder, out_dir=root, options={**options, "keep_video": keep, "cookies": "private cookies.txt"})
    assert downloads[0].is_file() is keep
    assert _snapshot(folder) == before
    assert "cookies" not in result["options"]
    run = root / ".frame-selection" / result["run_dir"]
    assert "private cookies" not in (run / "command.json").read_text()
    assert "private cookies" not in (run / "candidate.json").read_text()


def test_failed_download_cleans_partial_owned_files(setup, monkeypatch):
    root, folder, options, _ = setup
    (folder / "media" / "cover-video.mp4").unlink()
    downloads = []

    def download(url, output_dir, cookies=None):
        downloads.append(output_dir)
        (output_dir / "cover-video.part").write_bytes(b"partial")
        raise OSError("synthetic interrupted download")

    monkeypatch.setattr("bili_recipe_notes.downloader.download_cover_video", download)
    with pytest.raises(service.FrameSelectionError, match="interrupted download"):
        service.generate_frame_candidate(folder, out_dir=root, options=options)
    assert not downloads[0].exists()
    assert not (root / ".frame-selection" / "candidates").exists()


def test_input_changes_during_processing_do_not_publish_candidate(setup):
    root, folder, options, _ = setup

    def update_source(progress):
        if progress["phase"] == "fine":
            (folder / "source.json").write_text('{"title":"changed during processing"}')

    with pytest.raises(service.FrameSelectionError, match="已改变"):
        service.generate_frame_candidate(folder, out_dir=root, options=options, progress=update_source)
    assert service.load_frame_candidate(root, folder) is None
    assert list((root / ".frame-selection").rglob("best_frame.png"))


def test_reader_rejects_tampered_image_path_and_does_not_create_missing_state(setup):
    root, folder, options, _ = setup
    assert service.load_frame_candidate(root, folder) is None
    assert not (root / ".frame-selection").exists()
    result = service.generate_frame_candidate(folder, out_dir=root, options=options)
    latest = next((root / ".frame-selection" / "candidates").glob("*.json"))
    manifest = json.loads(latest.read_text())
    manifest["image_path"] = "../creator/dish/cover.jpg"
    latest.write_text(json.dumps(manifest))
    assert service.load_frame_candidate(root, folder) is None
    assert Path(result["image_path"]).is_file()


def test_configuration_errors_are_distinct_and_do_not_download(setup, monkeypatch):
    root, folder, options, _ = setup
    (folder / "media" / "cover-video.mp4").unlink()
    monkeypatch.setattr("bili_recipe_notes.downloader.download_cover_video", lambda *args, **kwargs: pytest.fail("must validate environment before downloading"))
    with pytest.raises(service.FrameSelectionConfigurationError):
        service.generate_frame_candidate(folder, out_dir=root, options={**options, "python_executable": "/missing/python"})


def test_corrupt_candidate_png_does_not_break_read_only_browsing(setup):
    root, folder, options, _ = setup
    result = service.generate_frame_candidate(folder, out_dir=root, options=options)
    image = Path(result["image_path"])
    image.write_bytes(image.read_bytes()[:45])
    before = _snapshot(root)
    assert service.load_frame_candidate(root, folder) is None
    assert _snapshot(root) == before


def test_symlinked_state_directory_cannot_write_outside_library(setup, tmp_path):
    root, folder, options, _ = setup
    target = tmp_path / "outside"
    target.mkdir()
    (root / ".frame-selection").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="超出允许目录"):
        service.generate_frame_candidate(folder, out_dir=root, options=options)
    assert list(target.iterdir()) == []


def test_relative_python_override_preserves_virtual_environment_path(setup, monkeypatch):
    root, folder, options, _ = setup
    monkeypatch.chdir(Path(sys.executable).parent.parent)
    relative = os.path.relpath(sys.executable)
    command = service._command(folder / "media" / "cover-video.mp4", root / "new", service.normalize_frame_options({**options, "python_executable": relative}))
    assert command[0] == sys.executable


@pytest.mark.parametrize("options", [{"allow_fallback": True}, {"coarse_limit": 1}, {"fine_limit": 0}, {"threads": True}, {"interval": float("inf")}, {"device": "auto"}, {"keep_video": "yes"}, {"requirement": " "}])
def test_invalid_options_fail_before_work(options):
    with pytest.raises(ValueError):
        service.normalize_frame_options(options)


def test_two_libraries_share_one_native_model_lock(setup, tmp_path):
    root, folder, options, scenario = setup
    second_root = tmp_path / "second library"
    second_folder = second_root / "dish"
    shutil.copytree(folder, second_folder)
    events = tmp_path / "events.jsonl"
    scenario.write_text(json.dumps({"events": str(events), "delay": 0.35}))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(service.generate_frame_candidate, f, out_dir=r, options=options)
                for r, f in ((root, folder), (second_root, second_folder))]
        assert [job.result(timeout=15)["status"] for job in jobs] == ["ok", "ok"]
    assert [event["event"] for event in _events(events)] == ["start", "end", "start", "end"]


@pytest.mark.skipif(os.name != "posix", reason="synthetic process termination uses POSIX signals")
def test_callback_failure_terminates_model_before_a_retry(setup, tmp_path):
    root, folder, options, scenario = setup
    events = tmp_path / "events.jsonl"
    scenario.write_text(json.dumps({"events": str(events), "mode": "hang"}))

    def fail(progress):
        if progress["phase"] == "coarse":
            raise RuntimeError("callback interrupted")

    with pytest.raises(service.FrameSelectionError, match="callback interrupted"):
        service.generate_frame_candidate(folder, out_dir=root, options=options, progress=fail)
    assert [event["event"] for event in _events(events)] == ["start", "terminated"]
    scenario.write_text(json.dumps({"events": str(events)}))
    assert service.generate_frame_candidate(folder, out_dir=root, options=options)["status"] == "ok"


@pytest.mark.skipif(os.name != "posix", reason="synthetic process termination uses POSIX signals")
def test_silent_model_timeout_terminates_process_and_keeps_logs(setup, tmp_path, monkeypatch):
    root, folder, options, scenario = setup
    events = tmp_path / "events.jsonl"
    scenario.write_text(json.dumps({"events": str(events), "mode": "hang"}))
    monkeypatch.setattr(service, "MODEL_TIMEOUT_SECONDS", 0.3)
    with pytest.raises(service.FrameSelectionError, match="退出码 124"):
        service.generate_frame_candidate(folder, out_dir=root, options=options)
    assert [event["event"] for event in _events(events)] == ["start", "terminated"]
    assert service.load_frame_candidate(root, folder) is None


@pytest.mark.skipif(os.name != "posix", reason="forcefully killing synthetic parent is POSIX-specific")
def test_parent_death_stops_model_and_allows_safe_restart(setup, tmp_path):
    root, folder, options, scenario = setup
    events = tmp_path / "events.jsonl"
    scenario.write_text(json.dumps({"events": str(events), "mode": "hang"}))
    config = tmp_path / "parent-config.json"
    config.write_text(json.dumps({"root": str(root), "folder": str(folder), "options": options, "tool": str(scenario.parent)}))
    harness = tmp_path / "parent.py"
    harness.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "from bili_recipe_notes import frame_selection, frame_selection_service as s\n"
        "v=json.loads(Path(sys.argv[1]).read_text())\n"
        "frame_selection.TOOL_ROOT=Path(v['tool'])\n"
        "s.generate_frame_candidate(v['folder'],out_dir=v['root'],options=v['options'])\n"
    )
    env = {**os.environ, "PYTHONPATH": str(Path(service.__file__).resolve().parent.parent)}
    process = subprocess.Popen([sys.executable, str(harness), str(config)], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _until(lambda: _events(events))
        process.kill()
        process.wait(timeout=5)
        _until(lambda: any(e["event"] == "terminated" for e in _events(events)))
        scenario.write_text(json.dumps({"events": str(events)}))
        assert service.generate_frame_candidate(folder, out_dir=root, options=options)["status"] == "ok"
        assert [event["event"] for event in _events(events)] == ["start", "terminated", "start", "end"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
