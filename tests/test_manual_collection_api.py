"""Manual queue admission uses an active consumer, not merely an active API.

HTTPX dispatches directly to ASGI. For zero sockets (including asyncio's
self-pipe on Windows), run with docs/qa-20261104/manual-queue/run_isolated.py.
"""
import asyncio
import gc

import httpx
import pytest

from backend.api import create_app


COLLECTION_KINDS = {'refresh', 'backfill', 'probe', 'catalog', 'news', 'prices', 'macro'}
PRESERVED_TABLES = ('jobs', 'job_runs', 'quota_ledger', 'analysis_runs', 'analyses', 'evidence_bundles')


class ASGIClient:
    def __init__(self, app):
        self.app = app
        self.store = app.state.store
        self.loop = asyncio.new_event_loop()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
            base_url='http://127.0.0.1', headers={'x-radar-token': app.state.token})

    def request(self, method, path, *, key=None, **kwargs):
        headers = kwargs.pop('headers', {})
        if key:
            headers = {**headers, 'Idempotency-Key': key}
        return self.loop.run_until_complete(self.client.request(method, '/api/v1' + path,
                                                               headers=headers, **kwargs))

    def close(self):
        self.loop.run_until_complete(self.client.aclose())
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()


@pytest.fixture
def api_factory(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': False, 'reason': 'isolated test'})
    def no_inference(*args, **kwargs):
        pytest.fail('Inference forbidden in manual collection API tests')
    monkeypatch.setattr('backend.cli_runner.AnalysisRunner.run', no_inference)
    clients = []
    def factory(mode='collection-only'):
        app = create_app(tmp_path / f'api-{len(clients)}.db', profile='real', execution_mode=mode)
        client = ASGIClient(app)
        clients.append(client)
        return client
    yield factory
    for client in clients:
        client.close()
    clients.clear()
    gc.collect()


def lease(client, scenario='general', mode='collection-only'):
    if scenario == 'absent':
        return
    scope = scenario if scenario in {'unknown', 'restricted'} else 'general'
    client.store.acquire_worker('qa-private-owner', execution_mode=mode, consumer_scope=scope)
    with client.store.transaction() as con:
        if scenario == 'expired':
            con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
        elif scenario == 'owner_changed':
            con.execute("UPDATE worker_lease SET owner='qa-legacy-owner'")
        elif scenario == 'invalid_mode':
            con.execute("UPDATE worker_lease SET execution_mode='unexpected'")


def snapshot(store):
    existing = {row['name'] for row in store.rows("SELECT name FROM sqlite_master WHERE type='table'")}
    return {table: store.rows('SELECT * FROM ' + table + ' ORDER BY rowid') if table in existing else None
            for table in PRESERVED_TABLES}


def failed_job(store, kind='news'):
    job = store.enqueue(kind)
    with store.transaction() as con:
        con.execute("UPDATE jobs SET status='failed',attempts=2,error_code='synthetic_failure',"
                    "checkpoint_json='{\"cursor\":3}' WHERE job_id=?", (job['job_id'],))
    return job['job_id']


@pytest.mark.parametrize('scenario,reason', [
    ('absent', 'manual_consumer_absent'),
    ('expired', 'manual_consumer_expired'),
    ('unknown', 'manual_consumer_unverified'),
    ('restricted', 'manual_consumer_restricted'),
    ('owner_changed', 'manual_consumer_unverified'),
    ('invalid_mode', 'manual_consumer_incompatible'),
])
@pytest.mark.parametrize('action', ['enqueue', 'retry'])
def test_unavailable_consumer_rejects_without_queue_history_or_quota_changes(api_factory, scenario, reason, action):
    client = api_factory()
    job_id = failed_job(client.store)
    lease(client, scenario)
    before = snapshot(client.store)
    if action == 'enqueue':
        response = client.request('POST', '/jobs', key='rejected-command', json={'kind': 'news'})
    else:
        response = client.request('POST', f'/jobs/{job_id}/retry', key='rejected-command')
    assert response.status_code == 409
    assert response.json()['detail'] == reason
    assert snapshot(client.store) == before
    saved = client.request('GET', '/mutations/rejected-command').json()
    assert saved['status_code'] == 409 and saved['response'] == response.json()
    control = client.request('GET', '/operations').json()['job_control']
    assert control['allowed_kinds'] == [] and control['reason'] == reason
    assert set(control) == {'allowed_kinds', 'reason', 'expires_at'}
    assert 'qa-private-owner' not in response.text and 'qa-private-owner' not in str(control)


@pytest.mark.parametrize('api_mode', ['collection-only', 'analysis-enabled'])
@pytest.mark.parametrize('worker_mode', ['collection-only', 'analysis-enabled'])
@pytest.mark.parametrize('kind', sorted(COLLECTION_KINDS))
def test_general_consumer_accepts_all_collection_commands_and_retry(api_factory, api_mode, worker_mode, kind):
    client = api_factory(api_mode)
    lease(client, mode=worker_mode)
    control = client.request('GET', '/operations').json()['job_control']
    assert COLLECTION_KINDS <= set(control['allowed_kinds']) and control['reason'] == 'ready'
    accepted = client.request('POST', '/jobs', key='accepted-command', json={'kind': kind})
    assert accepted.status_code == 202 and accepted.json()['status'] == 'queued'
    job_id = failed_job(client.store, kind)
    retried = client.request('POST', f'/jobs/{job_id}/retry', key='retried-command')
    assert retried.status_code == 200 and retried.json()['status'] == 'queued'
    assert client.store.get_job(job_id)['attempts'] == 0
    assert client.store.get_job(job_id)['checkpoint'] == {'cursor': 3}
    assert not client.store.rows('SELECT * FROM quota_ledger')


@pytest.mark.parametrize('action', ['enqueue', 'retry'])
def test_rejected_receipt_stays_rejected_after_consumer_appears(api_factory, action):
    client = api_factory()
    job_id = failed_job(client.store)
    path = '/jobs' if action == 'enqueue' else f'/jobs/{job_id}/retry'
    kwargs = {'json': {'kind': 'news'}} if action == 'enqueue' else {}
    first = client.request('POST', path, key='one-request', **kwargs)
    assert first.status_code == 409
    lease(client)
    replay = client.request('POST', path, key='one-request', **kwargs)
    assert replay.status_code == 409 and replay.json() == first.json()
    assert client.store.get_job(job_id)['status'] == 'failed'
    fresh = client.request('POST', path, key='new-request', **kwargs)
    assert fresh.status_code == (202 if action == 'enqueue' else 200)


@pytest.mark.parametrize('action', ['enqueue', 'retry'])
def test_accepted_receipt_replays_after_consumer_disappears_without_repeating_effect(api_factory, action):
    client = api_factory()
    lease(client)
    job_id = failed_job(client.store)
    path = '/jobs' if action == 'enqueue' else f'/jobs/{job_id}/retry'
    kwargs = {'json': {'kind': 'news'}} if action == 'enqueue' else {}
    first = client.request('POST', path, key='accepted-request', **kwargs)
    assert first.status_code == (202 if action == 'enqueue' else 200)
    client.store.release_worker('qa-private-owner')
    before = snapshot(client.store)
    replay = client.request('POST', path, key='accepted-request', **kwargs)
    assert replay.status_code == first.status_code and replay.json() == first.json()
    assert snapshot(client.store) == before
    assert client.request('POST', path, key='different-request', **kwargs).status_code == 409


@pytest.mark.parametrize('api_mode,worker_mode', [
    ('manual-analysis', 'collection-only'), ('collection-only', 'manual-analysis'),
])
def test_manual_analysis_sessions_do_not_accept_collection_commands(api_factory, api_mode, worker_mode):
    client = api_factory(api_mode)
    lease(client, mode=worker_mode)
    before = snapshot(client.store)
    response = client.request('POST', '/jobs', key='manual-session', json={'kind': 'news'})
    assert response.status_code == 409
    assert snapshot(client.store) == before
    assert client.request('GET', '/operations').json()['job_control']['allowed_kinds'] == []


def test_reads_and_cancellation_work_without_consumer(api_factory):
    client = api_factory()
    job = client.store.enqueue('news')
    assert client.request('GET', '/jobs').status_code == 200
    assert client.request('GET', '/operations').status_code == 200
    cancelled = client.request('POST', f"/jobs/{job['job_id']}/cancel", key='cancel-without-worker')
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'


def test_arbitrary_scope_is_invalid_even_when_consumer_is_absent(api_factory):
    client = api_factory()
    response = client.request('POST', '/jobs', key='invalid-scope', json={'kind': 'news', 'scope': {'anything': True}})
    assert response.status_code == 422
    assert not client.store.rows('SELECT * FROM jobs')


@pytest.mark.parametrize('kind', ['analyse', 'analysis'])
@pytest.mark.parametrize('worker_mode', ['collection-only', 'analysis-enabled'])
def test_collection_api_preserves_blocked_analyses_despite_general_consumer(api_factory, kind, worker_mode):
    client = api_factory()
    job_id = failed_job(client.store, kind)
    lease(client, mode=worker_mode)
    before = snapshot(client.store)
    response = client.request('POST', f'/jobs/{job_id}/retry', key='analysis-in-collection')
    assert response.status_code == 409
    assert snapshot(client.store) == before


def test_admission_is_rechecked_in_mutation_transaction_after_stale_ui_read(api_factory):
    client = api_factory()
    lease(client)
    assert client.request('GET', '/operations').json()['job_control']['reason'] == 'ready'
    with client.store.transaction() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    response = client.request('POST', '/jobs', key='worker-expired-after-ui', json={'kind': 'news'})
    assert response.status_code == 409 and response.json()['detail'] == 'manual_consumer_expired'
    assert not client.store.rows('SELECT * FROM jobs')


def test_scoped_claim_revokes_general_admission(api_factory):
    client = api_factory()
    lease(client)
    assert client.request('GET', '/operations').json()['job_control']['reason'] == 'ready'
    assert client.store.claim('qa-private-owner', job_ids=[]) is None
    response = client.request('POST', '/jobs', key='scope-was-restricted', json={'kind': 'news'})
    assert response.status_code == 409 and response.json()['detail'] == 'manual_consumer_restricted'
    assert not client.store.rows('SELECT * FROM jobs')


@pytest.mark.parametrize('kind', [
    'diagnostics', 'refresh_news', 'refresh_prices', 'gdelt', 'gdelt_partition',
    'bcb', 'bundle', 'maintenance', 'demo_task',
])
@pytest.mark.parametrize('consumer_scope', ['general', 'unknown'])
def test_historical_alias_retry_respects_consumer_and_new_request_contract(api_factory, kind, consumer_scope):
    client = api_factory()
    job_id = failed_job(client.store, kind)
    lease(client, consumer_scope)
    before = snapshot(client.store)
    response = client.request('POST', f'/jobs/{job_id}/retry', key='historical-kind-retry')
    if consumer_scope == 'general':
        assert response.status_code == 200 and response.json()['status'] == 'queued'
        assert client.store.get_job(job_id)['kind'] == kind
    else:
        assert response.status_code == 409 and response.json()['detail'] == 'manual_consumer_unverified'
        assert snapshot(client.store) == before
    # Internal/historical kinds remain unavailable as new arbitrary commands.
    assert client.request('POST', '/jobs', key='historical-new-command', json={'kind': kind}).status_code == 422
    assert len(client.store.rows('SELECT * FROM jobs')) == 1
    assert not client.store.rows('SELECT * FROM quota_ledger')


def test_unknown_kind_retry_is_rejected_even_with_general_consumer(api_factory):
    client = api_factory()
    job_id = failed_job(client.store, 'unsupported-fixture-kind')
    lease(client)
    before = snapshot(client.store)
    response = client.request('POST', f'/jobs/{job_id}/retry', key='unsupported-kind-retry')
    assert response.status_code == 409 and response.json()['detail'] == 'manual_consumer_incompatible'
    assert snapshot(client.store) == before
