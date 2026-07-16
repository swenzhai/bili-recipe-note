from __future__ import annotations

import json
import uuid
import zipfile
from pathlib import Path

from bili_recipe_notes.exports import export_docx, export_obsidian, export_pdf, export_recipe_bundle
from bili_recipe_notes.mobile_sync import MobileSyncStore


def test_exports_keep_full_text_and_bundle_images(tmp_path: Path) -> None:
    folder = tmp_path / "Demo Recipe"
    images = folder / "images"
    media = folder / "media"
    images.mkdir(parents=True)
    media.mkdir()
    (images / "step_01.jpg").write_bytes(b"not-a-real-image")
    (media / "video.mp4").write_bytes(b"private-media")
    (folder / "recipe.json").write_text("{}", encoding="utf-8")
    (folder / "transcript.json").write_text("[]", encoding="utf-8")
    lines = ["# Demo: Recipe", "", *[f"- 第 {index} 行内容" for index in range(100)], "", "最后一行"]
    note = folder / "note.md"
    note.write_text("\n".join(lines) + "\n\n![](images/step_01.jpg)\n", encoding="utf-8")

    obsidian = export_obsidian(note)
    pdf = export_pdf(note)
    docx = export_docx(note)
    bundle = export_recipe_bundle(note)

    assert 'title: "Demo: Recipe"' in obsidian.read_text(encoding="utf-8")
    assert pdf.read_bytes().startswith(b"%PDF-")
    with zipfile.ZipFile(docx) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
        assert "最后一行" in document
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert {"note.md", "recipe.json", "transcript.json", "images/step_01.jpg"} <= names
        assert "media/video.mp4" not in names


def test_bundle_does_not_include_unrelated_files(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    bundle = export_recipe_bundle(note)

    with zipfile.ZipFile(bundle) as archive:
        assert archive.namelist() == ["note.md"]


def test_bundle_includes_mobile_practice_logs(tmp_path: Path) -> None:
    folder = tmp_path / "outputs" / "recipe"
    folder.mkdir(parents=True)
    note = folder / "note.md"
    note.write_text("# 番茄炒蛋\n", encoding="utf-8")
    (folder / "recipe.json").write_text(
        json.dumps(
            {
                "title": "番茄炒蛋",
                "source_url": "https://www.bilibili.com/video/BV1demo",
                "steps": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (folder / "job.json").write_text(json.dumps({"bvid": "BV1demo", "cid": "100"}), encoding="utf-8")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_id = json.loads((folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    paired = store.pair_device(
        store.issue_pairing_credential("http://192.168.1.2:8765").pairing_token,
        "iPhone",
    )
    log_id = str(uuid.uuid4())
    store.sync(
        paired["device_id"],
        0,
        [
            {
                "op_id": str(uuid.uuid4()),
                "entity_type": "practice_log",
                "entity_id": log_id,
                "action": "upsert",
                "base_version": 0,
                "payload": {
                    "id": log_id,
                    "recipe_id": recipe_id,
                    "cooked_on": "2026-07-16",
                    "notes": "少放一点盐",
                },
            }
        ],
    )

    bundle = export_recipe_bundle(note)

    with zipfile.ZipFile(bundle) as archive:
        exported = json.loads(archive.read("practice-logs.json"))
        assert exported[0]["notes"] == "少放一点盐"
