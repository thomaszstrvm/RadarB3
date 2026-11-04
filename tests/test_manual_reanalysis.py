"""Explicit reanalysis keeps the snapshot and uses one guarded fake call."""
from contextlib import contextmanager, nullcontext
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.cli_process import CLIError, ProcessResult
from backend.cli_runner import POLICY_VERSION, REASONING_EFFORT
from backend.core import Store, digest, dumps, wal_supported
from backend.worker import Worker, Blocked
from test_cli_runner import setup_runner
from test_manual_analysis_mode import manual


@pytest.fixture(autouse=True)
def isolated_manual(monkeypatch):
    monkeypatch.setenv('RADAR_EXECUTION_MODE', 'manual-analysis')

    def forbidden(*args, **kwargs):
        pytest.fail('Reanalysis tests cannot launch subprocesses')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    from backend.cli_process import execute_bounded
    monkeypatch.setitem(execute_bounded.__kwdefaults__, 'popen_factory', forbidden)


def submit(client, key='reanalysis', body=None):
    return client.post('/api/v1/assets/a/analyse', headers={'idempotency-key': key},
                       json={'reanalyze': True} if body is None else body)


def claimed_manual(runner, bundle):
    store = runner.store
    owner = 'synthetic-reanalysis-owner'
    store.acquire_worker(owner, execution_mode='manual-analysis')
    store.worker_owner = owner
    job = store.enqueue('analyse', {'asset_id': bundle['asset']['asset_id'], 'manual_owner': owner, 'reanalyze': True},
                        'synthetic-reanalysis', max_attempts=1)
    return store.claim(owner, job_ids=[job['job_id']])


@pytest.mark.parametrize('body', [
    {'reanalyze': 'true'}, {'reanalyze': 1}, {'reanalyze': None},
    {'manual_owner': 'client-cannot-choose'}, {'reanalyze': True, 'as_of': '2099-01-01'},
    {'reanalyze': True, 'new_request_key': 'client-cannot-choose'},
])
def test_api_rejects_nonboolean_and_extra_fields(manual, body):
    client, store, worker = manual
    worker.start()
    assert submit(client, body=body).status_code == 422
    assert store.list_jobs() == [] and store.rows('SELECT * FROM quota_ledger') == []


def test_explicit_reanalysis_requires_valid_idempotency_key(manual):
    client, store, worker = manual
    worker.start()
    response = client.post('/api/v1/assets/a/analyse', json={'reanalyze': True})
    assert response.status_code == 422
    assert response.json()['detail'] == 'manual_reanalysis_idempotency_key_required'
    assert submit(client, key='invalid key').status_code == 422
    assert store.list_jobs() == [] and store.rows('SELECT * FROM mutation_receipts') == []


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_reanalysis_repeated_clicks_and_changed_intent_do_not_duplicate(manual, status):
    client, store, worker = manual
    worker.start()
    accepted = submit(client)
    assert accepted.status_code == 202
    job_id = accepted.json()['id']
    if status == 'running':
        worker.store.claim(worker.owner, job_ids=[job_id])
    before = store.get_job(job_id)
    receipt = store.rows("SELECT * FROM mutation_receipts WHERE key='reanalysis'")
    assert before['payload'] == {'asset_id': 'a', 'manual_owner': worker.owner, 'reanalyze': True}
    assert submit(client).json() == accepted.json()
    assert submit(client, body={}).status_code == 409
    assert submit(client, body={'reanalyze': False}).status_code == 409
    assert submit(client, key='second-click').json()['detail'] == 'manual_analysis_busy'
    assert store.list_jobs() == [before]
    assert store.rows("SELECT * FROM mutation_receipts WHERE key='reanalysis'") == receipt
    assert store.rows('SELECT * FROM quota_ledger') == []


def test_default_and_false_replay_historical_receipt_fingerprint(manual):
    client, store, worker = manual
    worker.start()
    accepted = client.post('/api/v1/assets/a/analyse', headers={'idempotency-key': 'legacy-key'})
    assert accepted.status_code == 202
    receipt = store.rows('SELECT * FROM mutation_receipts')
    for body in ({}, {'reanalyze': False}):
        response = submit(client, 'legacy-key', body)
        assert response.status_code == 202 and response.json() == accepted.json()
    assert submit(client, 'legacy-key').status_code == 409
    assert store.rows('SELECT * FROM mutation_receipts') == receipt
    assert len(store.list_jobs()) == 1 and 'reanalyze' not in store.list_jobs()[0]['payload']


