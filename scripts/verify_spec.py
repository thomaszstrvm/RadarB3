"""Verify the unchanged specification against its original ZIP and manifest."""
import hashlib
import json
from pathlib import Path
import zipfile

root=Path(__file__).resolve().parents[1]
archive=root/'original/radar-b3-codex.zip'
expected='edaae6248fe97e0141cc20f3881be2e30f11d978d10491558f5622e39f6c8471'
if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected: raise SystemExit('ZIP original divergente')
with zipfile.ZipFile(archive) as z:
    if len(z.infolist())!=35: raise SystemExit('Inventário ZIP divergente')
    manifest=json.loads(z.read('radar-b3-codex/MANIFEST.json'))
    for item in manifest['files']:
        data=(root/item['path']).read_bytes()
        if len(data)!=item['size_bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
            raise SystemExit('Especificação alterada: '+item['path'])
print(f'OK: ZIP SHA256 e {len(manifest["files"])} arquivos originais intactos. Código adicional separado do manifesto original.')
