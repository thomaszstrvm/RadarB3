"""Staged read-only verification and one approved SQLite backup; never imports.

Not executed by preparation. All writes are new QA evidence files or backup.
"""
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
from backend.core import wal_supported
from backend.timeutil import iso,instant,SAO_PAULO
from backend.market_calendar import package_rows,validate_package_envelope
from check_candidate_copy import tables

QA=Path(__file__).resolve().parent
LIVE=ROOT/'data/real/radar.sqlite3'
BACKUP=QA/'operational-before.sqlite3'


def read(path):
    con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    con.row_factory=sqlite3.Row
    return con


def check_hashes(plan):
    results={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value
             for name,value in plan['reviewed_hashes'].items()}
    if not all(results.values()): raise RuntimeError('reviewed_file_changed')
    return results


def verify_audit_append(current, baseline):
    """An append must preserve every prior ID, type, clock and payload byte."""
    previous = [tuple(row) for row in baseline.execute('SELECT * FROM audit_events ORDER BY id')]
    actual = [tuple(row) for row in current.execute('SELECT * FROM audit_events ORDER BY id')]
    if len(actual) != len(previous)+1 or actual[:-1] != previous:
        raise RuntimeError('historical_audit_changed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('before','after-import','final'))
    args=parser.parse_args()
    output=QA/f'operational-{args.stage}.json'
    if output.exists(): raise RuntimeError('existing_evidence_preserved')
    assert wal_supported(sqlite3.sqlite_version_info)
    plan=json.loads((QA/'operation-plan.json').read_text(encoding='utf-8'))
    hashes=check_hashes(plan)
    with read(LIVE) as con:
        con.execute('BEGIN')
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert json.loads(con.execute("SELECT value_json FROM settings WHERE key='profile'").fetchone()[0])=='real'
        assert con.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','retry_wait')").fetchone()[0]==0
        leases=[dict(r) for r in con.execute('SELECT * FROM worker_lease')]
        current=tables(con)
        now=iso();period=instant(now).astimezone(SAO_PAULO).date().isoformat()
        ledger=[dict(r) for r in con.execute("SELECT period,status,count(*) reservations,sum(units) units FROM quota_ledger WHERE provider='codex_cli' GROUP BY period,status ORDER BY period,status")]
        used=con.execute("SELECT coalesce(sum(units),0) FROM quota_ledger WHERE provider='codex_cli' AND period=? AND status<>'released'",(period,)).fetchone()[0]
        limit=json.loads(con.execute("SELECT value_json FROM settings WHERE key='max_analyses_per_day'").fetchone()[0])
        assert type(limit) is int and 0<=limit<=10
        if args.stage=='before':
            # Run after the confirmed launcher tree has gracefully stopped.
            assert not leases and not BACKUP.exists()
            with read(ROOT/'docs/qa-20261031/manual-ui-operable/fundamentals-after.sqlite3') as baseline:
                original=tables(baseline)
            assert current==original, 'unexpected_operation_since_reviewed_baseline'
            assert len(current)==34 and current['analyses']['count']==9 and current['market_sessions']['count']==0
            with sqlite3.connect(BACKUP) as target:
                con.backup(target)
            with read(BACKUP) as saved:
                assert saved.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and tables(saved)==current
        else:
            with read(BACKUP) as baseline:
                original=tables(baseline)
                verify_audit_append(con,baseline)
            assert set(current)-set(original)=={'calendar_packages'}
            assert len(current)==35 and current['calendar_packages']['count']==1
            assert current['market_sessions']['count']==original['market_sessions']['count']+96
            assert current['audit_events']['count']==original['audit_events']['count']+1
            assert all(current[name]==original[name] for name in original if name not in ('market_sessions','audit_events','worker_lease'))
            package=dict(con.execute('SELECT * FROM calendar_packages').fetchone())
            document=json.loads(package['payload_json'])
            envelope={key:package[key] for key in ('package_id','document_sha256','available_at')}
            envelope['document']=document
            expected=validate_package_envelope(envelope,now)
            assert [dict(row) for row in con.execute('SELECT * FROM market_sessions ORDER BY session_date')]==expected
            baseline_record=json.loads((QA/'operational-before.json').read_text(encoding='utf-8'))
            assert instant(package['available_at'])>=instant(baseline_record['checked_at'])
            audit=con.execute("SELECT payload_json,created_at FROM audit_events WHERE event_type='calendar_package_imported'").fetchall()
            assert len(audit)==1 and audit[0]['created_at']==package['available_at']
            if args.stage=='after-import': assert not leases
            else: assert len(leases)==1 and leases[0]['execution_mode']=='manual-analysis'
        result={'checked_at':now,'stage':args.stage,'sqlite_version':sqlite3.sqlite_version,
                'active_jobs':0,'lease':leases,'tables':current,'hashes_equal':hashes,
                'quota':{'period':period,'used':used,'limit':limit,'remaining':max(0,limit-used)},
                'ledger_by_period':ledger,'ai_calls_by_this_operation':0}
    if args.stage=='final':
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def get(path):
            with opener.open('http://127.0.0.1:8765'+path,timeout=10) as response: return json.load(response)
        operations=get('/api/v1/operations')
        assert operations['execution_mode']=='manual-analysis' and operations['worker']['status']=='active'
        assert operations['manual_analysis']['state']=='ready'
        source=next(row for row in get('/api/v1/sources')['items'] if row['id']=='market_calendar')
        assert source['count']==96 and source['details']['scheduled_open_days']==65
        assert source['details']['provider_finality_certified'] is False
        result['operations']=operations;result['calendar_source']=source
        result['coverage_known_gaps']=get('/api/v1/coverage')['known_gaps']
        with opener.open('http://127.0.0.1:8765/',timeout=10) as response:
            assert hashlib.sha256(response.read()).hexdigest()==plan['reviewed_hashes']['frontend/dist/index.html']
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'stage':args.stage,'checked_at':now,'quota':result['quota'],'active_jobs':0,'tables':len(current)},ensure_ascii=False))


if __name__=='__main__': main()
