"""Local parser worker: no application imports and no API credentials."""
import base64
import io
import json
import sys
from pathlib import Path

from files import parse_file


def run():
    payload = json.loads(sys.stdin.buffer.read(14_000_000))
    data = base64.b64decode(payload['data'], validate=True)
    ext = Path(payload['name']).suffix.lower()
    if ext in {'.jpg', '.jpeg', '.png'}:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = 16_000_000
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16_000_000:
                raise ValueError()
            image.load()
            image.thumbnail((1600, 1600))
            clean = io.BytesIO()
            image.convert('RGB').save(clean, format='JPEG')
        return {'image': base64.b64encode(clean.getvalue()).decode(), 'mime': 'image/jpeg'}
    try:
        return {'requirements': parse_file(payload['name'], data)}
    except ValueError as error:
        if ext != '.pdf' or 'no text layer' not in str(error):
            raise
        import pymupdf
        with pymupdf.open(stream=data, filetype='pdf') as document:
            page = document[0]
            if page.rect.width * page.rect.height > 4_000_000:
                raise ValueError()
            png = page.get_pixmap(matrix=pymupdf.Matrix(1, 1)).tobytes('png')
        return {'image': base64.b64encode(png).decode(), 'mime': 'image/png', 'first_page_only': True}


if __name__ == '__main__':
    try:
        print(json.dumps(run(), ensure_ascii=False))
    except Exception:
        print(json.dumps({'error': 'Файл повреждён, зашифрован или превышает ограничения обработки.'}, ensure_ascii=False))
