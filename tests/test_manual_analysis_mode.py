from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import json

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.cli_process import CLIError, ProcessResult
from backend.core import Conflict, Store, dumps, wal_supported
from backend.timeutil import iso
from backend.worker import Worker, Blocked
from test_cli_runner import setup_runner


@pytest.fixture
def manual(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': True})
    app = create_app(tmp_path/'manual.db', profile='real', execution_mode='manual-analysis')
    store = app.state.store
    with store.transaction() as con:
        for asset in ('a', 'b'):
            con.execute("INSERT INTO assets(asset_id,ticker,name,asset_type,eligible,provider,updated_at) VALUES(?,?,?,'stock',1,'synthetic',?)", (asset,asset.upper()+'3','Synthetic',iso()))
    # Match the real worker: reopening the API's WAL database as DELETE can
    # require an exclusive mode switch while an API connection is still open.
    worker = Worker(Store(store.path, wal=wal_supported()), handlers={'analyse': lambda job: {'synthetic': True}}, execution_mode='manual-analysis', reconciliation_mutex_factory=nullcontext)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.headers['x-radar-token'] = client.get('/api/v1/session').json()['token']
        try:
            yield client, store, worker
        finally:
            worker.close()


def post(client, asset='a', key='request-a'):
    return client.post('/api/v1/assets/'+asset+'/analyse', headers={'idempotency-key':key}, json={})


def test_manual_api_requires_live_worker_and_gets_have_no_effect(manual):
    client, store, worker = manual
    assert client.get('/api/v1/operations').json()['manual_analysis']['state'] == 'unavailable'
    assert not client.get('/api/v1/overview').json()['ai']['enabled']
    assert post(client).json()['detail'] == 'manual_worker_unavailable'
    assert store.list_jobs() == [] and not store.rows('SELECT * FROM quota_ledger')
    worker.start()
    ready = client.get('/api/v1/operations').json()['manual_analysis']
    assert {k:ready[k] for k in ('state','pending_limit','repair_attempts')} == {'state':'ready','pending_limit':1,'repair_attempts':0}
    assert ready['quota']['used'] == 0 and ready['quota']['remaining'] == ready['quota']['limit'] == 10
    assert client.get('/api/v1/overview').json()['ai']['enabled']
    for _ in range(2):
        client.get('/api/v1/assets/a/analysis-job')
    assert store.list_jobs() == []


def test_admission_idempotence_scope_and_tracking(manual):
    client, store, worker = manual
    legacy = store.enqueue('analyse', {'asset_id':'a'}, 'legacy', priority=0)
    news = store.enqueue('news', {}, 'legacy-news', priority=0)
    worker.start()
    first = post(client)
    assert first.status_code == 202
    job = store.get_job(first.json()['id'])
    assert job['payload'] == {'asset_id':'a','manual_owner':worker.owner}
    assert job['max_attempts'] == 1
    assert post(client).json() == first.json()
    assert post(client, 'b', 'request-b').status_code == 409
    assert client.get('/api/v1/operations').json()['manual_analysis']['state'] == 'busy'
    assert not client.get('/api/v1/overview').json()['ai']['enabled']
    tracked = client.get('/api/v1/assets/a/analysis-job').json()
    assert tracked['manual_scope'] == 'current' and tracked['queue_position'] == 1
    assert worker.run_once(job_ids=[job['job_id']])
    assert client.get('/api/v1/assets/a/analysis-job').json()['job']['id'] == job['job_id']
    assert client.get('/api/v1/operations').json()['manual_analysis']['state'] == 'ready'
    assert store.get_job(legacy['job_id']) == legacy
    assert store.get_job(news['job_id']) == news
    assert not worker.run_once(job_ids=[legacy['job_id'],news['job_id']])


def test_atomic_admission_allows_only_one_concurrent_request(manual):
    client, store, worker = manual
    worker.start()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda asset: post(client,asset,'concurrent-'+asset).status_code, ['a','b']))
    assert sorted(results) == [202,409]
    assert len(store.list_jobs()) == 1


def test_no_collection_or_generic_retry_in_manual_mode(manual, monkeypatch):
    client, store, worker = manual
    job = store.enqueue('news')
    before = store.get_job(job['job_id'])
    assert client.post('/api/v1/jobs',json={'kind':'refresh'}).json()['detail'] == 'manual_operation_not_allowed'
    assert client.post('/api/v1/jobs/'+job['job_id']+'/retry').json()['detail'] == 'manual_operation_not_allowed'
    monkeypatch.setattr(worker.store, 'enqueue', lambda *a, **k: pytest.fail('Scheduler called'))
    worker.schedule()
    with pytest.raises(Blocked, match='manual_job_scope_required'):
        worker.execute(job)
    with pytest.raises(Conflict, match='manual_job_scope_required'):
        worker.run_once()
    assert store.get_job(job['job_id']) == before


