"""HTTP recovery uses durable command identity, never the current resource alone."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.core import Conflict
from backend.timeutil import iso


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Neither a worker nor the CLI starts in these tests; all data is temporary.
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': False, 'reason': 'isolated test'})
    app = create_app(tmp_path / 'receipts.sqlite3', profile='demo')
    app.state.store.acquire_worker('qa-general', consumer_scope='general')
    with app.state.store.transaction() as con:
        con.execute("INSERT INTO assets(asset_id,ticker,name,asset_type,eligible,provider,updated_at) VALUES('a','TEST3','Synthetic','stock',1,'test',?)", (iso(),))
        con.execute("INSERT INTO alerts(alert_id,dedupe_key,asset_id,kind,title,body,created_at) VALUES('notice','notice','a','new_event','Synthetic','Synthetic',?)", (iso(),))
    with TestClient(app, base_url='http://127.0.0.1:8000', raise_server_exceptions=False) as instance:
        instance.headers['x-radar-token'] = instance.get('/api/v1/session').json()['token']
        yield instance


def send(client, method, path, key, body=None):
    kwargs = {} if body is None else {'json': body}
    return client.request(method, '/api/v1' + path, headers={'Idempotency-Key': key}, **kwargs)


def receipt(client, key):
    return client.get('/api/v1/mutations/' + key)


@pytest.mark.parametrize(('method', 'path', 'body', 'status'), [
    ('POST', '/jobs', {'kind': 'refresh'}, 202),
    ('POST', '/assets/a/analyse', None, 202),
    ('POST', '/watchlist', {'asset_id': 'a'}, 200),
    ('DELETE', '/watchlist/a', None, 200),
    ('PATCH', '/alerts/notice', {'read': True}, 200),
    ('POST', '/alert-rules', {'name': 'Synthetic'}, 200),
    ('PATCH', '/settings', {'theme': 'dark', 'horizons_days': [30]}, 200),
])
def test_receipt_and_replay_keep_exact_accepted_response(client, method, path, body, status):
    first = send(client, method, path, 'radar-test-command', body)
    replay = send(client, method, path, 'radar-test-command', body)
    saved = receipt(client, 'radar-test-command')
    assert first.status_code == replay.status_code == status
    assert replay.json() == first.json()
    assert saved.status_code == 200
    assert saved.json() == {
        'key': 'radar-test-command', 'method': method, 'path': '/api/v1' + path,
        'state': 'completed', 'status_code': status, 'response': first.json(),
        'completed_at': saved.json()['completed_at'],
    }
    assert len(client.app.state.store.rows('SELECT * FROM mutation_receipts')) == 1


def test_create_rule_concurrent_requests_and_restart_are_deduplicated(client):
    database = client.app.state.store.path
    # Two app/Store objects also exercise the SQLite boundary across instances.
    other_app = create_app(database, profile='demo')
    barrier = Barrier(2)

    def create(app):
        with TestClient(app, base_url='http://127.0.0.1:8000') as peer:
            peer.headers['x-radar-token'] = peer.get('/api/v1/session').json()['token']
            barrier.wait(timeout=5)
            return send(peer, 'POST', '/alert-rules', 'concurrent-rule', {'name': 'One rule'})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create, app) for app in (client.app, other_app)]
        responses = [future.result(timeout=10) for future in futures]
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert len(client.app.state.store.rows('SELECT * FROM alert_rules')) == 1

    restarted = create_app(database, profile='demo')
    with TestClient(restarted, base_url='http://127.0.0.1:8000') as peer:
        peer.headers['x-radar-token'] = peer.get('/api/v1/session').json()['token']
        assert receipt(peer, 'concurrent-rule').json()['response'] == responses[0].json()
        assert send(peer, 'POST', '/alert-rules', 'concurrent-rule', {'name': 'One rule'}).json() == responses[0].json()
    assert len(restarted.state.store.rows('SELECT * FROM alert_rules')) == 1


def test_key_cannot_identify_another_body_method_or_resource(client):
    original = send(client, 'PATCH', '/settings', 'key-conflict', {'theme': 'dark'})
    assert send(client, 'PATCH', '/settings', 'key-conflict', {'theme': 'light'}).status_code == 409
    assert send(client, 'POST', '/alert-rules', 'key-conflict', {'name': 'Other'}).status_code == 409
    assert send(client, 'DELETE', '/watchlist/a', 'key-conflict').status_code == 409
    assert receipt(client, 'key-conflict').json()['response'] == original.json()
    assert client.app.state.store.setting('theme') == 'dark'
    assert client.app.state.store.rows('SELECT * FROM alert_rules') == []


def test_encoded_asset_path_has_a_decoded_receipt_path(client):
    with client.app.state.store.transaction() as con:
        con.execute("INSERT INTO assets(asset_id,ticker,name,asset_type,eligible,provider,updated_at) VALUES('qa:DEMO3','DEMO3','Synthetic','stock',1,'test',?)", (iso(),))
    assert send(client, 'POST', '/assets/qa%3ADEMO3/analyse', 'encoded-analysis').status_code == 202
    assert receipt(client, 'encoded-analysis').json()['path'] == '/api/v1/assets/qa:DEMO3/analyse'
    assert send(client, 'DELETE', '/watchlist/qa%3ADEMO3', 'encoded-watch').status_code == 200
    assert receipt(client, 'encoded-watch').json()['path'] == '/api/v1/watchlist/qa:DEMO3'


def test_missing_receipt_can_mean_original_request_is_still_running(client, monkeypatch):
    started, release = Event(), Event()
    original = client.app.state.store.enqueue

    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(client.app.state.store, 'enqueue', delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(send, client, 'POST', '/jobs', 'late-command', {'kind': 'refresh'})
        try:
            assert started.wait(timeout=5)
            assert receipt(client, 'late-command').status_code == 404
        finally:
            release.set()
        completed = pending.result(timeout=5)
    assert completed.status_code == 202
    assert receipt(client, 'late-command').json()['response'] == completed.json()
    assert send(client, 'POST', '/jobs', 'late-command', {'kind': 'refresh'}).json() == completed.json()
    assert len(client.app.state.store.list_jobs()) == 1


def test_committed_effect_is_recovered_after_response_delivery_fails(client, monkeypatch):
    from backend.api import JSONResponse as Response

    def lost_response(content, **kwargs):
        if isinstance(content, dict) and content.get('name') == 'Lost response':
            raise RuntimeError('simulated_response_delivery_failure')
        return Response(content, **kwargs)

    monkeypatch.setattr('backend.api.JSONResponse', lost_response)
    result = send(client, 'POST', '/alert-rules', 'lost-response', {'name': 'Lost response'})
    assert result.status_code == 500
    saved = receipt(client, 'lost-response').json()
    assert saved['status_code'] == 200 and saved['response']['id']
    monkeypatch.setattr('backend.api.JSONResponse', Response)
    assert send(client, 'POST', '/alert-rules', 'lost-response', {'name': 'Lost response'}).json() == saved['response']
    assert len(client.app.state.store.rows('SELECT * FROM alert_rules')) == 1


def test_canonical_body_order_is_not_a_conflict(client):
    first = send(client, 'PATCH', '/settings', 'canonical-key', {'theme': 'dark', 'horizons_days': [30]})
    replay = send(client, 'PATCH', '/settings', 'canonical-key', {'horizons_days': [30], 'theme': 'dark'})
    assert replay.status_code == 200
    assert replay.json() == first.json()


def test_settings_and_watchlist_replay_do_not_overwrite_later_decisions(client):
    first = send(client, 'PATCH', '/settings', 'settings-first', {'theme': 'dark'})
    send(client, 'PATCH', '/settings', 'settings-second', {'theme': 'light'})
    assert send(client, 'PATCH', '/settings', 'settings-first', {'theme': 'dark'}).json() == first.json()
    store = client.app.state.store
    assert store.setting('theme') == 'light'
    assert store.rows("SELECT version FROM settings WHERE key='theme'")[0]['version'] == 2
    send(client, 'POST', '/watchlist', 'watch-add', {'asset_id': 'a'})
    send(client, 'DELETE', '/watchlist/a', 'watch-remove')
    assert send(client, 'POST', '/watchlist', 'watch-add', {'asset_id': 'a'}).json()['watchlisted'] is True
    assert store.rows('SELECT * FROM watchlist') == []
    send(client, 'POST', '/watchlist', 'watch-again', {'asset_id': 'a'})
    send(client, 'DELETE', '/watchlist/a', 'watch-remove')
    assert len(store.rows('SELECT * FROM watchlist')) == 1


def test_rule_patch_delete_and_alert_patch_do_not_repeat_effects(client):
    rule_id = send(client, 'POST', '/alert-rules', 'new-rule', {'name': 'Original'}).json()['id']
    path = '/alert-rules/' + rule_id
    first = send(client, 'PATCH', path, 'patch-rule', {'enabled': False})
    send(client, 'PATCH', path, 'patch-rule-later', {'enabled': True})
    assert send(client, 'PATCH', path, 'patch-rule', {'enabled': False}).json() == first.json()
    assert client.get('/api/v1/alert-rules').json()['items'][0]['enabled'] is True
    removed = send(client, 'DELETE', path, 'delete-rule')
    assert send(client, 'DELETE', path, 'delete-rule').json() == removed.json()
    for key, body in [('read', {'read': True}), ('unread', {'read': False})]:
        assert send(client, 'PATCH', '/alerts/notice', key, body).status_code == 200
    send(client, 'PATCH', '/alerts/notice', 'read', {'read': True})
    assert client.get('/api/v1/alerts').json()['items'][0]['read'] is False
    assert receipt(client, 'patch-rule').json()['response'] == first.json()
    assert receipt(client, 'delete-rule').json()['response'] == removed.json()


def test_cancel_and_retry_replay_never_affect_a_new_attempt(client):
    job = send(client, 'POST', '/jobs', 'create-job', {'kind': 'refresh'}).json()
    cancel_path, retry_path = f"/jobs/{job['id']}/cancel", f"/jobs/{job['id']}/retry"
    cancelled = send(client, 'POST', cancel_path, 'cancel-job')
    retried = send(client, 'POST', retry_path, 'retry-job')
    assert cancelled.json()['status'] == 'cancelled'
    assert retried.json()['status'] == 'queued'
    assert send(client, 'POST', cancel_path, 'cancel-job').json() == cancelled.json()
    store = client.app.state.store
    assert store.get_job(job['id'])['status'] == 'queued'
    with store.transaction() as con:
        con.execute("UPDATE jobs SET status='failed',attempts=2 WHERE job_id=?", (job['id'],))
    assert send(client, 'POST', retry_path, 'retry-job').json() == retried.json()
    assert store.get_job(job['id'])['status'] == 'failed'
    assert store.get_job(job['id'])['attempts'] == 2
    assert receipt(client, 'cancel-job').json()['response'] == cancelled.json()
    assert receipt(client, 'retry-job').json()['response'] == retried.json()
    assert len(store.list_jobs()) == 1


def test_error_before_receipt_rolls_back_every_effect(client, monkeypatch):
    from backend.api import dumps as encode

    def fail_receipt(value):
        if isinstance(value, dict) and 'theme' in value:
            raise RuntimeError('simulated_failure_before_receipt')
        return encode(value)

    monkeypatch.setattr('backend.api.dumps', fail_receipt)
    result = send(client, 'PATCH', '/settings', 'rollback-command', {'theme': 'dark', 'news_interval_minutes': 120})
    assert result.status_code == 500
    store = client.app.state.store
    assert store.setting('theme') is None
    assert store.setting('news_interval_minutes') is None
    assert receipt(client, 'rollback-command').status_code == 404
    monkeypatch.setattr('backend.api.dumps', encode)
    assert send(client, 'PATCH', '/settings', 'rollback-command', {'theme': 'dark', 'news_interval_minutes': 120}).status_code == 200


def test_domain_rejection_rolls_back_and_is_durable(client, monkeypatch):
    store = client.app.state.store
    original = store.enqueue
    calls = []

    def reject_after_write(*args, **kwargs):
        calls.append('called')
        original(*args, **kwargs)
        raise Conflict('simulated_rejection')

    monkeypatch.setattr(store, 'enqueue', reject_after_write)
    first = send(client, 'POST', '/jobs', 'rejected-command', {'kind': 'refresh'})
    replay = send(client, 'POST', '/jobs', 'rejected-command', {'kind': 'refresh'})
    assert first.status_code == replay.status_code == 409
    assert first.json() == replay.json() == {'detail': 'simulated_rejection'}
    assert store.list_jobs() == []
    assert calls == ['called']
    assert receipt(client, 'rejected-command').json()['status_code'] == 409


def test_receipt_does_not_persist_cli_or_job_diagnostics(client, monkeypatch):
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': False, 'private_path': 'NEVER-PERSIST'})
    settings = send(client, 'PATCH', '/settings', 'safe-settings', {'theme': 'dark'})
    assert 'ai' not in settings.json()
    store = client.app.state.store
    job = store.enqueue('refresh', dedupe_key='diagnostic-job')
    with store.transaction() as con:
        con.execute("UPDATE jobs SET checkpoint_json=?,result_json=? WHERE job_id=?",
                    ('{"private_path":"NEVER-PERSIST"}', '{"secret":"NEVER-PERSIST"}', job['job_id']))
    result = send(client, 'POST', f"/jobs/{job['job_id']}/cancel", 'safe-job')
    assert result.status_code == 200
    assert 'checkpoint' not in result.json() and 'result' not in result.json()
    assert all('NEVER-PERSIST' not in row['response_json'] for row in store.rows('SELECT response_json FROM mutation_receipts'))


def test_receipts_obey_local_boundary_and_missing_is_unknown(client):
    send(client, 'POST', '/alert-rules', 'local-only', {'name': 'Local'})
    path = '/api/v1/mutations/local-only'
    assert client.get(path, headers={'Origin': 'https://attacker.example'}).status_code == 403
    assert client.get(path, headers={'Host': 'attacker.example'}).status_code == 400
    assert client.get(path, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert receipt(client, 'never-received').status_code == 404
    assert send(client, 'POST', '/alert-rules', 'invalid key', {'name': 'No'}).status_code == 422
    assert len(client.app.state.store.rows('SELECT * FROM alert_rules')) == 1
