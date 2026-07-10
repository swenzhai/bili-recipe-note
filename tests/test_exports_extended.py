from __future__ import annotations

import zipfile
from pathlib import Path

from bili_recipe_notes.exports import export_docx, export_obsidian, export_pdf, export_recipe_bundle


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
