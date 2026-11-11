"""No live services: read-only operational observation regressions."""
import json
import socket
import subprocess
from pathlib import Path
import pytest

from backend.core import Store
from backend.operational_diagnostics import database_state,daily_receipts,api_state,diagnose
from backend.timeutil import iso

ROOT=Path(__file__).resolve().parents[1]
NOW='2026-11-11T00:00:00Z' # 10/11 21:00 SP; last due date is 09/11 until 21:15.

@pytest.fixture(autouse=True)
def no_external_actions(monkeypatch):
    def denied(*args,**kwargs):raise AssertionError('No real network/process in diagnostic tests')
    monkeypatch.setattr(socket,'socket',denied)
    monkeypatch.setattr(subprocess,'Popen',denied)

@pytest.fixture
def database(tmp_path):
    store=Store(tmp_path/'read.sqlite3','demo')
    with store.transaction() as con:con.execute('CREATE TABLE analysis_runs(status TEXT)')
    return store

def receipt(path,day='2026-11-10',status='partial',**extra):
    data={'local_date':day,'started_at':day+'T23:45:00Z','finished_at':day+'T23:46:00Z',
          'execution_mode':'collection-only','status':status,'jobs':[]}|extra
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data),encoding='utf-8')

def disabled(*args):return {'enabled':False,'error_code':'cli_verified_binary_unavailable'}

def offline(*args):raise ConnectionRefusedError('do not expose private text')


def test_database_read_uses_no_store_and_preserves_rows(database,monkeypatch):
    before=database.rows('SELECT * FROM settings')
    monkeypatch.setattr(Store,'__init__',lambda *a,**k:pytest.fail('Store initialization forbidden'))
    state=database_state(database.path,NOW)
    assert state['profile']=='demo' and state['database_idle'] is True
    assert state['pending_jobs']==state['active_analysis_runs']==state['unfinished_job_runs']==0
    assert not state['stop_authorized'] and not state['process_identity_verified']
    assert database.rows('SELECT * FROM settings')==before


def test_missing_database_is_not_created(tmp_path):
    path=tmp_path/'absent.sqlite3'
    with pytest.raises(FileNotFoundError):database_state(path,NOW)
    assert not path.exists()


def test_missing_runs_table_is_unknown_not_idle(database):
    with database.transaction() as con:con.execute('DROP TABLE analysis_runs')
    state=database_state(database.path,NOW)
    assert state['active_analysis_runs'] is None and state['database_idle'] is None


@pytest.mark.parametrize('active',['prepared','started'])
def test_active_analysis_blocks_idle_claim(database,active):
    with database.transaction() as con:con.execute('INSERT INTO analysis_runs VALUES(?)',(active,))
    assert database_state(database.path,NOW)['database_idle'] is False


@pytest.mark.parametrize('status',['queued','running','retry_wait'])
def test_pending_work_blocks_idle_claim(database,status):
    job=database.enqueue('demo_task',{},'one')
    with database.transaction() as con:con.execute('UPDATE jobs SET status=? WHERE job_id=?',(status,job['job_id']))
    assert database_state(database.path,NOW)['database_idle'] is False


def test_partial_receipt_not_hidden_by_ready_task(tmp_path):
    receipt(tmp_path/'today.json')
    result=daily_receipts(tmp_path,NOW)
    assert result['status']=='partial'
    assert result['latest']['status']=='partial' and not result['scheduled_execution_proven']


def test_receipt_deadline_boundary_and_missing_day(tmp_path):
    receipt(tmp_path/'prior.json',day='2026-11-09',status='succeeded')
    assert daily_receipts(tmp_path,'2026-11-11T00:14:59Z')['status']=='succeeded'
    result=daily_receipts(tmp_path,'2026-11-11T00:15:00Z')
    assert result['status']=='missing_expected_receipt'
    assert result['expected_local_date']=='2026-11-10'
    assert result['missing_dates_in_observed_window']==['2026-11-10']


def test_failed_receipts_are_retained_as_history(tmp_path):
    receipt(tmp_path/'failed.json',day='2026-11-09',status='incomplete',error_code='another_worker_is_active')
    receipt(tmp_path/'today.json')
    result=daily_receipts(tmp_path,NOW)
    assert result['status']=='partial'
    assert result['historical_failed_dates']==['2026-11-09']
    assert result['valid_receipts']==2


@pytest.mark.parametrize('extra',[{'finished_at':'2026-11-12T00:00:00Z'},{'started_at':'invalid'},
                                 {'execution_mode':'analysis-enabled'},{'local_date':'2026-11-09'}])
