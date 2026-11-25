from pathlib import Path

import av
import pytest
from PIL import Image
from reportlab.pdfgen.canvas import Canvas

from bili_recipe_notes.frame_selection_pdf import is_pdf_source, prepare_pdf


def make_pdf(path):
    canvas = Canvas(str(path), pagesize=(73, 101))
    for color in ('red', 'green', 'blue'):
        canvas.setFillColor(color)
        canvas.rect(0, 0, 73, 101, fill=1)
        canvas.showPage()
    canvas.save()


def test_pdf_scope_lossless_transport_and_original_page(tmp_path):
    source = tmp_path / '中文 book.pdf'
    make_pdf(source)
    work = tmp_path / 'work'
    work.mkdir()
    before = source.read_bytes()
    video, pages = prepare_pdf(source.as_uri().replace('file:', 'pdf:') + '#page=1',
                               {'pdf_pages': {'start': 2, 'end': 3}}, work)
    assert [p['page_number'] for p in pages] == [2, 3]
    with av.open(str(video)) as container:
        frames = list(container.decode(video=0))
    assert [float(f.pts * f.time_base) for f in frames] == [0, 1]
    for frame, page in zip(frames, pages):
        with Image.open(page['path']) as image:
            assert frame.to_image().tobytes() == image.tobytes()
    assert source.read_bytes() == before


def test_fragment_selects_only_source_page(tmp_path):
    source = tmp_path / 'book.pdf'
    make_pdf(source)
    _, pages = prepare_pdf(source.as_uri() + '#page=2', {}, tmp_path)
    assert [p['page_number'] for p in pages] == [2]


@pytest.mark.parametrize('scope', [{'start': 0, 'end': 2}, {'start': 2, 'end': 4},
                                  {'start': 3, 'end': 2}, {'start': True, 'end': 2}, []])
def test_invalid_scope_rejected(tmp_path, scope):
    source = tmp_path / 'book.pdf'
    make_pdf(source)
    with pytest.raises(ValueError, match='PDF 页码'):
        prepare_pdf(source.as_uri(), {'pdf_pages': scope}, tmp_path)


@pytest.mark.parametrize('url', ['pdf:///book.pdf#page=2', 'file:///book.PDF', 'https://example.test/book.pdf?download=1'])
def test_pdf_sources(url):
    assert is_pdf_source(url)
    assert not is_pdf_source('https://example.test/video.mp4')
