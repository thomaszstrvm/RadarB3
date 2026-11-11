"""Read-only operational evidence, separate from collection and inference."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, date, time, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .operations import collection_state
from .session_continuity import calendar_status
from .timeutil import instant, iso, SAO_PAULO


def safe_code(value):
    return value if isinstance(value,str) and re.fullmatch(r'[a-zA-Z0-9_.:-]{1,100}',value) else None


class ReadSnapshot:
    def __init__(self, connection):
        self.connection = connection
    def rows(self, query, args=()):
        return [dict(row) for row in self.connection.execute(query,args)]


def database_state(path, as_of, news_ids=()):
    """Use one SQLite read transaction including WAL, never Store/migrations."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError('database_missing')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3)) as con:
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        con.execute('BEGIN')
        tables={row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required={'settings','jobs','job_runs','worker_lease','quota_ledger','provider_state',
                  'market_sessions','assets','articles','article_revisions','price_revisions','macro_observations'}
        if not required <= tables:
            raise ValueError('database_schema_incomplete')
        reader=ReadSnapshot(con)
        def count(table,where='1'):
            return con.execute('SELECT COUNT(*) FROM '+table+' WHERE '+where).fetchone()[0] if table in tables else None
        profile=con.execute("SELECT value_json FROM settings WHERE key='profile'").fetchone()
        profile=json.loads(profile[0]) if profile else None
        profile=profile if profile in {'real','demo'} else None
        pending=count('jobs',"status IN ('queued','running','retry_wait')")
        active=count('analysis_runs',"status IN ('prepared','started')")
        runs=count('job_runs',"finished_at IS NULL")
        leases=[]
        for row in reader.rows('SELECT * FROM worker_lease'):
            try: state='active' if instant(row['expires_at'])>instant(as_of) else 'stale'
            except (ValueError,TypeError,AttributeError):state='invalid_clock'
            leases.append({'state':state,'execution_mode':row.get('execution_mode'),
                           'consumer_scope':row.get('consumer_scope')})
        collection=collection_state(reader,news_ids,now=as_of)
        collection['news_fetch_clock_reason'] = None if news_ids else 'news_source_ids_unavailable'
        collection['data_age_seconds']={key:(instant(as_of)-instant(value)).total_seconds() if value else None
            for key,value in collection.items() if key.endswith('_data_at')}
        source_clocks=[{'provider_id':safe_code(row['provider_id']),
                        'last_attempt_at':row['last_attempt_at'],'last_success_at':row['last_success_at'],
                        'error_code':safe_code(row['error_code'])} for row in collection.pop('sources')]
        period=instant(as_of).astimezone(SAO_PAULO).date().isoformat()
        quota=[dict(row) for row in con.execute("SELECT status,COUNT(*) reservations,SUM(units) units FROM quota_ledger WHERE provider='codex_cli' AND period=? GROUP BY status",(period,))]
        # Counts describe storage, not price freshness or analytical quality.
        return {'status':'observed','profile':profile,'pending_jobs':pending,'active_analysis_runs':active,
                'unfinished_job_runs':runs,'worker_leases':leases,
                'database_idle':pending==0 and active==0 and runs==0 if active is not None else None,
                'counts':{'eligible_assets':count('assets','eligible=1'),'articles':count('articles'),
                          'analyses':count('analyses'),'price_vintage_assets':con.execute('SELECT COUNT(DISTINCT asset_id) FROM price_vintages').fetchone()[0] if 'price_vintages' in tables else None},
                'collection':collection,'provider_clocks':source_clocks,'calendar':calendar_status(reader,as_of),
                'quota':{'period':period,'by_status':quota},
                'stop_authorized':False,'process_identity_verified':False}


def daily_receipts(directory, as_of, *, daily_at='20:45', grace_minutes=30):
    """A task being Ready is not evidence that a collection actually finished."""
    directory=Path(directory)
    local=instant(as_of).astimezone(SAO_PAULO)
    hour,minute=map(int,daily_at.split(':'))
    due=datetime.combine(local.date(),time(hour,minute),SAO_PAULO)+timedelta(minutes=grace_minutes)
    expected=(local.date() if local>=due else local.date()-timedelta(days=1)).isoformat()
    receipts=[];invalid=0
    if directory.exists() and not directory.is_dir():
        raise ValueError('receipts_directory_invalid')
    for path in sorted(directory.glob('*.json')) if directory.is_dir() else []:
        try:
            if path.stat().st_size>1_000_000:raise ValueError('receipt_too_large')
            item=json.loads(path.read_text(encoding='utf-8-sig'))
            start,end=instant(item['started_at']),instant(item['finished_at'])
            if not start<=end<=instant(as_of) or item['local_date']!=start.astimezone(SAO_PAULO).date().isoformat():
                raise ValueError('receipt_clock_invalid')
            if item['execution_mode']!='collection-only' or item['status'] not in {'succeeded','partial','incomplete'}:
                raise ValueError('receipt_shape_invalid')
            receipts.append({'local_date':item['local_date'],'started_at':iso(start),'finished_at':iso(end),
                             'status':item['status'],'error_code':safe_code(item.get('error_code')),
                             'jobs':[{'kind':safe_code(job.get('kind')),'status':safe_code(job.get('status')),
                                      'gaps_count':job.get('gaps_count') if type(job.get('gaps_count')) is int else None}
                                     for job in item.get('jobs',[])],
                             'receipt_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        except (ValueError,KeyError,TypeError,AttributeError,OverflowError):invalid+=1
    latest=max(receipts,key=lambda item:item['finished_at'],default=None)
    expected_receipts=[item for item in receipts if item['local_date']==expected]
    expected_latest=max(expected_receipts,key=lambda item:item['finished_at'],default=None)
    current=latest if latest and latest['local_date']>=expected else None
    state='missing_expected_receipt' if current is None else current['status']
    start=max(date.fromisoformat(min((x['local_date'] for x in receipts),default=expected)),
              date.fromisoformat(expected)-timedelta(days=30))
    observed_dates={x['local_date'] for x in receipts}
    missing=[]
    while start.isoformat()<=expected:
        if start.isoformat() not in observed_dates:missing.append(start.isoformat())
        start+=timedelta(days=1)
    return {'status':state,'expected_local_date':expected,'daily_at_assumed':daily_at,'grace_minutes':grace_minutes,
            'latest':latest,'expected_latest':expected_latest,'valid_receipts':len(receipts),'invalid_receipts':invalid,
            'recorded_dates':sorted(observed_dates),'missing_dates_in_observed_window':missing,
            'historical_failed_dates':sorted({item['local_date'] for item in receipts if item['status']=='incomplete'}),
            'scheduled_execution_proven':False,
            'limitation':'Receipts prove recorded runner results, not that Windows triggered them or complete source coverage.'}


def api_state(base,get):
    result={}
    for endpoint in ('/health/live','/operations'):
        try:
            response=get(base,endpoint)
            if not isinstance(response,dict):raise ValueError('api_shape_invalid')
            if endpoint=='/health/live':
                if response.get('status')!='alive':raise ValueError('api_health_invalid')
                result['health']={'status':'alive'}
            else:
                mode=response.get('execution_mode')
                worker=response.get('worker');ai=response.get('ai')
                if (mode not in {'collection-only','manual-analysis','analysis-enabled'}
                        or not isinstance(worker,dict) or worker.get('status') not in {'absent','active','stale'}
                        or not isinstance(ai,dict) or type(ai.get('enabled')) is not bool
                        or type(response.get('mode_mismatch')) is not bool):
                    raise ValueError('api_operations_invalid')
                result['operation']={'execution_mode':mode if mode in {'collection-only','manual-analysis','analysis-enabled'} else None,
                    'mode_mismatch':response.get('mode_mismatch'),
                    'worker_status':safe_code(response.get('worker',{}).get('status')),
                    'inference_enabled':response.get('ai',{}).get('enabled'),
                    'ai_error_code':safe_code(response.get('ai',{}).get('error_code'))}
        except OSError as exc:
            result[endpoint]={'status':'service_unavailable','error_type':type(exc).__name__}
        except (ValueError,TypeError,AttributeError) as exc:
            result[endpoint]={'status':'observation_failed','error_type':type(exc).__name__}
    result['status']='observed' if 'health' in result and 'operation' in result else (
        'observation_failed' if any(v.get('status')=='observation_failed' for v in result.values()) else 'service_unavailable')
    if 'operation' in result:
        operation=result['operation']
        operation['inference_deliberately_disabled']=operation['execution_mode']=='collection-only' and operation['inference_enabled'] is False
    return result


def backup_state(report_path,backup_path,as_of):
    """A recent verification date does not imply a recent source-data snapshot."""
    if report_path is None and backup_path is None:
        return {'status':'not_provided','verified':False,'current_database_equivalence':'not_tested'}
    if report_path is None or backup_path is None:
        raise ValueError('backup_file_and_report_required_together')
    report_path,backup_path=Path(report_path),Path(backup_path)
    if report_path.stat().st_size>10_000_000:
        raise ValueError('backup_report_too_large')
    receipt=json.loads(report_path.read_text(encoding='utf-8-sig'))
    if not isinstance(receipt,dict):raise ValueError('backup_report_object_required')
    if (receipt.get('format_version')!=1 or receipt.get('verified') is not True
            or receipt.get('status')!='ok' or receipt.get('operation') not in {'backup','restore'}):
        raise ValueError('backup_report_not_verified')
    when=instant(receipt['verified_at'])
    if when>instant(as_of):raise ValueError('backup_verification_in_future')
    with backup_path.open('rb') as stream:
        actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=receipt['destination_sha256']:
        return {'status':'file_hash_mismatch','verified':False,'current_database_equivalence':'not_tested'}
    from .backup_integrity import inspect_database
    actual_snapshot=inspect_database(backup_path)
    matches=(actual_snapshot==receipt['snapshot'] and actual_snapshot['integrity_ok']
             and actual_snapshot['foreign_key_violations']==0)
    return {'status':'verified_receipt_matches_file' if matches else 'snapshot_mismatch','verified':matches,
            'operation':receipt['operation'],'verified_at':iso(when),
            'verification_age_seconds':(instant(as_of)-when).total_seconds(),
            'verification_time_is_not_data_freshness':True,'current_database_equivalence':'not_tested',
            'tables':len(actual_snapshot['tables']),'sha256':actual}


def diagnose(database,receipts_dir,root,*,as_of=None,base='http://127.0.0.1:8765/api/v1',get,cli_status,schedule_probe=None,backup_report=None,backup_file=None):
    now=iso(as_of)
    report={'schema_version':1,'observed_at':now,'read_only':True,'operation_performed':None,
            'observation_errors':[]}
    news_ids=[]
    try:
        definitions=json.loads((Path(root)/'config/news-sources.json').read_text(encoding='utf-8-sig'))
        if not isinstance(definitions,dict) or not isinstance(definitions.get('sources'),list):
            raise ValueError('news_sources_invalid')
        news_ids=[row['id'] for row in definitions['sources']]
        if not news_ids or any(not isinstance(value,str) or not value for value in news_ids):
            raise ValueError('news_sources_invalid')
    except (OSError,ValueError,TypeError,KeyError,AttributeError) as exc:
        news_ids=[]
        report['observation_errors'].append({'component':'source_configuration','error_type':type(exc).__name__})
    for name,read in [('database',lambda:database_state(database,now,news_ids)),
                      ('daily_collection',lambda:daily_receipts(receipts_dir,now)),
                      ('api',lambda:api_state(base,get)),
                      ('backup',lambda:backup_state(backup_report,backup_file,now)),
                      ('cli_attestation',lambda:cli_status(Path(root)/'config/ai-runtime.json',Path(root)/'docs/cli-validation.json')),
                      ('windows_task',schedule_probe or (lambda:{'status':'not_observed'}))]:
        try:
            report[name]=read()
            if not isinstance(report[name],dict):raise ValueError('diagnostic_object_required')
        except (OSError,ValueError,sqlite3.Error,TypeError,KeyError,AttributeError,OverflowError) as exc:
            report[name]={'status':'observation_failed','error_type':type(exc).__name__}
            report['observation_errors'].append({'component':name,'error_type':type(exc).__name__})
    report['cli_attestation']['login_checked_now']=False
    if report['backup'].get('status') in {'file_hash_mismatch','snapshot_mismatch'}:
        report['observation_errors'].append({'component':'backup','error_type':'BackupVerificationFailed'})
    if report['windows_task'].get('status')=='observation_failed':
        report['observation_errors'].append({'component':'windows_task','error_type':report['windows_task'].get('error_type','TaskQueryFailed')})
    if report['daily_collection'].get('invalid_receipts'):
        report['observation_errors'].append({'component':'daily_collection','error_type':'InvalidReceipt'})
    latest=report['daily_collection'].get('latest')
    if latest and report['windows_task'].get('status')=='observed':
        try:
            task_time=instant(report['windows_task']['last_run_time'])
            correlated=task_time<=instant(now) and abs((instant(latest['started_at'])-task_time).total_seconds())<=120
            report['daily_collection']['latest_matches_task_run']=correlated
        except (ValueError,KeyError,TypeError,AttributeError):
            report['daily_collection']['latest_matches_task_run']=None
            report['observation_errors'].append({'component':'windows_task','error_type':'InvalidTaskClock'})
    if report['api'].get('status')=='observation_failed':
        report['observation_errors'].append({'component':'api','error_type':'InvalidResponse'})
    report['status']='observation_failed' if report['observation_errors'] else 'observed_with_limitations'
    report['limitations']=['No process was started, stopped or authorized to stop.',
        'Source availability and predictive quality are not certified by storage counts.',
        'CLI attestation checks files/hash only; login, quota availability and inference were not probed.']
    return report


def diagnostic_exit_code(report):
    if report['observation_errors']:
        return 2
    operation=report['api'].get('operation',{})
    task=report['windows_task']
    if (operation.get('mode_mismatch') is True or operation.get('worker_status')=='stale'
            or task.get('state')!='Ready' or task.get('last_result')!=0 or task.get('missed_runs')!=0
            or report['database'].get('database_idle') is not True):
        return 1
    if (report['api'].get('status')!='observed' or report['daily_collection'].get('status')!='succeeded'
            or report['backup'].get('verified') is not True
            or report['windows_task'].get('status')!='observed'
            or report['cli_attestation'].get('enabled') is not True):
        return 1
    return 0