def test_durable_job_key_cannot_silently_change_analysis_intent(manual):
    client, store, worker = manual
    worker.start()
    prior = store.enqueue('analyse', {'asset_id': 'a', 'manual_owner': worker.owner}, 'existing-job', max_attempts=1)
    store.cancel_job(prior['job_id'])
    response = submit(client, 'existing-job')
    assert response.status_code == 409 and response.json()['detail'] == 'manual_reanalysis_request_mismatch'
    assert len(store.list_jobs()) == 1 and store.rows('SELECT * FROM quota_ledger') == []


@pytest.mark.parametrize('mode', ['collection-only', 'analysis-enabled'])
def test_api_reanalysis_is_manual_only(manual, mode):
    _, store, worker = manual
    worker.start()
    app = create_app(store.path, profile='real', execution_mode=mode)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.headers['x-radar-token'] = client.get('/api/v1/session').json()['token']
        assert submit(client).status_code == 409
    assert store.list_jobs() == [] and store.rows('SELECT * FROM quota_ledger') == []


def test_real_prompt_hash_new_result_and_same_request_cache_preserve_original(setup_runner):
    runner, bundle, calls, config, *_ = setup_runner
    config_before = config.read_bytes()
    original = runner.run(bundle)
    old_row = runner.store.rows('SELECT * FROM analyses')[0]
    job = claimed_manual(runner, bundle)
    executor = runner.executor
    sent = []

    def capture(args, **kwargs):
        sent.append(kwargs['stdin'].decode())
        return executor(args, **kwargs)

    runner.executor = capture
    kwargs = {'job_id': job['job_id'], 'new_request_key': job['job_id'], 'repair_limit': 0}
    fresh = runner.run(bundle, **kwargs)
    assert fresh['calls'] == 1 and not fresh['cache_reused']
    assert fresh['analysis']['analysis_id'] != original['analysis']['analysis_id']
    assert fresh['analysis']['as_of'] == original['analysis']['as_of'] == bundle['as_of']
    assert len(calls) == 2 and len(sent) == 1
    assert calls[0]['envelope']['evidence_bundle'] == calls[1]['envelope']['evidence_bundle'] == bundle
    first_envelope, new_envelope = (dict(call['envelope']) for call in calls)
    first_envelope.pop('analysis_id'); new_envelope.pop('analysis_id')
    assert first_envelope == new_envelope
    system = sent[0].rsplit('\n\n', 1)[0]
    assert dumps({'manual_reanalysis_request': job['job_id']}) in system
    assert 'Não o repita na prosa da análise.' in system
    expected_hash = digest({'system': system,
        'request_template': (runner.prompt_dir/'analysis-request.txt').read_text(encoding='utf-8-sig'),
        'wrapper': 'json_envelope_v1', 'policy': POLICY_VERSION, 'reasoning': REASONING_EFFORT})
    new_row = runner.store.rows('SELECT * FROM analyses WHERE analysis_id=?', (fresh['analysis']['analysis_id'],))[0]
    assert new_row['prompt_hash'] == expected_hash != old_row['prompt_hash']
    assert new_row['bundle_id'] == old_row['bundle_id'] and new_row['schema_hash'] == old_row['schema_hash']
    ledger = runner.store.rows('SELECT * FROM quota_ledger')
    repeated = runner.run(bundle, **kwargs)
    normal = runner.run(bundle)
    assert repeated['cache_reused'] and repeated['calls'] == 0 and repeated['analysis'] == fresh['analysis']
    assert normal['cache_reused'] and normal['analysis'] == original['analysis']
    assert runner.store.rows('SELECT * FROM analyses WHERE analysis_id=?', (old_row['analysis_id'],)) == [old_row]
    assert runner.store.rows('SELECT * FROM quota_ledger') == ledger
    assert len(runner.store.rows('SELECT * FROM analyses')) == len(calls) == 2
    assert config.read_bytes() == config_before