def test_invalid_receipts_do_not_certify_success(tmp_path,extra):
    receipt(tmp_path/'bad.json',status='succeeded',**extra)
    result=daily_receipts(tmp_path,NOW)
    assert result['status']=='missing_expected_receipt' and result['invalid_receipts']==1


def test_corrupt_receipt_reports_observer_error(database,tmp_path):
    folder=tmp_path/'receipts';folder.mkdir();(folder/'broken.json').write_text('{')
    report=diagnose(database.path,folder,ROOT,as_of=NOW,get=offline,cli_status=disabled)
    assert {'component':'daily_collection','error_type':'InvalidReceipt'} in report['observation_errors']


def test_service_offline_is_not_database_failure(database,tmp_path):
    report=diagnose(database.path,tmp_path/'none',ROOT,as_of=NOW,get=offline,cli_status=disabled)
    assert report['database']['status']=='observed'
    assert report['api']['status']=='service_unavailable'
    assert not report['observation_errors']
    assert 'private text' not in json.dumps(report)
    assert report['cli_attestation']['login_checked_now'] is False


def test_invalid_api_is_observation_failure(database,tmp_path):
    report=diagnose(database.path,tmp_path/'none',ROOT,as_of=NOW,get=lambda *a:[],cli_status=disabled)
    assert report['api']['status']=='observation_failed' and report['observation_errors']


def test_collection_only_is_deliberately_disabled_not_ai_failure():
    def get(base,path):return {'status':'alive'} if path=='/health/live' else {'execution_mode':'collection-only','mode_mismatch':False,'worker':{'status':'absent'},'ai':{'enabled':False}}
    result=api_state('unused',get)
    assert result['status']=='observed'
    assert result['operation']['inference_deliberately_disabled'] is True


def test_schedule_query_failure_is_not_ready(database,tmp_path):
    report=diagnose(database.path,tmp_path/'none',ROOT,as_of=NOW,get=offline,cli_status=disabled,
                    schedule_probe=lambda:{'status':'observation_failed','error_type':'TaskQueryFailed'})
    assert report['status']=='observation_failed'
    assert {'component':'windows_task','error_type':'TaskQueryFailed'} in report['observation_errors']


def test_scheduler_and_receipt_correlation_is_explicit(database,tmp_path):
    folder=tmp_path/'receipts';receipt(folder/'today.json')
    report=diagnose(database.path,folder,ROOT,as_of=NOW,get=offline,cli_status=disabled,
        schedule_probe=lambda:{'status':'observed','state':'Ready','last_run_time':'2026-11-10T23:45:01Z'})
    assert report['daily_collection']['latest_matches_task_run'] is True
    assert report['daily_collection']['status']=='partial'
    assert report['operation_performed'] is None


@pytest.fixture
def verified_backup(database,tmp_path):
    from scripts.backup import copy_verified
    target=tmp_path/'backup.sqlite3'
    receipt=copy_verified('backup',database.path,target)
    receipt['verified_at']='2026-11-10T23:00:00Z'
    report=tmp_path/'backup.json';report.write_text(json.dumps(receipt))
    return report,target,receipt


def test_backup_receipt_is_rechecked_not_trusted(verified_backup):
    from backend.operational_diagnostics import backup_state
    report,path,receipt=verified_backup
    result=backup_state(report,path,NOW)
    assert result['verified'] and result['status']=='verified_receipt_matches_file'
    assert result['verification_time_is_not_data_freshness'] is True
    assert result['current_database_equivalence']=='not_tested'
    receipt['snapshot']['tables']['jobs']['rows']+=1
    report.write_text(json.dumps(receipt))
    assert backup_state(report,path,NOW)['status']=='snapshot_mismatch'


def test_backup_changed_file_is_not_verified(verified_backup):
    from backend.operational_diagnostics import backup_state
    report,path,_=verified_backup
    path.write_bytes(path.read_bytes()+b'changed')
    assert backup_state(report,path,NOW)['status']=='file_hash_mismatch'


def test_future_backup_verification_is_rejected(verified_backup):
    from backend.operational_diagnostics import backup_state
    report,path,receipt=verified_backup;receipt['verified_at']='2026-11-12T00:00:00Z'
    report.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='future'):backup_state(report,path,NOW)


def test_no_backup_is_unknown_not_healthy():
    from backend.operational_diagnostics import backup_state
    assert backup_state(None,None,NOW)['status']=='not_provided'
    with pytest.raises(ValueError):backup_state('one.json',None,NOW)


@pytest.mark.parametrize('operations',[{}, {'execution_mode':'invalid'},
    {'execution_mode':'collection-only','mode_mismatch':False,'worker':[],'ai':{'enabled':False}},
    {'execution_mode':'collection-only','mode_mismatch':False,'worker':{'status':'absent'},'ai':{'enabled':'false'}}])