def test_old_queue_is_visible_but_outside_manual_scope(manual):
    client, store, worker = manual
    old = store.enqueue('analyse', {'asset_id':'a'})
    worker.start()
    view = client.get('/api/v1/assets/a/analysis-job').json()
    assert view['job']['id'] == old['job_id'] and view['manual_scope'] == 'outside'
    assert view['queue_position'] is None and view['queue_position_reason'] == 'manual_scope_inactive'


def test_cancel_queued_manual_job_never_executes(manual):
    client, store, worker = manual
    worker.start(); job = post(client).json()
    assert client.post('/api/v1/jobs/'+job['id']+'/cancel').status_code == 200
    assert not worker.run_once(job_ids=[job['id']])
    assert not store.rows('SELECT * FROM job_runs')


def test_quota_is_local_read_only_and_replay_precedes_current_balance(manual):
    from backend.operations import manual_quota
    client, store, worker = manual
    worker.start(); accepted = post(client)
    period = manual_quota(store)[0]['period']
    reservation = store.reserve_quota('codex_cli',period,10,'synthetic-limit',units=10)
    store.settle_quota(reservation,'uncertain')
    ledger = store.rows('SELECT * FROM quota_ledger')
    assert worker.run_once(job_ids=[accepted.json()['id']])
    assert post(client).json() == accepted.json()  # Existing receipt, no new admission.
    rejected = post(client,'b','exhausted')
    assert rejected.status_code == 409 and rejected.json()['detail'] == 'manual_quota_exhausted'
    state = client.get('/api/v1/operations').json()
    assert state['manual_analysis']['quota'] == {'used':10,'limit':10,'remaining':0,'period':period}
    assert not state['ai']['enabled']
    assert store.rows('SELECT * FROM quota_ledger') == ledger
    assert len(store.list_jobs()) == 1


def test_quota_uses_sao_paulo_day_and_excludes_only_released(tmp_path):
    from backend.operations import manual_quota
    store = Store(tmp_path/'quota.db')
    for key, period, state in [('a','2026-10-30','consumed'),('b','2026-10-30','uncertain'),('c','2026-10-30','released'),('d','2026-10-31','reserved')]:
        reservation = store.reserve_quota('codex_cli',period,10,key)
        if state != 'reserved': store.settle_quota(reservation,state)
    assert manual_quota(store,now='2026-10-31T02:59:00Z')[0] == {'used':2,'limit':10,'remaining':8,'period':'2026-10-30'}
    assert manual_quota(store,now='2026-10-31T03:00:00Z')[0]['used'] == 1


def test_manual_receipt_replay_survives_worker_mode_change(manual):
    client, store, worker = manual
    worker.start(); accepted = post(client)
    worker.close()
    store.acquire_worker('collection-worker',execution_mode='collection-only')
    assert post(client).json() == accepted.json()
    fresh = post(client,'b','after-mode-change')
    assert fresh.status_code == 409 and fresh.json()['detail'] == 'manual_worker_unavailable'
    assert len(store.list_jobs()) == 1


def test_restart_reconciles_only_manual_jobs_under_mutex_and_fence(tmp_path):
    store = Store(tmp_path/'restart.db')
    old = Worker(store, execution_mode='manual-analysis', reconciliation_mutex_factory=nullcontext)
    old.start()
    job = store.enqueue('analyse', {'asset_id':'a','manual_owner':old.owner})
    store.claim(old.owner, job_ids=[job['job_id']])
    reservation = store.reserve_quota('codex_cli','2026-10-31',10,'synthetic-uncertain')
    ledger = store.rows('SELECT * FROM quota_ledger')
    legacy = store.enqueue('analyse', {'asset_id':'legacy'})
    with store.connect() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    protected = {'held':False}
    @contextmanager
    def mutex():
        protected['held'] = True
        try: yield
        finally: protected['held'] = False
    fresh_store = Store(store.path)
    fresh = Worker(fresh_store, execution_mode='manual-analysis', reconciliation_mutex_factory=mutex)
    acquire = fresh_store.acquire_worker
    def fenced(*a, **kw):
        assert protected['held'] and kw['recover_job_ids'] == [] and kw['analysis_mutex_held']
        return acquire(*a, **kw)
    fresh_store.acquire_worker = fenced
    try:
        fresh.start()
        interrupted = fresh_store.get_job(job['job_id'])
        assert interrupted['status'] == 'blocked' and interrupted['error_code'] == 'manual_session_interrupted'
        assert interrupted['result']['usage_uncertain']
        assert fresh_store.rows('SELECT * FROM quota_ledger') == ledger
        assert fresh_store.get_job(legacy['job_id']) == legacy
        assert not fresh.run_once(job_ids=[job['job_id']])
        assert fresh_store.rows('SELECT status FROM job_runs') == [{'status':'interrupted'}]
        with pytest.raises(Conflict, match='worker_lease_lost'):
            store.reserve_quota('codex_cli','2026-10-31',10,'old-worker-must-not-send')
    finally:
        old.close(); fresh.close()
    reconciled = fresh_store.get_job(job['job_id'])
    audit = fresh_store.rows('SELECT * FROM audit_events')
    restarted = Worker(Store(store.path),execution_mode='manual-analysis',reconciliation_mutex_factory=nullcontext)
    try:
        restarted.start()
        assert restarted.store.get_job(job['job_id']) == reconciled
        assert restarted.store.rows('SELECT * FROM audit_events') == audit
        assert restarted.store.rows('SELECT * FROM quota_ledger') == ledger
    finally: restarted.close()


