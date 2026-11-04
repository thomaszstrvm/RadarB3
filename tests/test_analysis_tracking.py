"""Read-only tracking tests: temporary SQLite, no worker, provider or model."""
import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.core import dumps

NOW = '2026-10-26T12:00:00Z'
EARLY = '2026-10-26T10:00:00Z'
LATER = '2026-10-26T11:00:00Z'
FUTURE = '2026-10-27T12:00:00Z'


@pytest.fixture
def client(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Tracking must not call AI, a provider or executor')

    monkeypatch.setattr('backend.api.iso', lambda: NOW)
    monkeypatch.setattr('backend.api.ai_status', forbidden)
    monkeypatch.setattr('backend.safe_http.SafeHTTP.get', forbidden)
    monkeypatch.setattr('backend.cli_runner.AnalysisRunner.run', forbidden)
    monkeypatch.setattr('backend.cli_process.execute_bounded', forbidden)
    app = create_app(tmp_path / 'tracking.sqlite3', profile='real')
    with app.state.store.transaction() as con:
        for asset_id in ('a', 'b', 'empty'):
            con.execute("INSERT INTO assets(asset_id,ticker,name,asset_type,eligible,provider,updated_at) VALUES(?,?,?,'stock',1,'synthetic',?)",
                        (asset_id, asset_id.upper() + '3', 'Synthetic tracking fixture', NOW))
    with TestClient(app, base_url='http://127.0.0.1:8000') as http:
        yield http


def insert_job(client, job_id, *, asset_id='a', kind='analyse', status='queued', created_at=EARLY,
               priority=50, not_before=EARLY, cancel_requested=0, result=None, error=None):
    with client.app.state.store.transaction() as con:
        con.execute('''INSERT INTO jobs(job_id,kind,payload_json,dedupe_key,status,priority,not_before,
                       created_at,updated_at,cancel_requested,result_json,error_code)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (job_id, kind, dumps({'asset_id': asset_id}), job_id, status, priority,
                     not_before, created_at, LATER, cancel_requested, dumps(result) if result else None, error))


def tracking(client, asset_id='a'):
    response = client.get(f'/api/v1/assets/{asset_id}/analysis-job')
    assert response.status_code == 200, response.text
    return response.json()


def insert_analysis(client, analysis_id, asset_id='a'):
    # Only persisted identity/status metadata is used by this route; no financial
    # output is generated, validated or passed to a model in these fixtures.
    with client.app.state.store.transaction() as con:
        con.execute('INSERT INTO evidence_bundles VALUES(?,?,?,?,?,?)',
                    ('bundle-' + analysis_id, asset_id, EARLY, analysis_id, '{}', EARLY))
        con.execute('INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (analysis_id, asset_id, 'bundle-' + analysis_id, EARLY, 'synthetic', analysis_id,
                     'synthetic', 'insufficient_data', '{}', EARLY))


def test_no_job_and_unknown_asset(client):
    value = tracking(client, 'empty')
    assert value == {'asset_id': 'empty', 'job': None, 'queue_position': None,
                     'queue_position_reason': 'no_job', 'result_analysis': None, 'as_of': NOW}
    assert client.get('/api/v1/assets/missing/analysis-job').status_code == 404


@pytest.mark.parametrize('status', ['running', 'queued', 'retry_wait'])
def test_inflight_job_preferred_to_newer_terminal_with_stable_ties(client, status):
    insert_job(client, 'active-a', kind='analysis', status=status)
    insert_job(client, 'active-z', status=status)
    insert_job(client, 'newer-terminal', status='succeeded', created_at=LATER)
    insert_job(client, 'other-asset', asset_id='b', created_at=FUTURE)
    insert_job(client, 'unrelated-kind', kind='news', created_at=FUTURE)
    selected = tracking(client)['job']
    assert selected['id'] == 'active-z'
    assert selected['status'] == status
    assert selected['updated_at'] == LATER
    assert tracking(client, 'b')['job']['id'] == 'other-asset'


@pytest.mark.parametrize('status', ['blocked', 'failed', 'cancelled', 'paused', 'succeeded', 'partial'])
def test_terminal_and_paused_status_are_not_reported_as_queue_progress(client, status):
    insert_job(client, 'older', status='succeeded')
    insert_job(client, 'last-a', status=status, created_at=LATER, error='synthetic_error')
    insert_job(client, 'last-z', status=status, created_at=LATER, error='synthetic_error')
    value = tracking(client)
    assert value['job']['id'] == 'last-z'
    assert value['job']['status'] == status
    assert value['job']['error'] == 'synthetic_error'
    assert value['queue_position'] is None
    assert value['queue_position_reason'] == 'not_waiting'


def test_queue_position_matches_global_ready_priority_and_tiebreaks(client):
    insert_job(client, 'target-m')
    insert_job(client, 'other-priority', asset_id='b', kind='news', priority=10)
    insert_job(client, 'target-a', asset_id='b')
    insert_job(client, 'target-z', asset_id='b')
    insert_job(client, 'already-running', asset_id='b', status='running', priority=1)
    insert_job(client, 'future-retry', asset_id='b', status='retry_wait', priority=1, not_before=FUTURE)
    insert_job(client, 'cancelled-request', asset_id='b', priority=1, cancel_requested=1)
    insert_job(client, 'paused', asset_id='b', status='paused', priority=1)
    insert_job(client, 'due-retry', asset_id='b', status='retry_wait', priority=20, not_before=NOW)
    value = tracking(client)
    assert value['job']['id'] == 'target-m'
    assert value['queue_position'] == 4  # priority 10, priority 20, same-priority id a
    assert value['queue_position_reason'] is None


@pytest.mark.parametrize('status,not_before,cancel,reason', [
    ('retry_wait', FUTURE, 0, 'not_ready'),
    ('queued', FUTURE, 0, 'not_ready'),
    ('queued', EARLY, 1, 'cancellation_requested'),
    ('running', EARLY, 0, 'not_waiting'),
])
def test_unavailable_queue_position_has_reason(client, status, not_before, cancel, reason):
    insert_job(client, 'selected', status=status, not_before=not_before, cancel_requested=cancel)
    value = tracking(client)
    assert value['queue_position'] is None
    assert value['queue_position_reason'] == reason
    assert value['job']['not_before'] == not_before


@pytest.mark.parametrize('result_id,owner,expected', [
    ('saved', 'a', {'analysis_id': 'saved', 'status': 'insufficient_data', 'as_of': EARLY}),
    ('saved', 'b', None),
    ('missing', 'a', None),
])
def test_result_requires_persisted_analysis_for_same_asset(client, result_id, owner, expected):
    insert_analysis(client, 'saved', owner)
    insert_job(client, 'done', status='succeeded', result={'analysis_id': result_id})
    assert tracking(client)['result_analysis'] == expected


def test_reload_uses_persisted_job_and_gets_do_not_write(client):
    insert_job(client, 'persisted', status='retry_wait', not_before=FUTURE)
    store = client.app.state.store
    tables = ['jobs', 'job_runs', 'analyses', 'evidence_bundles', 'quota_ledger', 'settings', 'provider_state']
    before = {table: store.rows('SELECT * FROM ' + table) for table in tables}
    first = tracking(client)
    with TestClient(create_app(store.path, profile='real'), base_url='http://127.0.0.1:8000') as reloaded:
        assert tracking(reloaded) == first
        assert tracking(reloaded, 'empty')['job'] is None
    assert {table: store.rows('SELECT * FROM ' + table) for table in tables} == before


def test_lookup_is_not_limited_to_recent_500_jobs(client):
    insert_job(client, 'old-active')
    with client.app.state.store.transaction() as con:
        for number in range(501):
            key = f'unrelated-{number:03}'
            con.execute('''INSERT INTO jobs(job_id,kind,payload_json,dedupe_key,status,not_before,created_at,updated_at)
                           VALUES(?,'news','{}',?,'succeeded',?,?,?)''', (key, key, LATER, LATER, LATER))
    assert tracking(client)['job']['id'] == 'old-active'