def test_invalid_dict_operations_never_count_as_observed(operations):
    def get(base,path):return {'status':'alive'} if path=='/health/live' else operations
    assert api_state('unused',get)['status']=='observation_failed'


def test_empty_health_never_counts_as_observed():
    assert api_state('unused',lambda *a:{})['status']=='observation_failed'


def test_supplied_backup_failure_has_nonzero_exit(database,tmp_path,verified_backup):
    from backend.operational_diagnostics import diagnostic_exit_code
    report,path,_=verified_backup
    path.write_bytes(path.read_bytes()+b'changed')
    result=diagnose(database.path,tmp_path/'none',ROOT,as_of=NOW,get=offline,cli_status=disabled,
                    backup_report=report,backup_file=path)
    assert {'component':'backup','error_type':'BackupVerificationFailed'} in result['observation_errors']
    assert diagnostic_exit_code(result)==2


def test_absent_backup_or_schedule_never_gets_healthy_exit(database,tmp_path):
    from backend.operational_diagnostics import diagnostic_exit_code
    result=diagnose(database.path,tmp_path/'none',ROOT,as_of=NOW,get=offline,cli_status=disabled)
    assert diagnostic_exit_code(result)==1


@pytest.mark.parametrize('value',[[],None,'not an object'])
def test_backup_json_wrong_type_is_reported_not_traceback(database,tmp_path,verified_backup,value):
    report,path,_=verified_backup;report.write_text(json.dumps(value))
    result=diagnose(database.path,tmp_path/'none',ROOT,as_of=NOW,get=offline,cli_status=disabled,
                    backup_report=report,backup_file=path)
    assert result['backup']['status']=='observation_failed'
    assert any(x['component']=='backup' for x in result['observation_errors'])


def test_cli_configuration_wrong_type_is_reported_not_traceback(database,tmp_path):
    def malformed(*args):raise AttributeError('do not expose config content')
    result=diagnose(database.path,tmp_path/'none',ROOT,as_of=NOW,get=offline,cli_status=malformed)
    assert result['cli_attestation']['status']=='observation_failed'
    assert 'config content' not in json.dumps(result)


def test_news_success_clock_uses_configured_ids(database):
    with database.transaction() as con:
        con.execute('INSERT INTO provider_state VALUES(?,?,?,?,?,?,?)',
          ('br_g1','partial','2026-11-10T23:45:00Z','2026-11-10T23:45:00Z',None,None,'{}'))
    state=database_state(database.path,NOW,news_ids=['br_g1'])
    assert state['collection']['latest_news_fetch']==iso('2026-11-10T23:45:00Z')
    assert state['collection']['news_fetch_clock_reason'] is None


def healthy_exit_fixture():
    return {'observation_errors':[],'api':{'status':'observed','operation':{'mode_mismatch':False,'worker_status':'absent'}},
            'daily_collection':{'status':'succeeded'},'backup':{'verified':True},
            'database':{'database_idle':True},'cli_attestation':{'enabled':True},
            'windows_task':{'status':'observed','state':'Ready','last_result':0,'missed_runs':0}}


def test_exit_zero_requires_all_covered_observations_consistent():
    from backend.operational_diagnostics import diagnostic_exit_code
    assert diagnostic_exit_code(healthy_exit_fixture())==0


@pytest.mark.parametrize('component,key,value',[
    ('operation','mode_mismatch',True),('operation','worker_status','stale'),
    ('windows_task','state','Disabled'),('windows_task','last_result',2),
    ('windows_task','missed_runs',1),('database','database_idle',False)])
def test_observed_operational_problem_cannot_exit_zero(component,key,value):
    from backend.operational_diagnostics import diagnostic_exit_code
    report=healthy_exit_fixture()
    target=report['api']['operation'] if component=='operation' else report[component]
    target[key]=value
    assert diagnostic_exit_code(report)==1


def test_cli_json_survives_windows_legacy_codepages(monkeypatch,capsys):
    from scripts import diagnose as command
    report={'message':'Sess\u00e3o, pre\u00e7os e valida\u00e7\u00e3o'}
    monkeypatch.setattr(command,'diagnose',lambda *args,**kwargs:report)
    monkeypatch.setattr(command,'diagnostic_exit_code',lambda value:1)
    assert command.main(['--db','unused.sqlite3','--receipts-dir','unused','--skip-windows-task'])==1
    text=capsys.readouterr().out
    assert text.isascii()
    assert json.loads(text.encode('cp1252').decode('cp850'))==report
