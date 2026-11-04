"""Current-window coverage uses synthetic prices and reviewed local calendars."""
import copy
import json
from pathlib import Path
import socket
import subprocess

import pytest

from backend import price_vintages as prices
from backend.core import digest
from backend.timeutil import instant


ROOT = Path(__file__).resolve().parents[1]
AS_OF = '2026-11-04T20:07:00Z'
CURRENT = ROOT / 'docs/qa-20261103/daily/calendar/calendar-package-v2.json'
ORIGINAL = ROOT / 'docs/qa-20261031/vale3-gates/calendar-package-v1.json'


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Window coverage tests must not use network or subprocesses')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def envelope(path=CURRENT):
    document = json.loads(path.read_text(encoding='utf-8'))
    return {'package_id': document['package_id'], 'document_sha256': digest(document),
            'available_at': '2026-11-03T23:00:00Z', 'document': document}


def synthetic_series(package, cutoff=AS_OF):
    days = [day for day, opened, close in package['document']['sessions']
            if opened and '2026-08-04' <= day and instant(close) < instant(cutoff)]
    return {'bars': [{'session_date': day, 'complete_session': True,
                      'close': 100 + i + (i % 3) / 5,
                      'adjustedClose': 100 + i + (i % 3) / 5}
                     for i, day in enumerate(days)]}


def test_complete_window_counts_open_sessions_and_keeps_requested_bounds():
    package = envelope()
    series = synthetic_series(package)
    result = prices.window_coverage(series, package, AS_OF)
    assert result == {
        'requested_start': '2026-08-04', 'requested_end': '2026-11-04',
        'expected_last_session': '2026-11-03',
        'observed_start': '2026-08-04', 'observed_end': '2026-11-03',
        'expected_sessions': 63, 'observed_sessions': 63,
        'missing_sessions': [], 'status': 'complete', 'reason': None,
    }
    assert '2026-11-02' not in {bar['session_date'] for bar in series['bars']}


@pytest.mark.parametrize('missing', ['2026-08-04', '2026-10-28', '2026-11-03'])
def test_initial_internal_and_latest_missing_sessions_are_explicit(missing):
    package = envelope()
    series = synthetic_series(package)
    series['bars'] = [bar for bar in series['bars'] if bar['session_date'] != missing]
    result = prices.window_coverage(series, package, AS_OF)
    assert result['status'] == 'partial'
    assert result['reason'] == 'missing_market_sessions'
    assert result['expected_sessions'] == 63
    assert result['observed_sessions'] == 62
    assert result['missing_sessions'] == [missing]


@pytest.mark.parametrize('cutoff,count', [
    ('2026-11-04T21:59:59Z', 63),
    ('2026-11-04T22:00:00Z', 63),
    ('2026-11-04T22:00:01Z', 64),
])
def test_session_is_expected_only_strictly_after_correction_period(cutoff, count):
    package = envelope()
    # The future date is deliberately present in synthetic input to test cutoff filtering.
    series = synthetic_series(package, '2026-11-04T22:00:01Z')
    result = prices.window_coverage(series, package, cutoff)
    assert result['expected_sessions'] == result['observed_sessions'] == count
    assert result['expected_last_session'] == ('2026-11-04' if count == 64 else '2026-11-03')
    assert result['status'] == 'complete'


def test_outside_window_future_closed_dates_and_duplicates_do_not_inflate_presence():
    package = envelope()
    series = synthetic_series(package)
    extra = [{'session_date': day, 'complete_session': True} for day in
             ('2026-08-03', '2026-11-05', '2026-11-02', '2026-11-03')]
    result = prices.window_coverage({'bars': series['bars'] + extra}, package, AS_OF)
    assert result['observed_sessions'] == 63
    assert result['observed_start'] == '2026-08-04'
    assert result['observed_end'] == '2026-11-03'
    assert result['status'] == 'complete'


