"""Synthetic scheduling and read-only projection regressions; no live services."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.core import Store, dumps
from backend.market_calendar import import_calendar_package
from backend.operations import collection_state
from backend.session_continuity import calendar_status, price_schedule_state
from backend.timeutil import instant, iso
from backend.worker import Worker
from scripts.collect_daily import price_skip_reason
from test_daily_activation import system
from test_cumulative_calendar import DIRECTORY, ROOT, reviewed_document


@pytest.fixture(autouse=True)
def no_providers_or_inference(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Temporal tests must not collect, infer or schedule analysis')
    monkeypatch.setattr('backend.safe_http.SafeHTTP.get', forbidden)
    monkeypatch.setattr('backend.cli_runner.AnalysisRunner.run', forbidden)
    monkeypatch.setattr('backend.worker.schedule_analyses', forbidden)
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': False, 'reason': 'synthetic test'})


@pytest.mark.parametrize('when,state,code,next_attempt', [
    ('2026-11-06T19:59:59-03:00', 'waiting_for_close', 'correction_period_not_ended', '2026-11-06T23:00:01.000000Z'),
    ('2026-11-06T20:00:00-03:00', 'waiting_for_close', 'correction_period_not_ended', '2026-11-06T23:00:01.000000Z'),
    ('2026-11-06T20:00:01-03:00', 'ready', None, None),
    ('2026-11-20T20:45:00-03:00', 'closed_session', 'closed_session', None),
    ('2027-01-04T20:45:00-03:00', 'blocked', 'calendar_date_unverified', None),
])
def test_daily_and_worker_gate_share_exact_correction_boundary(system, when, state, code, next_attempt):
    store, _, _ = system
    decision = price_schedule_state(store, when)
    assert decision['automatic_prices'] == state
    assert decision['reason_code'] == price_skip_reason(store, instant(when)) == code
    assert decision['next_attempt_at'] == next_attempt
    source = calendar_status(store, when)
    assert source['status'] == ('unavailable' if state == 'blocked' else 'partial')
    assert source['error_code'] == (code if state == 'blocked' else None)
    assert source['next_attempt_at'] == next_attempt
    assert not store.rows('SELECT * FROM jobs')
    assert not store.rows('SELECT * FROM quota_ledger')


def test_worker_wait_ready_transition_preserves_clocks_and_daily_deduplication(system, monkeypatch):
    store, _, _ = system
    prior_attempt, prior_success = '2026-11-03T12:00:00Z', '2026-11-03T11:00:00Z'
    with store.transaction() as con:
        con.execute('INSERT INTO provider_state VALUES(?,?,?,?,?,?,?)',
                    ('market_calendar', 'unavailable', prior_attempt, prior_success,
                     'verified_session_calendar_required', None, dumps({})))
    class Clock:
        value = instant('2026-11-06T20:00:00-03:00')
        @classmethod
        def now(cls, tz=None):
            return cls.value.astimezone(tz)
    monkeypatch.setattr('backend.worker.datetime', Clock)
    worker = Worker(store, execution_mode='collection-only')
    worker.schedule()
    waiting = store.rows("SELECT * FROM provider_state WHERE provider_id='market_calendar'")[0]
    assert waiting['status'] == 'partial' and waiting['error_code'] is None
    assert waiting['last_attempt_at'] == prior_attempt and waiting['last_success_at'] == prior_success
    assert waiting['next_attempt_at'] == iso(Clock.value + timedelta(seconds=1))
    assert not store.rows("SELECT * FROM jobs WHERE kind='prices'")
    Clock.value += timedelta(seconds=1)
    worker.schedule()
    worker.schedule()
    ready = store.rows("SELECT * FROM provider_state WHERE provider_id='market_calendar'")[0]
    assert ready['error_code'] is None and ready['next_attempt_at'] is None
    assert ready['last_attempt_at'] == prior_attempt and ready['last_success_at'] == prior_success
    assert len(store.rows("SELECT * FROM jobs WHERE kind='prices'")) == 1
    assert not store.rows("SELECT * FROM jobs WHERE kind IN ('analyse','analysis')")
    assert not store.rows('SELECT * FROM worker_lease')
    assert not store.rows('SELECT * FROM quota_ledger')


def test_worker_missing_calendar_does_not_invent_provider_attempt_or_success(tmp_path):
    store = Store(tmp_path / 'missing-calendar.sqlite3', 'real')
    Worker(store, execution_mode='collection-only').schedule()
    state = store.rows("SELECT * FROM provider_state WHERE provider_id='market_calendar'")[0]
    assert state['status'] == 'unavailable' and state['error_code'] == 'calendar_date_unverified'
    assert state['last_attempt_at'] is None and state['last_success_at'] is None
    assert not store.rows("SELECT * FROM jobs WHERE kind='prices'")


def test_future_calendar_cannot_open_gate(tmp_path, monkeypatch):
    store = Store(tmp_path / 'future-calendar.sqlite3', 'real')
    monkeypatch.setattr('backend.market_calendar.iso', lambda value=None: iso(value or '2026-11-07T12:00:00Z'))
    with store.connect() as con:
        import_calendar_package(con, reviewed_document(False), ROOT / 'docs/qa-20261031/data-readiness/calendar/sources', expected_profile='real')
        import_calendar_package(con, reviewed_document(True), DIRECTORY / 'sources', expected_profile='real')
    assert price_schedule_state(store, '2026-11-06T20:00:01-03:00')['reason_code'] == 'calendar_date_unverified'


def test_inconsistent_calendar_cannot_open_gate(system, monkeypatch):
    store, _, _ = system
    now = '2026-11-06T20:00:01-03:00'
    original_rows = store.rows
    def inconsistent_rows(query, args=()):
        rows = original_rows(query, args)
        if 'WHERE version IN' in query:
            rows[0]['source'] = 'synthetic mismatch'
        return rows
    monkeypatch.setattr(store, 'rows', inconsistent_rows)
    decision = price_schedule_state(store, now)
    assert decision['automatic_prices'] == 'blocked'
    assert decision['reason_code'] == 'calendar_package_inconsistent'
    assert calendar_status(store, now)['error_code'] == 'calendar_package_inconsistent'


@pytest.mark.parametrize('when,state', [
    ('2026-11-06T19:59:59-03:00', 'waiting_for_close'),
    ('2026-11-20T20:45:00-03:00', 'closed_session'),
    ('2026-11-06T20:00:01-03:00', 'ready'),
])
def test_read_projections_keep_schedule_distinct_from_errors(system, monkeypatch, when, state):
    store, _, _ = system
    now = iso(when)
    monkeypatch.setattr('backend.api.iso', lambda value=None: iso(value or now))
    monkeypatch.setattr('backend.operations.iso', lambda value=None: iso(value or now))
    app = create_app(store.path, profile='real', execution_mode='collection-only')
    tables = ['provider_state', 'calendar_packages', 'market_sessions', 'jobs', 'analyses', 'quota_ledger']
    before = {table: store.rows('SELECT * FROM ' + table) for table in tables}
    with TestClient(app, base_url='http://127.0.0.1:8000') as client:
        source = next(s for s in client.get('/api/v1/sources').json()['items'] if s['id'] == 'market_calendar')
        operation = next(s for s in client.get('/api/v1/operations').json()['collection']['sources'] if s['provider_id'] == 'market_calendar')
    assert source['status'] == 'partial' and source['error'] is None
    assert operation['error_code'] is None
    assert source['details']['automatic_prices'] == operation['details']['automatic_prices'] == state
    assert source['next_attempt_at'] == operation['next_attempt_at']
    assert (source['next_attempt_at'] is not None) == (state == 'waiting_for_close')
    assert source['last_success_at'] == operation['last_success_at'] == source['details']['available_at']
    assert source['last_success_at'] != now  # documentary availability, not a new collection
    assert source['reason'] == operation['reason']
    assert all(store.rows('SELECT * FROM ' + table) == rows for table, rows in before.items())


def test_read_projection_reports_real_calendar_failure(tmp_path, monkeypatch):
    store = Store(tmp_path / 'no-calendar.sqlite3', 'real')
    state = next(s for s in collection_state(store, [])['sources'] if s['provider_id'] == 'market_calendar')
    assert state['error_code'] == 'calendar_date_unverified'
    assert state['next_attempt_at'] is None and state['last_success_at'] is None
