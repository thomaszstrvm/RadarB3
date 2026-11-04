"""Legacy utilities must not infer, touch unrelated jobs, or pretend no usage."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import pytest

from backend.core import Conflict, Store
from backend.worker import Worker
from backend.safe_http import FetchError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def keep_utility_tests_off_operational_data(monkeypatch, tmp_path):
    original_init = Store.__init__
    original_connect = sqlite3.connect
    def isolated_init(self, path, *args, **kwargs):
        assert Path(path).resolve().is_relative_to(tmp_path.resolve()), 'Operational Store forbidden in regression test'
        return original_init(self, path, *args, **kwargs)
    def isolated_connect(database, *args, **kwargs):
        # Every path here comes from tmp_path; no utility may open data/real.
        path = urllib.request.url2pathname(urlsplit(str(database)).path) if str(database).startswith('file:') else database
        assert Path(path).resolve().is_relative_to(tmp_path.resolve()), 'Operational SQLite forbidden in regression test'
        return original_connect(database, *args, **kwargs)
    monkeypatch.setattr(Store, '__init__', isolated_init)
    monkeypatch.setattr(sqlite3, 'connect', isolated_connect)
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Real HTTP forbidden in script regression'))


def load_script(name):
    spec = importlib.util.spec_from_file_location('qa_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def collection(tmp_path, monkeypatch):
    # Real profile solely for deterministic refresh dispatch, in a temporary DB.
    store = Store(tmp_path / 'isolated-collection.sqlite3')
    script = load_script('collect_once')
    def no_inference(*args, **kwargs):
        pytest.fail('The utility attempted inference')
    monkeypatch.setattr('backend.worker.AnalysisRunner.run', no_inference)
    monkeypatch.setattr('backend.safe_http.SafeHTTP.get', lambda *a, **k: pytest.fail('External request forbidden'))
    def factory(db, **kwargs):
        return Worker(db, handlers={kind: lambda job: {'status': 'succeeded'} for kind in script.CHILD_KINDS}, **kwargs)
    return store, script, factory


@pytest.mark.parametrize('ambient_mode', [None, 'analysis-enabled'])
def test_collection_ignores_old_analysis_and_unrelated_priority_jobs(collection, monkeypatch, ambient_mode):
    store, script, factory = collection
    if ambient_mode is None:
        monkeypatch.delenv('RADAR_EXECUTION_MODE', raising=False)
    else:
        monkeypatch.setenv('RADAR_EXECUTION_MODE', ambient_mode)
    old = [store.enqueue('analyse', {'asset_id': 'fixture'}, priority=0),
           store.enqueue('analysis', {'asset_id': 'fixture'}, priority=0),
           store.enqueue('demo_task', {}, priority=0)]
    reservation = store.reserve_quota('codex_cli', '2026-10-30', 10)
    store.settle_quota(reservation)
    quota_before = store.rows('SELECT * FROM quota_ledger')
    result = script.collect_once(store, worker_factory=factory)
    assert result['completed'] and result['all_succeeded']
    assert len(result['jobs']) == 5
    assert result['iterations'] == 5
    assert [store.get_job(j['job_id']) for j in old] == old
    assert store.rows('SELECT * FROM quota_ledger') == quota_before
    assert not store.rows('SELECT * FROM worker_lease')
    assert os.environ.get('RADAR_EXECUTION_MODE') == ambient_mode


def test_active_worker_rejects_before_enqueuing(collection):
    store, script, factory = collection
    store.acquire_worker('other', execution_mode='collection-only')
    before = store.rows('SELECT * FROM jobs')
    with pytest.raises(Conflict, match='another_worker_is_active'):
        script.collect_once(store, worker_factory=factory)
    assert store.rows('SELECT * FROM jobs') == before
    assert store.rows('SELECT owner FROM worker_lease') == [{'owner': 'other'}]
    with pytest.raises(Conflict, match='another_worker_is_active'):
        script.existing_profile(store.path)


def test_empty_scope_never_claims_or_recovers_unrelated_running_job(collection):
    store, _, _ = collection
    job = store.enqueue('analyse')
    store.acquire_worker('old')
    store.claim('old')
    with store.transaction() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    before = store.get_job(job['job_id'])
    worker = Worker(store, execution_mode='collection-only')
    try:
        assert not worker.run_once(job_ids=[])
        assert store.get_job(job['job_id']) == before
    finally:
        worker.close()


def test_scoped_collection_preserves_unrelated_abandoned_analysis(collection):
    store, script, factory = collection
    job = store.enqueue('analyse')
    store.acquire_worker('old')
    store.claim('old')
    with store.transaction() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    before = store.get_job(job['job_id'])
    assert script.collect_once(store, worker_factory=factory)['all_succeeded']
    assert store.get_job(job['job_id']) == before


def test_collection_waits_for_own_retry_and_reports_partial(collection):
    store, script, factory = collection
    attempts = 0
    def retry_once(job):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise FetchError('fixture_transient', retryable=True, retry_after=0)
        return {'status': 'partial', 'gaps': ['fixture_missing_source']}
    def custom(db, **kwargs):
        worker = factory(db, **kwargs)
        worker.handlers['news'] = retry_once
        return worker
    result = script.collect_once(store, worker_factory=custom)
    assert result['completed'] and not result['all_succeeded']
    assert result['iterations'] == 6
    news = next(j for j in result['jobs'] if j['kind'] == 'news')
    assert news['status'] == 'partial' and news['attempts'] == 2
    assert next(j for j in result['jobs'] if j['kind'] == 'macro')['status'] == 'succeeded'


def test_iteration_limit_does_not_claim_completion_or_reset_waiting_jobs(collection):
    store, script, factory = collection
    def custom(db, **kwargs):
        worker = factory(db, **kwargs)
        worker.handlers['news'] = lambda job: {'status': 'retry_wait'}
        return worker
    result = script.collect_once(store, worker_factory=custom, max_iterations=6, sleep=lambda _: None)
    assert not result['completed'] and result['stop_reason'] == 'iteration_limit'
    news = next(j for j in result['jobs'] if j['kind'] == 'news')
    assert news['status'] == 'retry_wait' and news['attempts'] == 1


def test_time_budget_stops_between_jobs(collection):
    store, script, factory = collection
    clock = [0.0]
    def custom(db, **kwargs):
        worker = factory(db, **kwargs)
        def root(job):
            result = worker.execute(job)
            clock[0] = 2.0
            return result
        worker.handlers['refresh'] = root
        return worker
    result = script.collect_once(store, worker_factory=custom, timeout=1, clock=lambda: clock[0])
    assert not result['completed'] and result['stop_reason'] == 'deadline_between_jobs'
    assert result['iterations'] == 1
    assert all(j['attempts'] == 0 for j in result['jobs'] if j['kind'] != 'refresh')


def test_request_key_resumes_only_own_abandoned_child(collection):
    store, script, factory = collection
    first = script.collect_once(store, worker_factory=factory, request_key='fixed', max_iterations=1)
    assert not first['completed']
    child_id = next(j['job_id'] for j in first['jobs'] if j['kind'] == 'macro')
    unrelated = store.enqueue('analyse')
    store.acquire_worker('old')
    store.claim('old', job_ids=[child_id])
    store.claim('old', job_ids=[unrelated['job_id']])
    with store.transaction() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    untouched = store.get_job(unrelated['job_id'])
    result = script.collect_once(store, worker_factory=factory, request_key='fixed')
    assert result['all_succeeded'] and result['refresh_id'] == first['refresh_id']
    assert store.get_job(child_id)['attempts'] == 2
    assert store.get_job(unrelated['job_id']) == untouched


def test_factory_failure_restores_process_mode(collection, monkeypatch):
    store, script, _ = collection
    monkeypatch.setenv('RADAR_EXECUTION_MODE', 'analysis-enabled')
    def broken(*args, **kwargs):
        raise RuntimeError('fixture')
    with pytest.raises(RuntimeError, match='fixture'):
        script.collect_once(store, worker_factory=broken)
    assert os.environ['RADAR_EXECUTION_MODE'] == 'analysis-enabled'


def database_dump(path):
    with sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True) as con:
        return '\n'.join(con.iterdump())


def test_live_validation_is_read_only_even_when_api_allows_inference(tmp_path, monkeypatch):
    store = Store(tmp_path / 'read-only.sqlite3')
    store.enqueue('analyse')
    before = database_dump(store.path)
    script = load_script('validate_live')
    monkeypatch.setattr(Store, '__init__', lambda *a, **k: pytest.fail('Store construction is not read-only'))
    monkeypatch.setattr(Worker, '__init__', lambda *a, **k: pytest.fail('Validation must not create a worker'))
    calls = []
    def get(base, path):
        calls.append(path)
        return {'status': 'alive'} if path == '/health/live' else {'ai': {'enabled': True}, 'execution_mode': 'analysis-enabled'}
    result = script.validate_live(store.path, get=get)
    assert calls == ['/health/live', '/operations']
    assert result['inference_observation']['result'] == 'no_change_observed'
    assert 'ai_executed' not in result
    assert database_dump(store.path) == before


def test_live_validation_reports_concurrent_observed_usage(tmp_path):
    store = Store(tmp_path / 'observed.sqlite3')
    script = load_script('validate_live')
    def get(base, path):
        if path == '/health/live':
            reservation = store.reserve_quota('codex_cli', '2026-10-30', 10)
            store.settle_quota(reservation, 'uncertain')
        return {}
    result = script.validate_live(store.path, get=get)
    observation = result['inference_observation']
    assert observation['codex_ledger_changed']
    assert observation['result'] == 'activity_or_state_change_observed'
    assert 'não prova' in observation['limitation']


def test_live_validation_offline_error_is_explicit_and_does_not_mutate(tmp_path):
    store = Store(tmp_path / 'offline.sqlite3')
    script = load_script('validate_live')
    before = database_dump(store.path)
    def offline(*args):
        raise OSError('sensitive-value-not-to-log')
    result = script.validate_live(store.path, get=offline)
    assert result['api']['/health/live'] == {'error': 'OSError'}
    assert 'sensitive-value' not in json.dumps(result)
    assert database_dump(store.path) == before


def test_local_get_refuses_external_url_and_redirect_without_network():
    script = load_script('validate_live')
    with pytest.raises(ValueError, match='local_api_required'):
        script.local_get('https://example.org/api/v1', '/operations')
    with pytest.raises(ValueError, match='read_endpoint_not_allowed'):
        script.local_get('http://127.0.0.1:8765/api/v1', '/session')
    handler = script.NoRedirect()
    request = urllib.request.Request('http://127.0.0.1:8765/api/v1/operations')
    assert handler.redirect_request(request, None, 302, 'Found', {}, 'https://example.org/') is None


def test_missing_db_is_not_created(tmp_path):
    path = tmp_path / 'missing.sqlite3'
    for script_name, fn in [('collect_once', 'existing_profile'), ('validate_live', 'database_snapshot')]:
        with pytest.raises(sqlite3.OperationalError):
            getattr(load_script(script_name), fn)(path)
        assert not path.exists()
