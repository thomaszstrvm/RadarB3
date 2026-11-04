from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import importlib.util
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.core import Store, Conflict
from backend.worker import Worker, Blocked
from backend.cli_process import CLIError
from backend.operations import worker_state, collection_state
from backend.timeutil import iso, UTC
from test_cli_runner import setup_runner


@pytest.mark.parametrize('kind', ['analyse', 'analysis'])
@pytest.mark.parametrize('status', ['queued', 'retry_wait', 'paused', 'blocked'])
def test_mixed_queue_holds_analysis_without_attempts(tmp_path, monkeypatch, kind, status):
    store = Store(tmp_path/'queue.db')
    held = store.enqueue(kind, {'asset_id': 'a'}, priority=1)
    with store.transaction() as con:
        con.execute('UPDATE jobs SET status=? WHERE job_id=?', (status, held['job_id']))
    before = store.get_job(held['job_id'])
    data = store.enqueue('news', priority=99)
    monkeypatch.setattr('backend.worker.RSSAdapter.collect', lambda *a: {'status': 'succeeded'})
    worker = Worker(store, execution_mode='collection-only')
    try:
        assert worker.run_once()
        assert store.get_job(data['job_id'])['status'] == 'succeeded'
        assert not worker.run_once()
        assert store.get_job(held['job_id']) == before
        assert not store.rows("SELECT * FROM quota_ledger WHERE provider='codex_cli'")
    finally: worker.close()


@pytest.mark.parametrize('kind', ['analyse', 'analysis'])
def test_abandoned_analysis_recovery_and_explicit_retry_stay_held(tmp_path, kind):
    store = Store(tmp_path/'queue.db')
    job = store.enqueue(kind)
    store.acquire_worker('old', execution_mode='analysis-enabled')
    store.claim('old')
    with store.transaction() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    restarted = Store(store.path)
    restarted.acquire_worker('new', execution_mode='collection-only')
    assert restarted.get_job(job['job_id'])['status'] == 'queued'
    assert restarted.get_job(job['job_id'])['attempts'] == 1
    assert restarted.claim('new') is None
    assert restarted.rows('SELECT status FROM job_runs') == [{'status': 'interrupted'}]
    with restarted.transaction() as con:
        con.execute("UPDATE jobs SET status='paused' WHERE job_id=?", (job['job_id'],))
    restarted.retry_job(job['job_id'])  # Low-level explicit retry may queue; claim still refuses.
    assert restarted.claim('new') is None
    assert restarted.get_job(job['job_id'])['attempts'] == 0
    with pytest.raises(Conflict, match='another_worker'):
        Store(store.path).acquire_worker('second', execution_mode='analysis-enabled')
    restarted.release_worker('new')
    restarted.acquire_worker('approved', execution_mode='analysis-enabled')
    assert restarted.claim('approved')['job_id'] == job['job_id']


def test_direct_execution_and_runner_guard_preserve_history(setup_runner, monkeypatch):
    runner, bundle, calls, *_ = setup_runner
    runner.run(bundle)  # Existing analysis created by the fixture's fake executor.
    calls.clear()
    before = {t: runner.store.rows('SELECT * FROM '+t) for t in ['analyses','evidence_bundles','quota_ledger','analysis_runs']}
    worker = Worker(runner.store, execution_mode='collection-only')
    for kind in ('analyse','analysis'):
        with pytest.raises(Blocked, match='analysis_disabled_by_execution_mode'):
            worker.execute({'kind': kind, 'payload': {}})
    monkeypatch.setenv('RADAR_EXECUTION_MODE', 'collection-only')
    with pytest.raises(CLIError, match='analysis_disabled_by_execution_mode'):
        runner.run(bundle)
    assert calls == []
    assert before == {t: runner.store.rows('SELECT * FROM '+t) for t in before}


def test_schedule_skips_ai_coalesces_and_uses_sao_paulo_day(tmp_path, monkeypatch):
    store = Store(tmp_path/'queue.db')
    worker = Worker(store, execution_mode='collection-only')
    monkeypatch.setattr('backend.worker.schedule_analyses', lambda *a: pytest.fail('AI scheduling forbidden'))
    class Clock(datetime):
        value = datetime(2026,10,30,2,59,tzinfo=UTC)
        @classmethod
        def now(cls, tz=None): return cls.value.astimezone(tz)
    monkeypatch.setattr('backend.worker.datetime', Clock)
    worker.schedule(); worker.schedule()
    assert len(store.rows("SELECT * FROM jobs WHERE kind='macro'")) == 1
    assert store.rows("SELECT dedupe_key FROM jobs WHERE kind='macro'")[0]['dedupe_key'].endswith('2026-10-29')
    Clock.value = datetime(2026,10,30,3,1,tzinfo=UTC)
    worker.schedule()
    assert len(store.rows("SELECT * FROM jobs WHERE kind='macro'")) == 2
    assert not store.rows("SELECT * FROM jobs WHERE kind IN ('analyse','analysis')")
    assert not store.rows('SELECT * FROM worker_lease')


