"""Final integration gaps, using temporary databases and fake executors only."""
from contextlib import nullcontext
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.cli_runner import AnalysisRunner
from backend.core import Store, wal_supported
from backend.evidence import build_bundle
from backend.ingest import upsert_catalog
from backend.worker import Worker
from test_cli_runner import setup_runner
from test_manual_analysis_mode import manual, post


@pytest.fixture(autouse=True)
def forbid_subprocesses(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Final rehearsal cannot launch any subprocess')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    from backend.cli_process import execute_bounded
    monkeypatch.setitem(execute_bounded.__kwdefaults__, 'popen_factory', forbidden)


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_same_asset_repeated_http_clicks_remain_single_job_after_api_restart(manual, status):
    client, store, worker = manual
    worker.start()
    accepted = post(client, key='rehearsal-click')
    assert accepted.status_code == 202
    job_id = accepted.json()['id']
    if status == 'running':
        assert worker.store.claim(worker.owner, job_ids=[job_id])['job_id'] == job_id
    before = store.get_job(job_id)
    receipts = store.rows('SELECT * FROM mutation_receipts')
    assert before['status'] == status

    # A reload creates a fresh API/token, while durable receipts and the active
    # worker slot must still agree. No server or local TCP request is involved.
    reopened = create_app(store.path, profile='real', execution_mode='manual-analysis')
    with TestClient(reopened, base_url='http://127.0.0.1') as restarted_client:
        restarted_client.headers['x-radar-token'] = restarted_client.get('/api/v1/session').json()['token']
        for current_client in (client, restarted_client):
            for key in ('rehearsal-click', 'different-click', 'another-click'):
                response = post(current_client, key=key)
                if key == 'rehearsal-click':
                    assert response.status_code == 202 and response.json() == accepted.json()
                else:
                    assert response.status_code == 409
                    assert response.json()['detail'] == 'manual_analysis_busy'
            tracked = current_client.get('/api/v1/assets/a/analysis-job').json()
            assert tracked['job']['id'] == job_id and tracked['job']['status'] == status
            assert tracked['manual_scope'] == 'current'

    assert store.list_jobs() == [before]
    assert store.rows("SELECT * FROM mutation_receipts WHERE key='rehearsal-click'") == receipts
    # Rejected commands also keep a durable receipt, but no associated job.
    assert store.rows("SELECT key,status_code FROM mutation_receipts WHERE key<>'rehearsal-click' ORDER BY key") == [
        {'key': 'another-click', 'status_code': 409}, {'key': 'different-click', 'status_code': 409}]
    assert store.rows('SELECT * FROM quota_ledger') == []


class SyntheticProcessInterruption(BaseException):
    """Escape Worker error handling without starting or killing a real process."""


def test_store_api_worker_restart_preserves_uncertain_usage_and_cache(setup_runner, monkeypatch):
    runner, bundle, calls, config, report, _ = setup_runner
    monkeypatch.setenv('RADAR_EXECUTION_MODE', 'manual-analysis')
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': True})
    cached_asset = bundle['asset']['asset_id']
    upsert_catalog(runner.store, [{'symbol': 'GAP3', 'name': 'Synthetic interruption only', 'assetType': 'stock'}])
    # Complete the synthetic universe before caching: eligible_assets is part
    # of the evidence bundle and correctly invalidates cache when it changes.
    bundle = build_bundle(runner.store, cached_asset, schema_dir=runner.schema_dir)
    saved = runner.run(bundle)  # The fixture executor returns synthetic JSON.
    interrupted_asset = 'brapi:GAP3'
    path = runner.store.path
    config_before = config.read_bytes()
    app = create_app(path, profile='real', execution_mode='manual-analysis')
    old = Worker(Store(path, wal=wal_supported()), execution_mode='manual-analysis', reconciliation_mutex_factory=nullcontext)
    runner.store = old.store
    monkeypatch.setattr('backend.worker.AnalysisRunner', lambda *args, **kwargs: runner)
    legacy = app.state.store.enqueue('analyse', {'asset_id': cached_asset}, 'rehearsal-legacy')

    def interrupted_executor(*args, **kwargs):
        calls.append('synthetic-interruption')
        raise SyntheticProcessInterruption()

    runner.executor = interrupted_executor
    old.start()
    fresh = None
    try:
        with TestClient(app, base_url='http://127.0.0.1') as client:
            client.headers['x-radar-token'] = client.get('/api/v1/session').json()['token']
            accepted = post(client, interrupted_asset, 'rehearsal-interrupted')
            assert accepted.status_code == 202
            interrupted_id = accepted.json()['id']
            with pytest.raises(SyntheticProcessInterruption):
                old.run_once(job_ids=[interrupted_id])
            assert old.store.get_job(interrupted_id)['status'] == 'running'
            unstarted = old.store.enqueue('analyse', {'asset_id': cached_asset, 'manual_owner': old.owner}, 'rehearsal-unstarted')
            analyses_before = old.store.rows('SELECT * FROM analyses ORDER BY analysis_id')
            ledger_before = old.store.rows('SELECT * FROM quota_ledger ORDER BY reservation_id')
            runs_before = old.store.rows('SELECT * FROM analysis_runs ORDER BY run_id')
            assert sorted(row['status'] for row in ledger_before) == ['consumed', 'uncertain']

        # Simulate loss of the old worker without OS process control: stop its
        # heartbeat, leave its lease/jobs durable, then expire only this tmp DB.
        old.stop_event.set()
        old._heartbeat_thread.join(timeout=2)
        assert not old._heartbeat_thread.is_alive()
        with app.state.store.transaction() as con:
            con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")

        def forbidden_execution(*args, **kwargs):
            pytest.fail('Recovery/cache must not reserve quota or call an executor')

        reopened = create_app(path, profile='real', execution_mode='manual-analysis')
        fresh = Worker(Store(path, wal=wal_supported()), execution_mode='manual-analysis', reconciliation_mutex_factory=nullcontext)
        restarted_runner = AnalysisRunner(fresh.store, schema_dir=runner.schema_dir, prompt_dir=runner.prompt_dir,
                                         config_path=config, report_path=report, executor=forbidden_execution,
                                         mutex_factory=nullcontext)
        monkeypatch.setattr('backend.worker.AnalysisRunner', lambda *args, **kwargs: restarted_runner)
        monkeypatch.setattr(fresh.store, 'reserve_quota', forbidden_execution)
        fresh.start()
        recovered = fresh.store.get_job(interrupted_id)
        assert recovered['status'] == 'blocked' and recovered['error_code'] == 'manual_session_interrupted'
        assert recovered['result'] == {'usage_uncertain': True}
        pending = fresh.store.get_job(unstarted['job_id'])
        assert pending['status'] == 'blocked' and pending['result'] == {'usage_uncertain': False}
        assert fresh.store.get_job(legacy['job_id']) == legacy
        assert fresh.manual_job_ids() == []
        assert not fresh.run_once(job_ids=[interrupted_id, unstarted['job_id'], legacy['job_id']])

        with TestClient(reopened, base_url='http://127.0.0.1') as client:
            client.headers['x-radar-token'] = client.get('/api/v1/session').json()['token']
            replay = post(client, interrupted_asset, 'rehearsal-interrupted')
            assert replay.status_code == 202 and replay.json() == accepted.json()
            tracked = client.get('/api/v1/assets/'+interrupted_asset+'/analysis-job').json()
            assert tracked['job']['status'] == 'blocked' and tracked['manual_scope'] == 'outside'
            assert client.get('/api/v1/operations').json()['manual_analysis']['quota']['used'] == 2

            cached = post(client, cached_asset, 'rehearsal-cache')
            assert cached.status_code == 202
            assert fresh.run_once(job_ids=[cached.json()['id']])
            tracked = client.get('/api/v1/assets/'+cached_asset+'/analysis-job').json()
            assert tracked['job']['status'] == 'succeeded'
            assert tracked['job']['result']['cache_reused'] and tracked['job']['result']['calls'] == 0
            assert tracked['result_analysis']['analysis_id'] == saved['analysis']['analysis_id']

            # A new explicit click must also honor the previous uncertain run;
            # changing the request key cannot silently resend that analysis.
            retry = post(client, interrupted_asset, 'rehearsal-new-key-after-interruption')
            assert retry.status_code == 202
            assert fresh.run_once(job_ids=[retry.json()['id']])
            blocked = fresh.store.get_job(retry.json()['id'])
            assert blocked['status'] == 'blocked' and blocked['error_code'] == 'cli_previous_attempt_requires_review'

        assert fresh.store.get_job(interrupted_id) == recovered
        assert fresh.store.get_job(unstarted['job_id']) == pending
        assert fresh.store.get_job(legacy['job_id']) == legacy
        assert fresh.store.rows('SELECT * FROM analyses ORDER BY analysis_id') == analyses_before
        assert fresh.store.rows('SELECT * FROM quota_ledger ORDER BY reservation_id') == ledger_before
        assert fresh.store.rows('SELECT * FROM analysis_runs ORDER BY run_id') == runs_before
        assert len(calls) == 2 and config.read_bytes() == config_before
        assert fresh.store.rows('PRAGMA integrity_check') == [{'integrity_check': 'ok'}]
    finally:
        old.close()
        if fresh is not None:
            fresh.close()
