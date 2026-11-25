"""PDF page adapter for the official visual selector (one lossless frame/page)."""
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen
import shutil

from .data_paths import resolve_stored_path


def is_pdf_source(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == 'pdf' or parsed.path.lower().endswith('.pdf')


def prepare_pdf(url: str, recipe: dict, work: Path) -> tuple[Path, list[dict]]:
    import av
    import pypdfium2 as pdfium
    from PIL import Image

    parsed = urlparse(url)
    if parsed.scheme in {'pdf', 'file', ''}:
        if parsed.netloc:
            raise ValueError('PDF 本地来源不能包含远端主机')
        source = resolve_stored_path(unquote(parsed.path))
    elif parsed.scheme in {'http', 'https'}:
        source = work / 'source.pdf'
        with urlopen(Request(url, headers={'User-Agent': 'bili-recipe-notes'}), timeout=60) as response, source.open('wb') as target:
            shutil.copyfileobj(response, target)
    else:
        raise ValueError('不支持的 PDF 来源协议')
    pages = []
    with pdfium.PdfDocument(str(source)) as document:
        scope = recipe.get('pdf_pages')
        if scope is not None:
            if not isinstance(scope, dict):
                raise ValueError('PDF 页码范围无效')
            start, end = scope.get('start'), scope.get('end')
        else:
            fragment = parse_qs(parsed.fragment).get('page')
            start = int(fragment[0]) if fragment else 1
            end = start if fragment else len(document)
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(document):
            raise ValueError('PDF 页码范围超出文档或格式无效')
        for number in range(start, end + 1):
            with closing(document[number - 1]) as page:
                bitmap = page.render(scale=150 / 72)
                try:
                    image = bitmap.to_pil().convert('RGB')
                    path = work / f'page-{number}.png'
                    image.save(path)
                    pages.append({'page_number': number, 'path': path, 'dimensions': list(image.size)})
                finally:
                    bitmap.close()
    # A lossless transport keeps the existing local/remote selector protocol.
    # Export uses the original rendered page, never a padded transport frame.
    width = max(row['dimensions'][0] for row in pages)
    height = max(row['dimensions'][1] for row in pages)
    video = work / 'pdf-pages.mkv'
    with av.open(str(video), 'w') as container:
        stream = container.add_stream('ffv1', rate=1)
        stream.width, stream.height, stream.pix_fmt = width, height, 'bgr0'
        for index, row in enumerate(pages):
            canvas = Image.new('RGB', (width, height), 'white')
            with Image.open(row['path']) as image:
                canvas.paste(image, (0, 0))
            frame = av.VideoFrame.from_image(canvas)
            frame.pts = index
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return video, pages