@pytest.mark.parametrize('live_lease',[False,True])
def test_busy_cli_or_live_lease_prevents_recovery(tmp_path, live_lease):
    store = Store(tmp_path/'busy.db')
    store.acquire_worker('old', execution_mode='manual-analysis')
    old = store.enqueue('analyse', {'asset_id':'a','manual_owner':'old'})
    if not live_lease:
        with store.transaction() as con: con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    before = store.rows('SELECT * FROM worker_lease')
    def occupied(): raise CLIError('cli_analysis_already_running')
    worker = Worker(Store(store.path), execution_mode='manual-analysis', reconciliation_mutex_factory=nullcontext if live_lease else occupied)
    with pytest.raises((CLIError,Conflict)):
        worker.start()
    assert store.get_job(old['job_id']) == old and store.rows('SELECT * FROM worker_lease') == before


def test_general_worker_never_claims_or_recovers_manual_scope(tmp_path):
    store = Store(tmp_path/'normal.db')
    store.acquire_worker('old', execution_mode='manual-analysis')
    manual = store.enqueue('analyse', {'asset_id':'a','manual_owner':'old'})
    store.claim('old',job_ids=[manual['job_id']])
    manual = store.get_job(manual['job_id'])
    store.release_worker('old')
    worker = Worker(Store(store.path), handlers={'news':lambda _: {}}, execution_mode='analysis-enabled')
    news = store.enqueue('news')
    try:
        worker.start()
        assert worker.run_once() and store.get_job(news['job_id'])['status'] == 'succeeded'
        assert not worker.run_once() and store.get_job(manual['job_id']) == manual
    finally: worker.close()


@pytest.mark.parametrize('mode',['collection-only','analysis-enabled'])
def test_normal_mode_cannot_take_expired_manual_lease_while_cli_mutex_busy(tmp_path,mode):
    store = Store(tmp_path/'takeover.db')
    store.acquire_worker('old',execution_mode='manual-analysis')
    job = store.enqueue('analyse',{'asset_id':'a','manual_owner':'old'})
    store.claim('old',job_ids=[job['job_id']])
    with store.transaction() as con: con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    before_lease = store.rows('SELECT * FROM worker_lease')
    before_job = store.get_job(job['job_id'])
    with pytest.raises(Conflict,match='manual_takeover_guard_required'):
        store.acquire_worker('unguarded',execution_mode=mode)
    def occupied(): raise CLIError('cli_analysis_already_running')
    worker = Worker(Store(store.path),execution_mode=mode,reconciliation_mutex_factory=occupied)
    with pytest.raises(CLIError,match='cli_analysis_already_running'): worker.start()
    assert store.rows('SELECT * FROM worker_lease') == before_lease
    assert store.get_job(job['job_id']) == before_job
    fresh = Worker(Store(store.path),execution_mode=mode,reconciliation_mutex_factory=nullcontext)
    try:
        fresh.start()
        assert store.rows('SELECT owner FROM worker_lease') == [{'owner':fresh.owner}]
        assert store.get_job(job['job_id']) == before_job  # Normal mode never reconciles manual state.
    finally: fresh.close()


def test_repair_cap_keeps_single_call_and_does_not_change_configuration(setup_runner):
    runner, bundle, calls, config, *_ = setup_runner
    before = config.read_bytes()
    def malformed(*args, **kwargs):
        calls.append(1)
        return ProcessResult(0,b'not JSON',b'')
    runner.executor = malformed
    with pytest.raises(CLIError): runner.run(bundle,repair_limit=0)
    assert len(calls) == 1 and len(runner.store.rows('SELECT * FROM quota_ledger')) == 1
    assert config.read_bytes() == before


def test_manual_process_policy_caps_repairs_even_for_direct_runner(setup_runner, monkeypatch):
    runner, bundle, calls, *_ = setup_runner
    monkeypatch.setenv('RADAR_EXECUTION_MODE','manual-analysis')
    def malformed(*args, **kwargs):
        calls.append(1)
        return ProcessResult(0,b'not JSON',b'')
    runner.executor = malformed
    with pytest.raises(CLIError): runner.run(bundle)
    assert len(calls) == 1


