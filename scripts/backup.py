"""Consistent SQLite backup, and restore into a new destination only."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import backend
import sqlite3
from backend.core import Store

def main():
    p=argparse.ArgumentParser()
    p.add_argument('operation',choices=['backup','restore'])
    p.add_argument('source',type=Path)
    p.add_argument('destination',type=Path)
    args=p.parse_args()
    src=args.source.resolve(); dst=args.destination.resolve()
    if not src.is_file() or dst.exists() or src==dst: p.error('Origem deve existir e destino deve ser novo')
    dst.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(src.as_uri()+'?mode=ro',uri=True) as a, sqlite3.connect(dst) as b:
        if a.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('Origem corrompida')
        a.backup(b)
        if b.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('Destino corrompido')
    print(f'{args.operation}: integridade ok; {dst.name}')

if __name__=='__main__': main()
