"""Validate upload containers before parsing. Raw uploads are never persisted."""
import asyncio
import base64
import io
import json
import os
import sys
import zipfile
from pathlib import Path

from fastapi import HTTPException

EXTENSIONS = {'.csv', '.xlsx', '.xls', '.docx', '.pdf', '.jpg', '.jpeg', '.png'}


def validate_file(name, data):
    ext = Path(name).suffix.lower()
    if ext not in EXTENSIONS or not data:
        raise HTTPException(422, 'Неподдерживаемый или пустой файл.')
    if len(data) > 10_000_000:
        raise HTTPException(413, 'Файл превышает 10 МБ.')
    signatures = {'.pdf': b'%PDF-', '.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8\xff', '.jpeg': b'\xff\xd8\xff', '.xls': b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'}
    if ext in signatures and not data.startswith(signatures[ext]):
        raise HTTPException(422, 'Содержимое файла не соответствует расширению.')
    if ext in {'.docx', '.xlsx'}:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                required = 'word/document.xml' if ext == '.docx' else 'xl/workbook.xml'
                if required not in archive.namelist() or len(members) > 1000 or sum(m.file_size for m in members) > 30_000_000:
                    raise ValueError()
                for member in members:
                    name = member.filename.lower().replace('\\', '/')
                    if '..' in name.split('/') or name.startswith('/') or member.flag_bits & 1:
                        raise ValueError()
                    if any(marker in name for marker in ('vbaproject', '/embeddings/', 'externallinks/')):
                        raise ValueError()
        except (zipfile.BadZipFile, ValueError, OSError):
            raise HTTPException(422, 'Повреждённый документ, слишком большой архив или активное содержимое.') from None
    if ext == '.csv':
        try:
            data.decode('utf-8-sig')
        except UnicodeError:
            raise HTTPException(422, 'CSV должен быть в UTF-8.') from None
        if b'\x00' in data:
            raise HTTPException(422, 'CSV содержит бинарные данные.')
    return ext


async def parse_isolated(name, data):
    """Dedicated child with minimal environment and timeout, not a full OS sandbox."""
    env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH'}}
    env['PYTHONIOENCODING'] = 'utf-8'
    worker = Path(__file__).with_name('upload_worker.py')
    child = await asyncio.create_subprocess_exec(sys.executable, str(worker), stdin=asyncio.subprocess.PIPE,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    try:
        raw, _ = await asyncio.wait_for(child.communicate(json.dumps({'name': Path(name).name,
            'data': base64.b64encode(data).decode('ascii')}).encode()), timeout=20)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        child.kill()
        await child.wait()
        raise HTTPException(422, 'Обработка файла заняла слишком много времени.') from None
    if child.returncode or len(raw) > 8_000_000:
        raise HTTPException(422, 'Файл не удалось безопасно обработать.')
    try:
        result = json.loads(raw)
    except ValueError:
        raise HTTPException(422, 'Файл не удалось прочитать.') from None
    if result.get('error'):
        raise HTTPException(422, result['error'])
    return result

