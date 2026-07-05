from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path


def _plain_lines(markdown: str) -> list[str]:
    lines: list[str] = []
    for raw in markdown.splitlines():
        line = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", raw)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = line.lstrip("#").strip()
        line = line.removeprefix("- ").strip()
        if line:
            lines.append(line)
    return lines


def export_obsidian(note_path: Path, output_path: Path | None = None) -> Path:
    output = output_path or note_path.with_name("note.obsidian.md")
    markdown = note_path.read_text(encoding="utf-8")
    title = _plain_lines(markdown)[0] if _plain_lines(markdown) else note_path.parent.name
    content = f"---\ntitle: {title}\nsource: bili-recipe-notes\n---\n\n{markdown}"
    output.write_text(content, encoding="utf-8")
    return output


def export_pdf(note_path: Path, output_path: Path | None = None) -> Path:
    output = output_path or note_path.with_suffix(".pdf")
    markdown = note_path.read_text(encoding="utf-8")
    lines = _plain_lines(markdown)[:45]
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfgen import canvas

        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        c = canvas.Canvas(str(output), pagesize=A4)
        width, height = A4
        y = height - 48
        c.setFont("STSong-Light", 12)
        for line in lines:
            c.drawString(48, y, line[:90])
            y -= 18
            if y < 48:
                c.showPage()
                c.setFont("STSong-Light", 12)
                y = height - 48
        c.save()
        return output
    except Exception:
        _write_basic_pdf(output, lines)
        return output


def export_docx(note_path: Path, output_path: Path | None = None) -> Path:
    output = output_path or note_path.with_suffix(".docx")
    markdown = note_path.read_text(encoding="utf-8")
    lines = _plain_lines(markdown)
    try:
        from docx import Document

        doc = Document()
        if lines:
            doc.add_heading(lines[0], level=1)
            for line in lines[1:]:
                doc.add_paragraph(line)
        doc.save(output)
        return output
    except Exception:
        _write_basic_docx(output, lines)
        return output


def export_note(note_path: Path, kind: str) -> Path:
    if kind == "pdf":
        return export_pdf(note_path)
    if kind == "docx":
        return export_docx(note_path)
    if kind == "obsidian":
        return export_obsidian(note_path)
    raise ValueError(f"Unsupported export kind: {kind}")


def _pdf_hex(text: str) -> str:
    return "<FEFF" + text.encode("utf-16-be", errors="ignore").hex().upper() + ">"


def _write_basic_pdf(output: Path, lines: list[str]) -> None:
    text_ops = ["BT", "/F1 12 Tf", "50 780 Td"]
    for idx, line in enumerate(lines[:35]):
        if idx:
            text_ops.append("0 -18 Td")
        text_ops.append(f"{_pdf_hex(line[:80])} Tj")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 595 842] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    body = b"%PDF-1.4\n"
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{idx} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref_offset = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets[1:]:
        body += f"{offset:010d} 00000 n \n".encode("ascii")
    body += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
            "ascii"
        )
    )
    output.write_bytes(body)


def _write_basic_docx(output: Path, lines: list[str]) -> None:
    paragraphs = "\n".join(
        f"<w:p><w:r><w:t>{html.escape(line)}</w:t></w:r></w:p>"
        for line in lines
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}<w:sectPr/></w:body></w:document>"
    )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        zf.writestr("word/document.xml", document_xml)
