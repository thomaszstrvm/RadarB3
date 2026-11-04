"""One disposable copy of a closed historical backup; no live DB/network/AI."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import backend  # Approved SQLite preload must precede sqlite3.
import sqlite3
import hashlib
import importlib.util
import json
import shutil

from backend.core import Store,digest,dumps,wal_supported
from backend.evidence import build_bundle,validate_bundle
from backend.market_calendar import import_calendar_package
from backend.timeutil import iso

QA=Path(__file__).resolve().parent
SOURCE=ROOT/'docs/qa-20261031/manual-ui-operable/fundamentals-after.sqlite3'
COPY=QA/'calendar-candidate-copy.sqlite3'
SOURCES=ROOT/'docs/qa-20261031/data-readiness/calendar/sources'


def tables(con):
    names=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name:{'count':len(rows),'hash':digest(sorted(dumps(list(row)) for row in rows))}
            for name in names for rows in [con.execute('SELECT * FROM "'+name+'"').fetchall()]}


def main():
    assert sqlite3.sqlite_version_info>=(3,50,0) and wal_supported()
    assert not COPY.exists() and not (QA/'candidate-copy-result.json').exists()
    source_hash=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    shutil.copy2(SOURCE,COPY)
    store=Store(COPY,'real')
    with store.connect() as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not con.execute('SELECT 1 FROM worker_lease').fetchone()
        assert not con.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','retry_wait')").fetchone()
        before=tables(con)
        old_bundles=con.execute('SELECT bundle_id,payload_json FROM evidence_bundles').fetchall()
        analyses=con.execute('SELECT analysis_id,bundle_id FROM analyses').fetchall()
        validated={row['bundle_id']:validate_bundle(json.loads(row['payload_json']),ROOT/'schemas') for row in old_bundles}
        imported=import_calendar_package(con,json.loads((QA/'calendar-package-v1.json').read_text(encoding='utf-8')),SOURCES,expected_profile='real')
        after_import=tables(con)
    assert set(after_import)-set(before)=={'calendar_packages'}
    assert after_import['market_sessions']['count']==before['market_sessions']['count']+96
    assert after_import['audit_events']['count']==before['audit_events']['count']+1
    assert all(before[name]==after_import[name] for name in before if name not in ('market_sessions','audit_events'))
    bundle=build_bundle(store,'brapi:VALE3',iso(),ROOT/'schemas')
    assert bundle['schema_version']=='1.1' and validate_bundle(bundle,ROOT/'schemas')
    with store.connect() as con:
        after=tables(con)
        assert all(after[name]==after_import[name] for name in after_import if name!='evidence_bundles')
        for row in old_bundles:
            assert con.execute('SELECT payload_json FROM evidence_bundles WHERE bundle_id=?',(row['bundle_id'],)).fetchone()[0]==row['payload_json']
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==source_hash
    (QA/'candidate-copy-bundle.json').write_text(dumps(bundle),encoding='utf-8')
    spec=importlib.util.spec_from_file_location('snapshot_policy',QA/'daily_snapshot_policy_qa.py')
    policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
    proof=policy.verify_daily_snapshot((QA/'brapi-public-response.json').read_bytes(),
        'ca776418bafd6f621b6e49d78b7a04fc013c822cb3f2e8336fc8c5c6f3f5921e',
        (QA/'brapi-historical-dictionary-approved.json').read_bytes(),
        '2026-11-01T02:17:43.178290Z',iso(),json.loads((QA/'calendar-package-v1.json').read_text(encoding='utf-8')))
    (QA/'daily-snapshot-proof.json').write_text(json.dumps(proof,indent=2,ensure_ascii=False),encoding='utf-8')
    result={'checked_at':iso(),'live_database_accessed':False,'production_ingestion':False,'ai_calls':0,
        'sqlite_version':sqlite3.sqlite_version,'wal_supported':wal_supported(),'source_backup_sha256':source_hash,
        'source_backup_unchanged':True,'import':imported,'tables_before':before,'tables_after_import':after_import,
        'tables_after_bundle':after,'historical_bundles_revalidated':len(validated),'all_historical_bundles_valid':all(validated.values()),
        'historical_analyses_preserved':len(analyses),'analysis_bundles_valid':all(validated[row['bundle_id']] for row in analyses),
        'bundle':{'id':bundle['bundle_id'],'sha256':digest(bundle),'characters':len(dumps(bundle)),
                  'utf8_bytes':len(dumps(bundle).encode()),'evidence_count':len(bundle['evidence']),
                  'price_count':sum(e['record_type']=='price' for e in bundle['evidence']),
                  'calendar_ref_count':sum('calendar_ref' in json.loads(e['content']) for e in bundle['evidence'] if e['record_type']=='price'),
                  'known_gaps':bundle['coverage']['known_gaps'],'metrics':bundle['computed_metrics']}}
    (QA/'candidate-copy-result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('checked_at','ai_calls','historical_bundles_revalidated','historical_analyses_preserved','analysis_bundles_valid','bundle')},ensure_ascii=False))


if __name__=='__main__': main()
