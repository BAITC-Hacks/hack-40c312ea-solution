"""Portable demo/live entry point: python run.py [--demo] [--port 8000]."""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='EKT AI Engineer MVP')
    parser.add_argument('--demo', action='store_true', help='Use clearly labeled synthetic offline products; no provider calls')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if args.demo or os.getenv('EKT_PASSWORD', '') in {'', 'replace_me'}:
        os.environ['DEMO_MODE'] = '1'
    if os.getenv('DEMO_MODE') == '1':
        for name in ('OPENAI_API_KEY', 'NVIDIA_API_KEY'):
            os.environ.pop(name, None)
        print('OFFLINE DEMO: synthetic products, no external model or catalog calls.')
    else:
        print('LIVE EKT: catalog and current details use the configured server credentials.')
    import uvicorn
    uvicorn.run('app.main:app', host=args.host, port=args.port)
