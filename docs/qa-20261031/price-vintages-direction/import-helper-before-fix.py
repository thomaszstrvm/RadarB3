"""Prepared one-capture import. Execution requires coordinator's separate GO."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import backend
import sqlite3
import argparse
import hashlib
import json
from backend.core import Store,wal_supported
from backend.price_vintages import persist_vintage

BODY=ROOT/'docs/qa-20261031/vale3-gates/brapi-public-response.json'
EXPECTED='ca776418bafd6f621b6e49d78b7a04fc013c822cb3f2e8336fc8c5c6f3f5921e'
FETCHED='2026-11-01T02:17:43.178290Z'

def inactive(con,profile):
    if json.loads(con.execute("SELECT value_json FROM settings WHERE key='profile'").fetchone()[0])!=profile:raise RuntimeError('profile_mismatch')
    if con.execute('SELECT 1 FROM worker_lease').fetchone():raise RuntimeError('worker_must_be_stopped')
    if con.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','retry_wait')").fetchone():raise RuntimeError('pending_jobs_preserved')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--profile',choices=('demo','real'),required=True);args=parser.parse_args()
    assert wal_supported()
    body=BODY.read_bytes()
    if hashlib.sha256(body).hexdigest()!=EXPECTED:raise RuntimeError('reviewed_public_body_changed')
    with sqlite3.connect(args.db.resolve().as_uri()+'?mode=rw',uri=True) as con:inactive(con,args.profile)
    store=Store(args.db.resolve(),args.profile)
    original=store._check_worker_fence
    def fence(con):
        original(con);inactive(con,args.profile)
    store._check_worker_fence=fence
    result=persist_vintage(store,'brapi:VALE3',body,fetched_at=FETCHED)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
