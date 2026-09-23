import csv
import io
import re
from pathlib import Path


def _rows_to_lines(rows):
    lines = []
    for row in rows:
        cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
        if cells:
            lines.append(' '.join(cells)[:400])
    return lines


def parse_file(filename: str, data: bytes) -> list[dict]:
    ext = Path(filename).suffix.lower()
    lines = []
    if ext == '.csv':
        text = data.decode('utf-8-sig', errors='replace')
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=',;\t')
        lines = _rows_to_lines(csv.reader(io.StringIO(text), dialect))
    elif ext == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines = _rows_to_lines(book.active.iter_rows(values_only=True))
    elif ext == '.xls':
        import xlrd
        sheet = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
        lines = _rows_to_lines(sheet.row_values(i) for i in range(sheet.nrows))
    elif ext == '.docx':
        from docx import Document
        doc = Document(io.BytesIO(data))
        lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            lines += _rows_to_lines([[c.text for c in row.cells] for row in table.rows])
    elif ext == '.pdf':
        import fitz
        doc = fitz.open(stream=data, filetype='pdf')
        lines = [line.strip() for page in doc for line in page.get_text().splitlines() if line.strip()]
        if not lines:
            raise ValueError('PDF has no text layer; scanned PDF requires a configured vision model.')
    elif ext in {'.jpg', '.jpeg', '.png'}:
        raise ValueError('Image recognition requires a configured vision model.')
    else:
        raise ValueError('Unsupported file type')
    output = []
    for line in lines[:100]:
        if len(line) < 3:
            continue
        qty = 1
        match = re.search(r'(?:кол[-. ]?во|qty|quantity|шт)\s*[:=]?\s*(\d+)', line, re.I)
        if match:
            qty = max(1, int(match.group(1)))
        output.append({'description': line, 'quantity': qty})
    return output
