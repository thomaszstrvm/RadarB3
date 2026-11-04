"""Install the verified official SQLite DLL inside this project only (Windows x64)."""
import hashlib
import io
import json
import re
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '3.54.1'
EXPECTED = 'c2d09c8babb9399277055c057c97a53f1ffd7c5f11d0c1f2118ffdfa39f2daa4'

def main():
    with urllib.request.urlopen('https://sqlite.org/download.html', timeout=30) as response:
        page = response.read(2_000_000).decode()
    match = re.search(r'PRODUCT,3\.54\.1,([^,\s]*sqlite-dll-win-x64-3540100\.zip),(\d+),([a-f0-9]{64})', page)
    if not match or match[3] != EXPECTED:
        raise RuntimeError('Official release or checksum changed; review before installation.')
    url = 'https://sqlite.org/' + match[1]
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(5_000_001)
    if len(data) != int(match[2]) or hashlib.sha3_256(data).hexdigest() != EXPECTED:
        raise RuntimeError('SQLite download integrity mismatch')
    target = ROOT / 'vendor' / 'sqlite'
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if 'sqlite3.dll' not in names or any('/' in n or '\\' in n or '..' in n for n in names):
            raise RuntimeError('Unexpected archive paths')
        dll = archive.read('sqlite3.dll')
        (target / 'sqlite3.dll').write_bytes(dll)
    record = {'version': VERSION, 'url': url, 'archive_sha3_256': EXPECTED,
              'dll_sha256': hashlib.sha256(dll).hexdigest(), 'license': 'public domain',
              'license_url': 'https://sqlite.org/copyright.html'}
    (target / 'provenance.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps(record))

if __name__ == '__main__':
    main()