@pytest.mark.parametrize('change', [
    'nonmanual_mode', 'no_job_id', 'different_key', 'no_owner', 'wrong_owner', 'wrong_asset',
    'queued', 'not_requested', 'wrong_kind', 'cancelled', 'expired_worker', 'expired_job',
    'second_attempt', 'reviewed_retry',
])
def test_runner_rejects_unbound_reanalysis_before_quota(setup_runner, monkeypatch, change):
    runner, bundle, calls, *_ = setup_runner
    job = claimed_manual(runner, bundle)
    kwargs = {'job_id': job['job_id'], 'new_request_key': job['job_id'], 'repair_limit': 0}
    if change == 'nonmanual_mode': monkeypatch.setenv('RADAR_EXECUTION_MODE', 'analysis-enabled')
    elif change == 'no_job_id': kwargs['job_id'] = None
    elif change == 'different_key': kwargs['new_request_key'] = 'not-the-job'
    elif change == 'no_owner': runner.store.worker_owner = None
    elif change == 'wrong_owner': runner.store.worker_owner = 'not-the-owner'
    elif change == 'reviewed_retry': kwargs['reviewed_retry'] = object()
    else:
        with runner.store.connect() as con:
            if change in {'wrong_asset', 'not_requested'}:
                payload = dict(job['payload'])
                payload['asset_id' if change == 'wrong_asset' else 'reanalyze'] = 'another-asset' if change == 'wrong_asset' else False
                con.execute('UPDATE jobs SET payload_json=? WHERE job_id=?', (dumps(payload), job['job_id']))
            elif change == 'expired_worker': con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
            elif change == 'expired_job': con.execute("UPDATE jobs SET lease_until='2000-01-01T00:00:00Z'")
            elif change == 'queued': con.execute("UPDATE jobs SET status='queued'")
            elif change == 'wrong_kind': con.execute("UPDATE jobs SET kind='news'")
            elif change == 'cancelled': con.execute('UPDATE jobs SET cancel_requested=1')
            elif change == 'second_attempt': con.execute('UPDATE jobs SET attempts=2')
    with pytest.raises(CLIError, match='cli_manual_reanalysis_scope_required'):
        runner.run(bundle, **kwargs)
    assert calls == [] and runner.store.rows('SELECT * FROM quota_ledger') == []


def test_manual_binding_rechecked_after_mutex_wait(setup_runner):
    runner, bundle, calls, *_ = setup_runner
    job = claimed_manual(runner, bundle)

    @contextmanager
    def changed_while_waiting():
        with runner.store.connect() as con:
            con.execute('UPDATE jobs SET cancel_requested=1 WHERE job_id=?', (job['job_id'],))
        yield

    runner.mutex_factory = changed_while_waiting
    with pytest.raises(CLIError, match='cli_manual_reanalysis_scope_required'):
        runner.run(bundle, job_id=job['job_id'], new_request_key=job['job_id'])
    assert calls == [] and runner.store.rows('SELECT * FROM quota_ledger') == []


@pytest.mark.parametrize('failure', ['invalid_output', 'uncertain', 'after_save'])
def test_one_call_no_repair_and_no_resend_after_failure(setup_runner, monkeypatch, failure):
    runner, bundle, calls, *_ = setup_runner
    job = claimed_manual(runner, bundle)
    if failure == 'after_save':
        finish = runner._finish_run

        def fail_after_save(run_id, status, *args, **kwargs):
            if status == 'validated':
                raise RuntimeError('synthetic failure after immutable analysis commit')
            return finish(run_id, status, *args, **kwargs)

        monkeypatch.setattr(runner, '_finish_run', fail_after_save)
    else:
        def fail(*args, **kwargs):
            calls.append(failure)
            if failure == 'uncertain': raise CLIError('cli_timeout_usage_uncertain', uncertain=True)
            return ProcessResult(0, b'not JSON', b'')
        runner.executor = fail
    kwargs = {'job_id': job['job_id'], 'new_request_key': job['job_id']}
    with pytest.raises((CLIError, RuntimeError)):
        runner.run(bundle, **kwargs)
    assert len(calls) == 1
    ledger = runner.store.rows('SELECT * FROM quota_ledger')
    assert len(ledger) == 1
    runner.executor = lambda *args, **kwargs: pytest.fail('same request must not be resent')
    if failure == 'after_save':
        result = runner.run(bundle, **kwargs)
        assert result['cache_reused'] and result['calls'] == 0
        assert len(runner.store.rows('SELECT * FROM analyses')) == 1
    else:
        with pytest.raises(CLIError, match='cli_previous_attempt_requires_review'):
            runner.run(bundle, **kwargs)
        assert runner.store.rows('SELECT * FROM analyses') == []
    assert runner.store.rows('SELECT * FROM quota_ledger') == ledger and len(calls) == 1


