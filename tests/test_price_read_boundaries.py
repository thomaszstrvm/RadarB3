"""Read projections use a single known cutoff, never collectors or real AI."""
import socket
import subprocess

import pytest

from backend.api import create_app
from backend.ingest import ingest_price
from backend.price_vintages import adjusted_metrics, window_coverage
from backend.session_continuity import price_session_state
from backend.timeutil import iso
from test_price_session_regression import price_store, insert_legacy_price, ASSET
from test_price_vintages import store, capture
from test_window_coverage import envelope, synthetic_series


@pytest.fixture(autouse=True)
def no_network_or_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Boundary tests cannot use network or subprocesses')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': False, 'reason': 'isolated'})


@pytest.mark.parametrize('include_unfinished', [False, True])
def test_metric_close_boundary_matches_coverage_and_schedule(include_unfinished):
    package = envelope()
    series = synthetic_series(package, '2026-11-04T22:00:01Z' if include_unfinished else '2026-11-04T21:59:59Z')
    before = adjusted_metrics(series, package, '2026-11-04T21:59:59Z', 'synthetic')
    exact = adjusted_metrics(series, package, '2026-11-04T22:00:00Z', 'synthetic')
    after = adjusted_metrics(series, package, '2026-11-04T22:00:01Z', 'synthetic')
    assert exact == before
    assert exact[0]['value'] is not None
    assert window_coverage(series, package, '2026-11-04T22:00:00Z')['expected_last_session'] == '2026-11-03'
    assert price_session_state(package, '2026-11-04T22:00:00Z')['automatic_prices'] == 'waiting_for_close'
    assert after[0]['end_at'] == ('2026-11-04' if include_unfinished else None)
    assert after[0]['missing_reason'] == (None if include_unfinished else 'missing_latest_market_session')


def handlers(store, monkeypatch, cutoff):
    monkeypatch.setattr('backend.api.iso', lambda value=None: iso(value or cutoff))
    app = create_app(store.path, profile=store.profile, execution_mode='collection-only')
    return {route.path: route.endpoint for route in app.routes if hasattr(route, 'endpoint')}


def assert_views(routes, expected, session):
    listing = routes['/api/v1/assets'](page=1, page_size=50)['items'][0]
    detail = routes['/api/v1/assets/{asset_id}'](ASSET)
    history = routes['/api/v1/assets/{asset_id}/prices'](ASSET)
    assert listing['price'] == detail['price'] == expected
    assert listing['session_date'] == detail['session_date'] == session
    assert [row['close'] for row in history['items']] == ([] if expected is None else [expected])
    assert detail['prices'] == history['items']


@pytest.mark.parametrize('cutoff,future_session', [
    ('2026-10-26T12:00:00Z', '2026-10-27'),
    ('2026-10-26T02:59:59.999999Z', '2026-10-26'),
])
def test_legacy_future_session_is_not_exposed_or_rewritten(price_store, monkeypatch, cutoff, future_session):
    ingest_price(price_store, ASSET, {'session_date': '2026-10-24', 'close': 10, 'fetched_at': '2026-10-25T12:00:00Z'})
    insert_legacy_price(price_store, future_session, None, '2026-10-25T12:00:00Z')
    routes = handlers(price_store, monkeypatch, cutoff)
    before = price_store.rows('SELECT * FROM price_revisions')
    assert_views(routes, 10, '2026-10-24')
    assert price_store.rows('SELECT * FROM price_revisions') == before


@pytest.mark.parametrize('clock', ['available_at', 'fetched_at', 'market_timestamp'])
def test_legacy_future_revision_uses_earlier_known_revision(price_store, monkeypatch, clock):
    first = ingest_price(price_store, ASSET, {'session_date': '2026-10-24', 'close': 10, 'fetched_at': '2026-10-25T12:00:00Z'})
    row = price_store.rows('SELECT * FROM price_revisions')[0]
    row.update(revision_id='future-revision', content_hash='future', close=999)
    row[clock] = iso('2026-10-27T12:00:00Z')
    with price_store.transaction() as con:
        con.execute('INSERT INTO price_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', tuple(row.values()))
        con.execute('UPDATE price_bars SET latest_revision_id=? WHERE bar_id=?', (row['revision_id'], first['bar_id']))
    routes = handlers(price_store, monkeypatch, '2026-10-26T12:00:00Z')
    before = price_store.rows('SELECT * FROM price_revisions')
    assert_views(routes, 10, '2026-10-24')
    assert price_store.rows('SELECT * FROM price_revisions') == before


def test_legacy_only_future_revision_is_missing_not_zero(price_store, monkeypatch):
    ingest_price(price_store, ASSET, {'session_date': '2026-10-24', 'close': 999, 'fetched_at': '2026-10-27T12:00:00Z'})
    assert_views(handlers(price_store, monkeypatch, '2026-10-26T12:00:00Z'), None, None)


