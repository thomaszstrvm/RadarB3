"""Independent calendar/continuity review cases; temporary SQLite only."""
from datetime import date, timedelta

import pytest

from backend.core import Store
from backend.evidence import metrics_for_bars
from backend.market_calendar import import_calendar
from backend.session_continuity import calendar_for_bars, continuity_reason


AS_OF = '2026-10-23T23:00:00Z'


@pytest.fixture
def calendar_store(tmp_path):
    store = Store(tmp_path / 'review-calendar.sqlite3', profile='demo')
    days = [date(2026, 10, 16) + timedelta(days=i) for i in range(8)]
    document = {
        'schema_version': 1, 'source': 'https://example.org/calendar', 'version': 'fixture-v1',
        'verified_at': '2026-10-01T00:00:00Z', 'start': days[0].isoformat(), 'end': days[-1].isoformat(),
        'sessions': [{'session_date': d.isoformat(), 'is_open': d.weekday() < 5,
                      'closes_at': d.isoformat() + 'T20:00:00Z' if d.weekday() < 5 else None} for d in days],
    }
    with store.connect() as con:
        import_calendar(con, document, now=AS_OF)
    return store


def bars(*dates):
    return [{'session_date': d, 'provider': 'fixture', 'currency': 'BRL', 'adjustment': 'split_adjusted',
             'close': 100 + i, 'volume': 1000, 'complete_session': True, 'revision_id': f'fixture-{d}',
             'market_timestamp': d + 'T20:00:00Z', 'quality_flags': []} for i, d in enumerate(dates)]


def test_internal_missing_open_day_blocks_metric(calendar_store):
    annotated = calendar_for_bars(calendar_store, bars('2026-10-21', '2026-10-23'), AS_OF)
    assert continuity_reason(annotated) == 'missing_market_session'
    metric = metrics_for_bars(annotated)[0]
    assert metric['value'] is None
    assert metric['missing_reason'] == 'missing_market_session'


def test_latest_missing_open_day_blocks_metric(calendar_store):
    annotated = calendar_for_bars(calendar_store, bars('2026-10-21', '2026-10-22'), AS_OF)
    assert continuity_reason(annotated) == 'missing_latest_market_session'


def test_weekend_is_explicit_and_preserves_adjacency(calendar_store):
    annotated = calendar_for_bars(calendar_store, bars('2026-10-16', '2026-10-19'), '2026-10-19T23:00:00Z')
    assert continuity_reason(annotated) is None
    assert annotated[-1]['calendar']['closed_dates'] == ['2026-10-17', '2026-10-18']
    assert metrics_for_bars(annotated)[0]['value'] == pytest.approx(.01)


def test_holiday_does_not_count_as_missing_session(calendar_store):
    with calendar_store.transaction() as con:
        con.execute("UPDATE market_sessions SET is_open=0,closes_at=NULL WHERE session_date='2026-10-22'")
    annotated = calendar_for_bars(calendar_store, bars('2026-10-21', '2026-10-23'), AS_OF)
    assert continuity_reason(annotated) is None
    assert annotated[-1]['calendar']['closed_dates'] == ['2026-10-22']


def test_bar_on_closed_day_rejected(calendar_store):
    annotated = calendar_for_bars(calendar_store, bars('2026-10-17', '2026-10-19'), '2026-10-19T23:00:00Z')
    assert continuity_reason(annotated) == 'calendar_unverified'


def test_tail_calendar_gap_cannot_hide_missing_latest_session(calendar_store):
    with calendar_store.transaction() as con:
        con.execute("DELETE FROM market_sessions WHERE session_date IN ('2026-10-21','2026-10-22')")
        con.execute("UPDATE market_sessions SET is_open=0,closes_at=NULL WHERE session_date='2026-10-23'")
    annotated = calendar_for_bars(calendar_store, bars('2026-10-19', '2026-10-20'), AS_OF)
    assert continuity_reason(annotated) == 'calendar_unverified'


@pytest.mark.parametrize('mode', ['absent', 'future_verification'])
def test_calendar_unavailable_at_snapshot(calendar_store, mode):
    with calendar_store.transaction() as con:
        if mode == 'absent':
            con.execute('DELETE FROM market_sessions')
        else:
            con.execute("UPDATE market_sessions SET verified_at='2026-10-24T00:00:00.000000Z'")
    assert continuity_reason(calendar_for_bars(calendar_store, bars('2026-10-22', '2026-10-23'), AS_OF)) == 'calendar_unverified'


def test_intraday_close_not_yet_elapsed_is_not_last_expected(calendar_store):
    annotated = calendar_for_bars(calendar_store, bars('2026-10-21', '2026-10-22'), '2026-10-23T19:00:00Z')
    assert continuity_reason(annotated) is None
    assert annotated[-1]['calendar']['expected_last_session'] == '2026-10-22'
    unfinished = calendar_for_bars(calendar_store, bars('2026-10-22', '2026-10-23'), '2026-10-23T19:00:00Z')
    assert continuity_reason(unfinished) == 'calendar_unverified'


def test_calendar_does_not_grant_adjustment_or_complete_session(calendar_store):
    for field, value, reason in [('adjustment', 'unknown', 'adjustment_unknown'),
                                 ('complete_session', False, 'session_completeness_unverified')]:
        sample = bars('2026-10-22', '2026-10-23')
        for row in sample:
            row[field] = value
        result = metrics_for_bars(calendar_for_bars(calendar_store, sample, AS_OF))[0]
        assert result['value'] is None
        assert result['missing_reason'] == reason


def test_tail_knowledge_is_preserved_in_metadata(calendar_store):
    with calendar_store.transaction() as con:
        con.execute("UPDATE market_sessions SET verified_at='2026-10-22T10:00:00.000000Z' WHERE session_date='2026-10-23'")
    sample = calendar_for_bars(calendar_store, bars('2026-10-21', '2026-10-22'), '2026-10-23T19:00:00Z')
    assert sample[-1]['calendar']['verified_at'] == '2026-10-22T10:00:00.000000Z'


def test_replay_rejects_mixed_calendar_versions(calendar_store):
    sample = calendar_for_bars(calendar_store, bars('2026-10-22', '2026-10-23'), AS_OF)
    sample[-1]['calendar']['version'] = 'different-fixture-version'
    assert continuity_reason(sample) == 'calendar_provenance_mismatch'
