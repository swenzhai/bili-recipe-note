from __future__ import annotations

import json

import pytest

from bili_recipe_notes.history import scan_history


@pytest.mark.parametrize("quality", [None, b"{ broken report", b"\xff\xfeoriginal", b'{"score":"invalid"}'])
def test_library_browsing_never_repairs_or_creates_reports(tmp_path, quality):
    folder = tmp_path / "eggs"
    folder.mkdir()
    (folder / "recipe.json").write_text(json.dumps({
        "title": "炒蛋", "source_url": "https://example.com/eggs",
        "ingredients": [{"name": "鸡蛋"}], "seasonings": [], "tools": [],
        "steps": [{"title": "炒熟", "start_time": 0, "action": "将鸡蛋放入锅中炒熟"}],
        "summary_tips": [], "uncertain_points": [],
    }, ensure_ascii=False), encoding="utf-8")
    (folder / "note.md").write_text("# 原始生成笔记\n", encoding="utf-8")
    if quality is not None:
        (folder / "quality.json").write_bytes(quality)
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.iterdir()}

    items = scan_history(tmp_path)
    repeated = scan_history(tmp_path)

    assert items[0].title == "炒蛋"
    assert items[0].quality_score is not None
    assert repeated[0].quality_score == items[0].quality_score
    assert before == {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.iterdir()}