def test_manual_service_loop_never_schedules_or_claims_general_queue(tmp_path, monkeypatch):
    store = Store(tmp_path/'service.db')
    worker = Worker(store,handlers={'analyse':lambda _:{}},execution_mode='manual-analysis',reconciliation_mutex_factory=nullcontext)
    general = store.enqueue('news',priority=0)
    selected = store.enqueue('analyse',{'asset_id':'a','manual_owner':worker.owner})
    monkeypatch.setattr(worker,'schedule',lambda:pytest.fail('Manual service invoked scheduler'))
    original = worker.run_once
    def once(**kwargs):
        assert kwargs == {'job_ids':[selected['job_id']]}
        result = original(**kwargs)
        worker.stop_event.set()
        return result
    monkeypatch.setattr(worker,'run_once',once)
    worker.run_forever()
    assert store.get_job(selected['job_id'])['status'] == 'succeeded'
    assert store.get_job(general['job_id']) == general
    assert not store.rows('SELECT * FROM worker_lease')


def test_ui_api_worker_fake_runner_and_cache_complete_end_to_end(setup_runner, monkeypatch):
    runner, bundle, calls, config, *_ = setup_runner
    original_config = config.read_bytes()
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled':True})
    app = create_app(runner.store.path, profile='real', execution_mode='manual-analysis')
    worker = Worker(Store(runner.store.path), execution_mode='manual-analysis', reconciliation_mutex_factory=nullcontext)
    runner.store = worker.store
    monkeypatch.setattr('backend.worker.AnalysisRunner', lambda *a, **kw: runner)
    worker.start()
    try:
        with TestClient(app, base_url='http://127.0.0.1') as client:
            client.headers['x-radar-token'] = client.get('/api/v1/session').json()['token']
            asset_id = bundle['asset']['asset_id']
            first = post(client,asset_id,'end-to-end-first').json()
            assert worker.run_once(job_ids=[first['id']])
            view = client.get('/api/v1/assets/'+asset_id+'/analysis-job').json()
            assert view['job']['status'] == 'succeeded'
            assert view['result_analysis']['analysis_id'] == view['job']['result']['analysis_id']
            assert view['job']['result']['calls'] == 1 and not view['job']['result']['cache_reused']
            quota_before = worker.store.rows('SELECT * FROM quota_ledger')
            previous_owner = worker.owner
            worker.close()
            worker = Worker(Store(runner.store.path),execution_mode='manual-analysis',reconciliation_mutex_factory=nullcontext)
            runner.store = worker.store
            worker.start()
            assert worker.owner != previous_owner
            second = post(client,asset_id,'end-to-end-cache').json()
            assert worker.run_once(job_ids=[second['id']])
            cached = client.get('/api/v1/assets/'+asset_id+'/analysis-job').json()['job']
            assert cached['status'] == 'succeeded'
            assert cached['result']['cache_reused'] and cached['result']['calls'] == 0
            assert len(calls) == 1 and worker.store.rows('SELECT * FROM quota_ledger') == quota_before
            assert len(worker.store.rows('SELECT * FROM analyses')) == 1
            assert config.read_bytes() == original_config
    finally: worker.close()


def test_cancel_during_fake_call_retains_uncertain_usage_and_terminal_state(setup_runner, monkeypatch):
    runner, bundle, calls, *_ = setup_runner
    monkeypatch.setattr('backend.api.ai_status',lambda:{'enabled':True})
    app = create_app(runner.store.path, profile='real', execution_mode='manual-analysis')
    worker = Worker(Store(runner.store.path), execution_mode='manual-analysis', reconciliation_mutex_factory=nullcontext)
    runner.store = worker.store
    monkeypatch.setattr('backend.worker.AnalysisRunner',lambda *a, **kw:runner)
    worker.start()
    try:
        with TestClient(app,base_url='http://127.0.0.1') as client:
            client.headers['x-radar-token'] = client.get('/api/v1/session').json()['token']
            job = post(client,bundle['asset']['asset_id'],'cancel-running').json()
            def cancel(*args,**kwargs):
                calls.append(1)
                app.state.store.cancel_job(job['id'])
                kwargs['cancel_check']()
                pytest.fail('Cancelled execution continued')
            runner.executor = cancel
            assert worker.run_once(job_ids=[job['id']])
            assert worker.store.get_job(job['id'])['status'] == 'cancelled'
            assert len(calls) == 1
            assert worker.store.rows('SELECT status FROM quota_ledger') == [{'status':'uncertain'}]
            assert worker.store.rows('SELECT status FROM analysis_runs') == [{'status':'interrupted'}]
    finally: worker.close()
