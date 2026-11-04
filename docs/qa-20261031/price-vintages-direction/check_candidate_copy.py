"""Offline closed-backup replay; no live database, network or model."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import backend
import sqlite3
import asyncio
import hashlib
import importlib.util
import json
import shutil
import socket
import subprocess
from backend.core import Store,digest,dumps,wal_supported
from backend.market_calendar import import_calendar_package
from backend.price_vintages import persist_vintage
from backend.evidence import build_bundle,validate_bundle,validate_analysis
from backend.timeutil import iso

QA=Path(__file__).resolve().parent
SOURCE=ROOT/'docs/qa-20261031/manual-ui-operable/fundamentals-after.sqlite3'
COPY=QA/'candidate-copy-reviewed.sqlite3'

def blocked(*args,**kwargs):raise RuntimeError('Offline QA prohibits process/network')

def tables(con):
    def cell(value):return {'blob_sha256':hashlib.sha256(value).hexdigest(),'bytes':len(value)} if isinstance(value,bytes) else value
    names=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name:{'count':len(rows),'hash':digest(sorted(dumps([cell(value) for value in row]) for row in rows))}
            for name in names for rows in [con.execute('SELECT * FROM "'+name+'"').fetchall()]}

def main():
    socket.getaddrinfo=socket.socket.connect=socket.socket.connect_ex=subprocess.Popen=blocked
    assert wal_supported() and not COPY.exists()
    original_hash=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    shutil.copy2(SOURCE,COPY);store=Store(COPY,'real')
    with store.connect() as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        before=tables(con)
        previous_audit=[tuple(r) for r in con.execute('SELECT * FROM audit_events ORDER BY id')]
        historical=[json.loads(r[0]) for r in con.execute('SELECT payload_json FROM evidence_bundles')]
        analyses=[dict(r) for r in con.execute('SELECT payload_json,bundle_id FROM analyses')]
        by_id={bundle['bundle_id']:bundle for bundle in historical}
        assert len(historical)==18 and len(analyses)==9
        for bundle in historical:validate_bundle(bundle,ROOT/'schemas')
        spec=importlib.util.spec_from_file_location('backend.numeric_before',QA/'source-before/backend/numeric_support.py')
        old_numeric=importlib.util.module_from_spec(spec);spec.loader.exec_module(old_numeric)
        spec=importlib.util.spec_from_file_location('backend.evidence_before',QA/'source-before/backend/evidence.py')
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        old.numbers_supported=old_numeric.numbers_supported;old.numeric_rejection_reason=old_numeric.numeric_rejection_reason
        revalidation=[]
        for row in analyses:
            result={}
            for name,validator in [('baseline',old.validate_analysis),('current',validate_analysis)]:
                try:validator(json.loads(row['payload_json']),by_id[row['bundle_id']],ROOT/'schemas');result[name]='valid'
                except ValueError as error:result[name]=str(error)
            assert result['baseline']==result['current']
            revalidation.append(result)
        prior=ROOT/'docs/qa-20261031/vale3-gates'
        calendar=import_calendar_package(con,json.loads((prior/'calendar-package-v1.json').read_text(encoding='utf-8')),ROOT/'docs/qa-20261031/data-readiness/calendar/sources',expected_profile='real')
    receipt=persist_vintage(store,'brapi:VALE3',(prior/'brapi-public-response.json').read_bytes(),fetched_at='2026-11-01T02:17:43.178290Z')
    bundle=build_bundle(store,'brapi:VALE3',iso(),ROOT/'schemas')
    assert bundle['schema_version']=='1.2' and bundle['technical_policy']['eligible_action']=='wait'
    with store.connect() as con:
        after=tables(con)
        assert all(before[name]==after[name] for name in before if name not in {'audit_events','market_sessions','evidence_bundles'})
        audit=[tuple(r) for r in con.execute('SELECT * FROM audit_events ORDER BY id')]
        assert audit[:-2]==previous_audit
        assert after['audit_events']['count']==before['audit_events']['count']+2
        assert set(after)-set(before)=={'calendar_packages','price_vintages'}
        for old in historical:
            assert json.loads(con.execute('SELECT payload_json FROM evidence_bundles WHERE bundle_id=?',(old['bundle_id'],)).fetchone()[0])==old
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==original_hash
    (QA/'candidate-copy-bundle.json').write_text(dumps(bundle),encoding='utf-8')
    result={'checked_at':iso(),'live_database_accessed':False,'ai_calls':0,'network_calls':0,
            'sqlite_version':sqlite3.sqlite_version,'source_backup_sha256':original_hash,'source_backup_unchanged':True,
            'historical_bundles_valid':len(historical),'historical_analyses_preserved':len(analyses),
            'historical_analyses_revalidation_identical_to_baseline':True,'historical_analyses_current_valid':sum(r['current']=='valid' for r in revalidation),
            'tables_before':before,'tables_after':after,
            'calendar_import_in_copy':calendar,'vintage_import_in_copy':receipt,
            'bundle':{'id':bundle['bundle_id'],'sha256':digest(bundle),'characters':len(dumps(bundle)),'utf8_bytes':len(dumps(bundle).encode()),
                      'evidence_count':len(bundle['evidence']),'metrics':bundle['computed_metrics'],'technical_policy':bundle['technical_policy']}}
    (QA/'candidate-copy-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('checked_at','historical_bundles_valid','historical_analyses_preserved','historical_analyses_current_valid','ai_calls','network_calls')},ensure_ascii=False))

if __name__=='__main__':main()