@pytest.mark.parametrize('status,error', [('unavailable', 'rate_limited'), ('partial', 'history_incomplete')])
def test_saved_capture_does_not_mask_later_provider_failure(store, monkeypatch, status, error):
    capture(store)
    monkeypatch.setattr('backend.core.iso', lambda value=None: iso(value or '2026-11-01T04:00:00Z'))
    store.provider_status('brapi_prices', status, error, {'diagnostic': 'synthetic'}, next_attempt_at='2026-11-01T05:00:00Z')
    routes = handlers(store, monkeypatch, '2026-11-01T04:01:00Z')
    before = store.rows('SELECT * FROM provider_state')
    source = next(x for x in routes['/api/v1/sources']()['items'] if x['id'] == 'brapi_prices')
    assert source['status'] == status
    assert source['error'] == source['reason'] == error
    assert source['count'] == 63 and source['available_end'] == '2026-10-30'
    assert source['next_attempt_at'] == iso('2026-11-01T05:00:00Z')
    assert source['details']['diagnostic'] == 'synthetic'
    assert source['details']['vintage_id']
    assert store.rows('SELECT * FROM provider_state') == before


def test_equal_clock_revision_tie_keeps_last_insertion_and_bundle_agreement(price_store, monkeypatch):
    first = ingest_price(price_store, ASSET, {'session_date': '2026-10-24', 'close': 10, 'fetched_at': '2026-10-25T12:00:00Z'})
    row = price_store.rows('SELECT * FROM price_revisions')[0]
    # IDs are hashes, not chronology: deliberately sort the later revision first alphabetically.
    row.update(revision_id='000-latest', content_hash='tie-correction', close=20)
    with price_store.transaction() as con:
        con.execute('INSERT INTO price_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', tuple(row.values()))
        con.execute('UPDATE price_bars SET latest_revision_id=? WHERE bar_id=?', (row['revision_id'], first['bar_id']))
    assert_views(handlers(price_store, monkeypatch, '2026-10-26T12:00:00Z'), 20, '2026-10-24')
    from backend.evidence import build_bundle
    from test_price_session_regression import SCHEMAS
    import json
    bundle = build_bundle(price_store, ASSET, '2026-10-26T12:00:00Z', SCHEMAS)
    observed = [json.loads(e['content'])['close'] for e in bundle['evidence'] if e['record_type'] == 'price']
    assert observed == [20]


def test_known_null_revision_is_not_replaced_by_older_price(price_store, monkeypatch):
    ingest_price(price_store, ASSET, {'session_date': '2026-10-24', 'close': 10, 'fetched_at': '2026-10-25T12:00:00Z'})
    ingest_price(price_store, ASSET, {'session_date': '2026-10-24', 'close': None, 'fetched_at': '2026-10-26T11:00:00Z'})
    routes = handlers(price_store, monkeypatch, '2026-10-26T12:00:00Z')
    listing = routes['/api/v1/assets'](page=1, page_size=50)['items'][0]
    detail = routes['/api/v1/assets/{asset_id}'](ASSET)
    history = routes['/api/v1/assets/{asset_id}/prices'](ASSET)
    assert listing['price'] is None and detail['price'] is None
    assert listing['session_date'] == '2026-10-24'
    assert [row['close'] for row in history['items']] == [None]


def test_capture_without_provider_state_remains_partial_and_read_only(store, monkeypatch):
    capture(store)
    routes = handlers(store, monkeypatch, '2026-11-01T04:01:00Z')
    source = next(x for x in routes['/api/v1/sources']()['items'] if x['id'] == 'brapi_prices')
    assert source['status'] == 'partial' and source['error'] is None
    assert source['count'] == 63
    assert not store.rows('SELECT * FROM provider_state')


def test_newer_capture_supersedes_older_failure_without_rewriting_history(store, monkeypatch):
    monkeypatch.setattr('backend.core.iso', lambda value=None: iso(value or '2026-11-01T02:00:00Z'))
    store.provider_status('brapi_prices', 'unavailable', 'fixture_failure', next_attempt_at='2026-11-01T05:00:00Z')
    capture(store)
    before = store.rows('SELECT * FROM provider_state')
    routes = handlers(store, monkeypatch, '2026-11-01T04:01:00Z')
    source = next(x for x in routes['/api/v1/sources']()['items'] if x['id'] == 'brapi_prices')
    assert source['status'] == 'partial' and source['error'] is None
    assert source['next_attempt_at'] is None
    assert source['last_success_at'] == iso('2026-11-01T02:17:43.178290Z')
    assert store.rows('SELECT * FROM provider_state') == before


def test_later_success_without_new_capture_keeps_both_clocks(store, monkeypatch):
    capture(store)
    monkeypatch.setattr('backend.core.iso', lambda value=None: iso(value or '2026-11-01T04:00:00Z'))
    store.provider_status('brapi_prices', 'available')
    routes = handlers(store, monkeypatch, '2026-11-01T04:01:00Z')
    source = next(x for x in routes['/api/v1/sources']()['items'] if x['id'] == 'brapi_prices')
    assert source['status'] == 'partial' and source['error'] is None
    assert source['last_success_at'] == source['last_attempt_at'] == iso('2026-11-01T04:00:00Z')
    assert source['details']['available_at'] == iso('2026-11-01T03:20:00Z')


def test_failure_without_attempt_clock_is_not_silently_hidden(store, monkeypatch):
    capture(store)
    with store.transaction() as con:
        con.execute('INSERT INTO provider_state VALUES(?,?,?,?,?,?,?)',
                    ('brapi_prices','unavailable',None,None,'fixture_failure',None,'{}'))
    routes = handlers(store, monkeypatch, '2026-11-01T04:01:00Z')
    source = next(x for x in routes['/api/v1/sources']()['items'] if x['id'] == 'brapi_prices')
    assert source['status'] == 'unavailable'
    assert source['reason'] == source['error'] == 'fixture_failure'
    assert source['last_attempt_at'] is None and source['count'] == 63
