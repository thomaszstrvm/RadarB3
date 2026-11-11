"""Synthetic read diagnostics: no network, subprocess, or production database."""
import json
import socket
import subprocess
from pathlib import Path

import backend
import sqlite3
import pytest
from backend.api import (create_app, expected_price_session, price_freshness,
                         coverage_resolution, fundamentals_coverage_gap)


@pytest.fixture(autouse=True)
def isolated_only(monkeypatch, tmp_path):
    original = sqlite3.connect
    def connect(database, *args, **kwargs):
        assert Path(database).resolve().is_relative_to(tmp_path.resolve())
        return original(database, *args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError('Network and subprocess forbidden in coverage tests')
    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': False, 'reason': 'synthetic'})


def calendar(monkeypatch, rows):
    monkeypatch.setattr('backend.session_continuity.calendar_context', lambda *a: ([], {'synthetic': True}))
    monkeypatch.setattr('backend.market_calendar.validate_package_envelope', lambda *a: rows)


def day(date, open=True):
    return {'session_date': date, 'is_open': open,
            'closes_at': date + 'T21:00:00Z' if open else None}


@pytest.mark.parametrize('cutoff,expected', [
    ('2026-11-09T20:59:59Z', '2026-11-06'),
    ('2026-11-09T21:00:00Z', '2026-11-06'),
    ('2026-11-09T21:00:01Z', '2026-11-09'),
    ('2026-11-08T15:00:00Z', '2026-11-06'),
])
def test_closed_session_not_calendar_day_age(monkeypatch, cutoff, expected):
    calendar(monkeypatch, [day('2026-11-06'), day('2026-11-07', False),
                           day('2026-11-08', False), day('2026-11-09')])
    assert expected_price_session(None, cutoff) == expected


def test_calendar_gap_or_expiration_is_unknown(monkeypatch):
    calendar(monkeypatch, [day('2026-11-06'), day('2026-11-08', False)])
    assert expected_price_session(None, '2026-11-08T15:00:00Z') is None
    calendar(monkeypatch, [day('2026-11-06')])
    assert expected_price_session(None, '2026-11-09T15:00:00Z') is None


def test_unverified_or_invalid_calendar_is_unknown(monkeypatch):
    monkeypatch.setattr('backend.session_continuity.calendar_context', lambda *a: ([], None))
    assert expected_price_session(None, '2026-11-09T15:00:00Z') is None
    def invalid(*args):
        raise ValueError('synthetic invalid package')
    monkeypatch.setattr('backend.session_continuity.calendar_context', invalid)
    assert expected_price_session(None, '2026-11-09T15:00:00Z') is None


@pytest.mark.parametrize('price,session,expected,complete,state', [
    (None, None, None, False, 'missing'),
    (10, '2026-11-06', None, True, 'unknown'),
    (10, '2026-11-06', '2026-11-09', True, 'stale'),
    (10, '2026-11-09', '2026-11-09', True, 'fresh'),
    (10, '2026-11-09', '2026-11-09', False, 'unknown'),
    (10, '2026-11-10', '2026-11-09', True, 'unknown'),
    (10, None, '2026-11-09', True, 'unknown'),
])
def test_price_freshness_states(price, session, expected, complete, state):
    assert price_freshness(price, session, expected, complete) == state


class ReceiptStore:
    def __init__(self, result):
        self.result = result
    def rows(self, query, args):
        assert "status='succeeded'" in query and 'updated_at<=?' in query
        assert args == ('2026-11-09T22:00:00.000000Z',)
        return [{'updated_at': '2026-11-09T21:30:00Z', 'result_json': json.dumps(self.result)}]


def test_historical_presence_not_current_membership_or_usable_prices():
    assets = [{'eligible': 1, 'price_freshness': x, 'price_window_status': 'partial'}
              for x in ('fresh', 'stale', 'unknown', 'missing')]
    assets += [{'eligible': 0, 'price_freshness': 'fresh', 'price_window_status': 'complete'}]
    result = coverage_resolution(ReceiptStore({'received': 2370, 'provider_catalog_complete': True}),
                                 {}, assets, '2026-11-09T22:00:00Z')
    assert result['eligible_price_freshness'] == dict.fromkeys(('fresh','stale','unknown','missing'), 1)
    assert result['eligible_assets_with_price_usable_at_cutoff'] == 1
    assert result['eligible_assets_with_partial_price_window'] == 4
    assert result['catalog_latest_completed_receipt']['received_records'] == 2370
    assert result['catalog_current_eligible_assets'] is None
    assert result['catalog_scope'] == 'accumulated_catalog_membership_not_reconciled'


@pytest.mark.parametrize('receipt', [{}, {'received': 2370},
    {'received': True, 'provider_catalog_complete': True},
    {'received': -1, 'provider_catalog_complete': True},
    {'received': 2370, 'provider_catalog_complete': False}])
def test_unproved_catalog_receipt_is_not_promoted(receipt):
    result = coverage_resolution(ReceiptStore(receipt), {}, [], '2026-11-09T22:00:00Z')
    assert result['catalog_latest_completed_receipt'] is None
    assert result['catalog_current_eligible_assets'] is None


@pytest.mark.parametrize('count,word', [(None, 'não confirmada'), (0, 'Nenhum'), (1, 'parciais')])
def test_fundamentals_gap_distinguishes_absent_unknown_and_partial(count, word):
    assert word in fundamentals_coverage_gap({'eligible_assets_with_fundamentals': count})


def test_api_exposes_additive_diagnostics_without_inventing_data(tmp_path, monkeypatch):
    app = create_app(tmp_path/'isolated.sqlite3', execution_mode='collection-only')
    with app.state.store.transaction() as con:
        con.execute("INSERT INTO assets(asset_id,ticker,name,asset_type,eligible,provider,updated_at) "
                    "VALUES('a','TEST3','Synthetic','stock',1,'synthetic','2026-11-01T12:00:00Z')")
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, 'endpoint')}
    asset = routes['/api/v1/assets'](page=1, page_size=50)['items'][0]
    assert asset['price'] is None and asset['price_freshness'] == 'missing'
    assert asset['expected_price_session'] is None
    assert asset['stale'] is True and asset['coverage'] == 'ausente'
    data = routes['/api/v1/coverage']()
    assert data['eligible_assets'] == 1
    assert data['assets_with_prices'] == 0
    assert data['eligible_price_freshness']['missing'] == 1
    assert data['catalog_current_eligible_assets'] is None
    assert data['price_freshness_as_of'] == data['as_of']
    overview = routes['/api/v1/overview']()
    assert overview['coverage']['price_freshness_as_of'] == overview['as_of']
    assert not any('Fundamentos CVM indisponíveis' in gap for gap in data['known_gaps'])
    assert any('Catálogo acumulado' in gap for gap in data['known_gaps'])