def test_operations_and_manual_commands_no_effect(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': True, 'reason': 'fake homologation'})
    app = create_app(tmp_path/'api.db', execution_mode='collection-only')
    store = app.state.store
    held = store.enqueue('analysis', {'asset_id':'a'})
    with store.transaction() as con:
        con.execute("UPDATE jobs SET status='paused' WHERE job_id=?", (held['job_id'],))
    before = store.get_job(held['job_id'])
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.headers['x-radar-token'] = client.get('/api/v1/session').json()['token']
        assert client.post('/api/v1/assets/a/analyse').status_code == 409
        assert client.post('/api/v1/jobs/'+held['job_id']+'/retry').status_code == 409
        assert store.get_job(held['job_id']) == before
        receipts = store.rows('SELECT * FROM mutation_receipts')
        assert len(receipts) == 1 and receipts[0]['status_code'] == 409
        op = client.get('/api/v1/operations').json()
        assert op['ai']['homologated'] and not op['ai']['enabled']
        assert op['worker']['status'] == 'absent'
        assert op['collection']['latest_news_fetch'] is None
        assert op['latest_analysis_snapshot'] is None
        assert client.get('/api/v1/health/ready').json()['status'] == 'ready'
        store.acquire_worker('qa', execution_mode='collection-only')
        assert client.get('/api/v1/operations').json()['worker']['status'] == 'active'
        future = iso(datetime.now(UTC) + timedelta(minutes=2))
        assert worker_state(store, future)['status'] == 'stale'
        with store.transaction() as con:
            con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
        assert client.get('/api/v1/operations').json()['worker']['status'] == 'stale'
        assert not store.rows('SELECT * FROM quota_ledger')


def test_api_refuses_mismatched_active_collection_worker(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled':True})
    app = create_app(tmp_path/'api.db', execution_mode='analysis-enabled')
    app.state.store.acquire_worker('qa', execution_mode='collection-only')
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.headers['x-radar-token'] = client.get('/api/v1/session').json()['token']
        assert client.post('/api/v1/assets/a/analyse').status_code == 409
        assert not client.get('/api/v1/operations').json()['ai']['enabled']


def test_collection_api_reports_active_ai_worker_divergence(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled':True})
    app = create_app(tmp_path/'api.db', execution_mode='collection-only')
    app.state.store.acquire_worker('independent', execution_mode='analysis-enabled')
    with TestClient(app, base_url='http://127.0.0.1') as client:
        op = client.get('/api/v1/operations').json()
        assert op['mode_mismatch'] and not op['ai']['enabled']
        assert op['worker']['execution_mode'] == 'analysis-enabled'
        assert 'nesta API' in op['ai']['reason']


def test_304_collection_clock_advances_without_new_data(tmp_path, monkeypatch):
    from backend.adapters import RSSAdapter
    from backend.safe_http import Response
    store = Store(tmp_path/'qa.db')
    source = {'id':'br_g1','allowed_hostnames':['g1.globo.com'],'rss_urls':['https://g1.globo.com/feed']}
    class HTTP:
        def get(self, *a, **k): return Response(304,b'',{})
    monkeypatch.setattr('backend.core.iso', lambda: '2026-10-29T22:00:00.000000Z')
    RSSAdapter(store,source,HTTP()).collect('2026-10-29T22:00:00Z')
    state = collection_state(store, ['br_g1'])
    assert state['latest_news_fetch'] == '2026-10-29T22:00:00.000000Z'
    assert state['latest_news_data_at'] is None
    monkeypatch.setattr('backend.core.iso', lambda: '2026-10-30T01:00:00.000000Z')
    RSSAdapter(store,source,HTTP()).collect('2026-10-30T01:00:00Z')
    state = collection_state(store, ['br_g1'])
    assert state['latest_news_fetch'] == '2026-10-30T01:00:00.000000Z'
    assert state['latest_news_data_at'] is None


def test_additive_lease_migration_handles_concurrent_open(tmp_path):
    path = tmp_path/'old.db'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE worker_lease(singleton INTEGER PRIMARY KEY,owner TEXT,heartbeat_at TEXT,expires_at TEXT)')
        con.execute("INSERT INTO worker_lease VALUES(1,'old','2000-01-01T00:00:00Z','2000-01-01T00:01:00Z')")
    with ThreadPoolExecutor(2) as pool:
        stores = list(pool.map(lambda _: Store(path), range(2)))
    row = stores[0].rows('SELECT * FROM worker_lease')[0]
    assert row['owner'] == 'old' and row['execution_mode'] == 'analysis-enabled'


@pytest.mark.parametrize('flags,expected,count', [([], 'collection-only', 2),
    (['--no-worker'], 'collection-only', 1), (['--execution-mode','analysis-enabled'], 'analysis-enabled', 2),
    (['--execution-mode','manual-analysis'], 'manual-analysis', 2)])
def test_launcher_propagates_one_mode_without_starting_services(monkeypatch, flags, expected, count):
    spec = importlib.util.spec_from_file_location('qa_launcher', Path(__file__).resolve().parents[1]/'run.py')
    launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
    monkeypatch.setattr('backend.core.Store', lambda *a, **k: None)
    monkeypatch.setattr('backend.core.wal_supported', lambda: True)
    monkeypatch.setattr(launcher.os, 'chdir', lambda path: None)
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def bind(self, address): assert address[0] == '127.0.0.1'
    monkeypatch.setattr(launcher.socket, 'socket', Socket)
    launches = []
    class Process:
        returncode = 0
        def poll(self): return 0
        def wait(self, **kwargs): return 0
    def spawn(args, **kwargs):
        launches.append((args, kwargs['env']['RADAR_EXECUTION_MODE']))
        return Process()
    monkeypatch.setattr(launcher.subprocess, 'Popen', spawn)
    monkeypatch.setattr(launcher.sys, 'argv', ['run.py', *flags])
    launcher.main()
    assert len(launches) == count and all(mode == expected for _, mode in launches)
    if count == 2:
        assert launches[1][0][-2:] == ['--execution-mode', expected]