def test_worker_restart_never_resends_interrupted_reanalysis(setup_runner, monkeypatch):
    runner, bundle, calls, *_ = setup_runner
    job = claimed_manual(runner, bundle)

    def interrupted(*args, **kwargs):
        calls.append('synthetic-timeout')
        raise CLIError('cli_timeout_usage_uncertain', uncertain=True)

    runner.executor = interrupted
    with pytest.raises(CLIError, match='cli_timeout_usage_uncertain'):
        runner.run(bundle, job_id=job['job_id'], new_request_key=job['job_id'])
    ledger = runner.store.rows('SELECT * FROM quota_ledger')
    runs = runner.store.rows('SELECT * FROM analysis_runs')
    with runner.store.connect() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    fresh = Worker(Store(runner.store.path), execution_mode='manual-analysis', reconciliation_mutex_factory=nullcontext)
    monkeypatch.setattr('backend.worker.AnalysisRunner', lambda *args, **kwargs: pytest.fail('Restart cannot create a runner'))
    try:
        fresh.start()
        recovered = fresh.store.get_job(job['job_id'])
        assert recovered['status'] == 'blocked' and recovered['error_code'] == 'manual_session_interrupted'
        assert recovered['payload'] == job['payload'] and recovered['result']['usage_uncertain']
        assert fresh.manual_job_ids() == [] and not fresh.run_once(job_ids=[job['job_id']])
        with pytest.raises(CLIError, match='cli_manual_reanalysis_scope_required'):
            runner.run(bundle, job_id=job['job_id'], new_request_key=job['job_id'])
        assert fresh.store.rows('SELECT * FROM quota_ledger') == ledger
        assert fresh.store.rows('SELECT * FROM analysis_runs') == runs and len(calls) == 1
    finally:
        fresh.close()


def test_http_worker_reanalysis_preserves_old_and_displays_new(setup_runner, monkeypatch):
    runner, bundle, calls, *_ = setup_runner
    old = runner.run(bundle)
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': True})
    app = create_app(runner.store.path, profile='real', execution_mode='manual-analysis')
    worker = Worker(Store(runner.store.path, wal=wal_supported()), execution_mode='manual-analysis', reconciliation_mutex_factory=nullcontext)
    runner.store = worker.store
    monkeypatch.setattr('backend.worker.AnalysisRunner', lambda *args, **kwargs: runner)
    worker.start()
    try:
        with TestClient(app, base_url='http://127.0.0.1') as client:
            client.headers['x-radar-token'] = client.get('/api/v1/session').json()['token']
            path = '/api/v1/assets/'+bundle['asset']['asset_id']
            accepted = client.post(path+'/analyse', json={'reanalyze': True}, headers={'idempotency-key': 'new-explicit'})
            assert accepted.status_code == 202
            assert worker.run_once(job_ids=[accepted.json()['id']])
            tracked = client.get(path+'/analysis-job').json()
            assert tracked['job']['status'] == 'succeeded'
            assert tracked['job']['result']['calls'] == 1 and not tracked['job']['result']['cache_reused']
            fresh = client.get(path).json()['analysis']
            assert fresh['analysis_id'] == tracked['result_analysis']['analysis_id'] != old['analysis']['analysis_id']
            assert fresh['as_of'] == old['analysis']['as_of']
            assert len(calls) == len(worker.store.rows('SELECT * FROM quota_ledger')) == 2
            assert len(worker.store.rows('SELECT * FROM analyses')) == 2
            assert client.post(path+'/analyse', json={'reanalyze': True}, headers={'idempotency-key': 'new-explicit'}).json() == accepted.json()
            assert not worker.run_once(job_ids=[accepted.json()['id']])
            assert len(calls) == 2
    finally:
        worker.close()


@pytest.mark.parametrize('payload,mode', [({'reanalyze': True}, 'analysis-enabled'), ({'reanalyze': 'true'}, 'manual-analysis')])
def test_worker_rejects_invalid_reanalysis_scope_before_runner(tmp_path, payload, mode):
    worker = Worker(Store(tmp_path/'invalid.db'), execution_mode=mode)
    with pytest.raises(Blocked, match='manual_reanalysis_scope_required'):
        worker.ensure_job_allowed({'kind': 'analyse', 'payload': payload})