@pytest.mark.parametrize('endpoint', ['/api/v1/overview', '/api/v1/coverage'])
def test_concurrent_catalog_change_keeps_price_states_own_denominator(tmp_path, monkeypatch, endpoint):
    app = create_app(tmp_path/'concurrent.sqlite3', execution_mode='collection-only')
    store = app.state.store
    def add(asset):
        with store.transaction() as con:
            con.execute("INSERT INTO assets(asset_id,ticker,name,asset_type,eligible,provider,updated_at) "
                        "VALUES(?,?,?,'stock',1,'synthetic','2026-11-01T12:00:00Z')", (asset, asset, asset))
    add('FIRST3')
    original = store.coverage
    def changing_catalog():
        archived = original()
        add('LATE3')
        return archived
    monkeypatch.setattr(store, 'coverage', changing_catalog)
    route = next(route for route in app.routes if route.path == endpoint)
    data = route.endpoint()
    coverage = data['coverage'] if endpoint.endswith('overview') else data
    assert coverage['eligible_assets'] == 1
    assert coverage['eligible_price_freshness_denominator'] == 2
    assert sum(coverage['eligible_price_freshness'].values()) == 2
    assert coverage['coverage_read_consistency'] == 'price_states_share_list_denominator_not_archived_counts_snapshot'
    assert any('leituras independentes' in item for item in coverage['known_gaps'])


def test_real_calendar_and_synthetic_vintage_integrated_cutoffs(tmp_path, monkeypatch):
    from backend import market_calendar as cal, price_vintages as prices
    from backend.ingest import upsert_catalog
    from backend.timeutil import iso, instant
    root = Path(__file__).resolve().parents[1]
    app = create_app(tmp_path/'vintage.sqlite3', profile='demo', execution_mode='collection-only')
    store = app.state.store
    upsert_catalog(store, [{'symbol':'TEST3','name':'Synthetic','assetType':'stock'}], fetched_at='2026-10-01T00:00:00Z')
    monkeypatch.setattr(cal, 'iso', lambda value=None: iso(value or '2026-11-01T03:00:00Z'))
    with store.connect() as con:
        cal.import_calendar_package(con,
            json.loads((root/'docs/qa-20261031/vale3-gates/calendar-package-v1.json').read_text(encoding='utf-8')),
            root/'docs/qa-20261031/data-readiness/calendar/sources')
    monkeypatch.setattr(prices, 'iso', lambda value=None: iso(value or '2026-11-01T04:00:00Z'))
    payload = {'results':[{'symbol':'TEST3', 'currency':'BRL', 'usedInterval':'1d', 'usedRange':'3mo',
        'historicalDataPrice':[{'date':int(instant('2026-10-30T00:00:00Z').timestamp()),
                               'open':100,'high':102,'low':99,'close':101,'adjustedClose':101,'volume':1000}]}]}
    prices.persist_vintage(store, 'brapi:TEST3', json.dumps(payload).encode(), fetched_at='2026-11-01T03:30:00Z')
    route = next(route for route in app.routes if route.path == '/api/v1/assets')
    for cutoff, state, expected in [
        ('2026-11-01T03:59:59Z', 'missing', '2026-10-30'),
        ('2026-11-01T05:00:00Z', 'fresh', '2026-10-30'),
        ('2026-11-03T23:00:00Z', 'stale', '2026-11-03'),
        ('2026-11-04T23:00:00Z', 'unknown', None),
    ]:
        monkeypatch.setattr('backend.api.iso', lambda value=None, cutoff=cutoff: iso(value or cutoff))
        asset = route.endpoint(page=1, page_size=50)['items'][0]
        assert asset['price_freshness'] == state
        assert asset['expected_price_session'] == expected
        assert asset['price'] == (None if state == 'missing' else 101)
        if state == 'fresh':
            assert asset['price_window_status'] == 'partial'
            assert asset['stale'] is False