def test_incomplete_capture_does_not_become_complete_when_clock_advances():
    package = envelope()
    series = synthetic_series(package, '2026-11-04T22:00:01Z')
    series['bars'][-1]['complete_session'] = False
    result = prices.window_coverage(series, package, '2026-11-04T23:00:00Z')
    assert result['missing_sessions'] == ['2026-11-04']
    assert result['observed_sessions'] == 63


@pytest.mark.parametrize('condition,reason', [
    ('missing', 'calendar_unverified'),
    ('corrupt', 'calendar_inconsistent'),
    ('future', 'calendar_inconsistent'),
    ('outside_window', 'calendar_window_unverified'),
])
def test_calendar_that_cannot_prove_entire_window_never_claims_completeness(condition, reason):
    package = envelope()
    series = synthetic_series(package)
    if condition == 'missing':
        package = None
    elif condition == 'corrupt':
        package['document']['sessions'] = package['document']['sessions'][1:]
    elif condition == 'future':
        package['available_at'] = '2026-11-05T00:00:00Z'
    else:
        package = envelope(ORIGINAL)
    result = prices.window_coverage(series, package, AS_OF)
    assert result['status'] == 'unknown'
    assert result['reason'] == reason
    assert result['expected_sessions'] is None
    assert result['expected_last_session'] is None
    assert result['observed_sessions'] == 63
    assert result['missing_sessions'] == []


def test_initial_gap_does_not_invalidate_shorter_complete_metric_samples():
    package = envelope()
    complete = synthetic_series(package)
    partial = {'bars': complete['bars'][1:]}
    full_metrics = prices.adjusted_metrics(complete, package, AS_OF, 'one-capture')
    partial_metrics = prices.adjusted_metrics(partial, package, AS_OF, 'one-capture')
    assert full_metrics == partial_metrics
    r63 = next(metric for metric in full_metrics if metric['metric_id'] == 'provider_adjusted_change_63')
    assert r63['value'] is None and r63['missing_reason'] == 'insufficient_observations'
    assert all(metric['value'] is not None for metric in full_metrics if metric is not r63)
    assert prices.window_coverage(partial, package, AS_OF)['status'] == 'partial'


def test_projection_adds_current_coverage_without_mutating_capture_or_metrics(monkeypatch):
    package = envelope()
    historical = envelope(ORIGINAL)
    selected = {key: 'synthetic-' + key for key in
                ('vintage_id', 'fetched_at', 'latest_capture_at', 'available_at',
                 'response_sha256', 'policy_sha256')}
    selected.update(normalized=synthetic_series(package), calendar_json=json.dumps(historical))
    before = copy.deepcopy(selected)
    expected_metrics = prices.adjusted_metrics(selected['normalized'], historical, AS_OF, selected['vintage_id'])
    monkeypatch.setattr(prices, 'selected_vintage', lambda *args: selected)
    monkeypatch.setattr(prices, 'calendar_context', lambda *args: ([], package))
    result = prices.price_projection(object(), {}, AS_OF)
    assert result['snapshot']['window_coverage']['status'] == 'complete'
    assert result['metrics'] == expected_metrics
    assert {bar['vintage_id'] for bar in result['bars']} == {selected['vintage_id']}
    assert selected == before


def test_projection_survives_inconsistent_current_calendar_without_rewriting_snapshot(monkeypatch):
    package = envelope()
    selected = {key: 'synthetic-' + key for key in
                ('vintage_id', 'fetched_at', 'latest_capture_at', 'available_at',
                 'response_sha256', 'policy_sha256')}
    selected.update(normalized=synthetic_series(package), calendar_json=json.dumps(package))
    monkeypatch.setattr(prices, 'selected_vintage', lambda *args: selected)
    def invalid(*args):
        raise ValueError('calendar_package_sessions_mismatch')
    monkeypatch.setattr(prices, 'calendar_context', invalid)
    result = prices.price_projection(object(), {}, AS_OF)
    assert result['snapshot']['window_coverage']['status'] == 'unknown'
    assert result['snapshot']['window_coverage']['reason'] == 'calendar_inconsistent'
    assert result['metrics'] == prices.adjusted_metrics(selected['normalized'], package, AS_OF, selected['vintage_id'])
