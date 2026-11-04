"""Prepared verifier: new backup, exact append/preservation, local GETs only."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import backend
import sqlite3
import argparse
import hashlib
import json
import urllib.request
from contextlib import closing
from backend.core import Store,wal_supported,digest,dumps
from backend.timeutil import iso,instant,SAO_PAULO
from backend.price_vintages import validate_vintage
from backend.evidence import build_bundle,validate_bundle
from check_candidate_copy import tables

QA=Path(__file__).resolve().parent
LIVE=ROOT/'data/real/radar.sqlite3'
BACKUP=QA/'operational-before.sqlite3'

def read(path):
    con=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);con.row_factory=sqlite3.Row;return closing(con)

def check_hashes(plan):
    result={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected for name,expected in plan['reviewed_hashes'].items()}
    if not all(result.values()):raise RuntimeError('reviewed_file_changed')
    return result

def preserve_append(con,baseline):
    old=[tuple(row) for row in baseline.execute('SELECT * FROM audit_events ORDER BY id')]
    new=[tuple(row) for row in con.execute('SELECT * FROM audit_events ORDER BY id')]
    if len(new)!=len(old)+1 or new[:-1]!=old:raise RuntimeError('historical_audit_changed')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=('before','after-import','final'));args=parser.parse_args()
    output=QA/f'operational-{args.stage}.json'
    if output.exists():raise RuntimeError('existing_evidence_preserved')
    assert wal_supported()
    plan=json.loads((QA/'operation-plan.json').read_text(encoding='utf-8'));hashes=check_hashes(plan)
    with read(LIVE) as con:
        con.execute('BEGIN');assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert json.loads(con.execute("SELECT value_json FROM settings WHERE key='profile'").fetchone()[0])=='real'
        active=con.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','retry_wait')").fetchone()[0]
        assert active==0
        current=tables(con);leases=[dict(row) for row in con.execute('SELECT * FROM worker_lease')]
        now=iso();period=instant(now).astimezone(SAO_PAULO).date().isoformat()
        used=con.execute("SELECT coalesce(sum(units),0) FROM quota_ledger WHERE provider='codex_cli' AND period=? AND status<>'released'",(period,)).fetchone()[0]
        limit=json.loads(con.execute("SELECT value_json FROM settings WHERE key='max_analyses_per_day'").fetchone()[0])
        assert type(limit) is int and 0<=limit<=10
        if args.stage=='before':
            assert not leases and not BACKUP.exists() and len(current)==35 and 'price_vintages' not in current
            baseline=json.loads((QA/'readonly-checkpoint.json').read_text(encoding='utf-8'))['tables']
            assert all(current[name]==expected for name,expected in baseline.items() if name!='worker_lease')
            with closing(sqlite3.connect(BACKUP)) as target:con.backup(target)
            with read(BACKUP) as backup:assert backup.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and tables(backup)==current
            proof=None
        else:
            with read(BACKUP) as baseline:
                original=tables(baseline);preserve_append(con,baseline)
            assert len(current)==36 and set(current)-set(original)=={'price_vintages'}
            assert all(current[name]==original[name] for name in original if name not in {'worker_lease','audit_events'})
            rows=[dict(row) for row in con.execute('SELECT * FROM price_vintages')];assert len(rows)==1
            row=rows[0];asset=dict(con.execute('SELECT * FROM assets WHERE asset_id=?',(row['asset_id'],)).fetchone())
            normalized=validate_vintage(row,asset,now)
            assert row['response_sha256']==plan['public_response_sha256'] and row['fetched_at']==plan['public_fetched_at']
            assert instant(row['available_at'])>=instant(json.loads((QA/'operational-before.json').read_text(encoding='utf-8'))['checked_at'])
            audit=con.execute("SELECT created_at,payload_json FROM audit_events WHERE event_type='price_vintage_captured'").fetchall()
            assert len(audit)==1 and audit[0]['created_at']==row['available_at']
            assert json.loads(audit[0]['payload_json'])=={key:row[key] for key in ('vintage_id','response_sha256','policy_sha256')}
            proof={key:row[key] for key in ('vintage_id','response_sha256','fetched_at','available_at','policy_sha256','material_sha256')}
            proof['observations']=len(normalized['bars'])
            if args.stage=='after-import':
                assert not leases
                copy=QA/'operational-after-copy.sqlite3';assert not copy.exists()
                with closing(sqlite3.connect(copy)) as target:con.backup(target)
            else:assert len(leases)==1 and leases[0]['execution_mode']=='manual-analysis' and instant(leases[0]['expires_at'])>instant(now)
    result={'checked_at':now,'local_time':instant(now).astimezone(SAO_PAULO).isoformat(),'stage':args.stage,
            'sqlite_version':sqlite3.sqlite_version,'active_jobs':active,'lease':leases,'tables':current,'hashes_equal':hashes,
            'quota':{'period':period,'used':used,'limit':limit,'remaining':max(0,limit-used)},'vintage':proof,'new_ai_calls':0,'new_price_requests':0}
    if args.stage=='after-import':
        candidate=build_bundle(Store(copy,'real',wal=True),row['asset_id'],iso(),ROOT/'schemas')
        assert validate_bundle(candidate,ROOT/'schemas') and candidate['schema_version']=='1.2'
        with read(copy) as checked:
            copy_tables=tables(checked)
            assert all(copy_tables[name]==expected for name,expected in current.items() if name!='evidence_bundles')
        result['isolated_candidate']={'id':candidate['bundle_id'],'characters':len(dumps(candidate)),
                                     'metrics':candidate['computed_metrics'],'technical_policy':candidate['technical_policy']}
        (QA/'operational-copy-bundle.json').write_text(dumps(candidate),encoding='utf-8')
    if args.stage=='final':
        # Only fixed loopback GET paths; no analysis/coleta endpoint.
        def get(path):
            with urllib.request.urlopen('http://127.0.0.1:8765'+path,timeout=10) as response:return response.read()
        operations=json.loads(get('/api/v1/operations'));asset_view=json.loads(get('/api/v1/assets/brapi:VALE3'))
        assert operations['execution_mode']=='manual-analysis' and operations['worker']['status']=='active'
        assert operations['manual_analysis']['state']=='ready' and operations['manual_analysis']['quota']==result['quota']
        assert plan['served_script'] in get('/').decode()
        assert asset_view['price_snapshot']['vintage_id']==proof['vintage_id'] and asset_view['price_at'] is None
        assert asset_view['price_timestamp_precision']=='date' and len(asset_view['current_price_metrics'])==8
        source=next(item for item in json.loads(get('/api/v1/sources'))['items'] if item['id']=='brapi_prices')
        assert source['count']==63 and source['status']=='partial'
        result.update(operations=operations,served_script=plan['served_script'],price_source=source,
                      price_session_date=asset_view['price_session_date'],current_price_metrics=asset_view['current_price_metrics'])
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('stage','checked_at','active_jobs','quota','vintage')},ensure_ascii=False))

if __name__=='__main__':main()
