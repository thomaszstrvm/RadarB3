import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from backend.core import Store
from backend.market_calendar import import_calendar, validate_calendar

NOW = '2026-10-29T20:00:00Z'


def document():
    return {'schema_version': 1, 'source': 'https://example.org/calendar', 'version': 'fixture-v1',
            'verified_at': '2026-10-28T12:00:00-03:00', 'start': '2026-10-23', 'end': '2026-10-26',
            'sessions': [{'session_date': f'2026-10-{day}', 'is_open': day in (23, 26),
                          'closes_at': f'2026-10-{day}T17:00:00-03:00' if day in (23, 26) else None}
                         for day in range(23, 27)]}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'demo.sqlite3', profile='demo')


def test_import_replay_and_provenance(store):
    with store.connect() as con:
        assert import_calendar(con, document(), NOW)['inserted'] == 4
        assert import_calendar(con, document(), NOW)['unchanged'] == 4
        rows = con.execute('SELECT * FROM market_sessions ORDER BY session_date').fetchall()
    assert len(rows) == 4
    assert rows[0]['source'] == 'https://example.org/calendar'
    assert rows[0]['version'] == 'fixture-v1'
    assert rows[0]['verified_at'] == '2026-10-28T15:00:00.000000Z'
    assert rows[0]['closes_at'] == '2026-10-23T20:00:00.000000Z'
    assert rows[1]['is_open'] == 0 and rows[1]['closes_at'] is None


@pytest.mark.parametrize('field,value', [
    ('schema_version', True), ('schema_version', '1'), ('source', 'http://example.org'),
    ('source', 'https://user:secret@example.org'), ('source', 'https://example.org/?token=x'),
    ('source', 'https://exa\nmple.org'), ('version', ''), ('verified_at', None),
    ('verified_at', ''), ('verified_at', False), ('verified_at', '2026-10-28T12:00:00'),
    ('verified_at', '2026-10-30T12:00:00Z'), ('start', '20261023'), ('end', '2026-10-20'),
    ('end', '2037-10-29'),
])
def test_reject_invalid_metadata(field, value):
    doc = document(); doc[field] = value
    with pytest.raises((ValueError, TypeError)):
        validate_calendar(doc, NOW)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'numeric_bool', 'naive', 'wrong_day', 'closed_with_time'])
def test_reject_invalid_sessions(change):
    doc = document()
    if change == 'missing': doc['sessions'].pop(1)
    if change == 'duplicate': doc['sessions'][1] = copy.deepcopy(doc['sessions'][0])
    if change == 'numeric_bool': doc['sessions'][0]['is_open'] = 1
    if change == 'naive': doc['sessions'][0]['closes_at'] = '2026-10-23T17:00:00'
    if change == 'wrong_day': doc['sessions'][0]['closes_at'] = '2026-10-24T20:00:00Z'
    if change == 'closed_with_time': doc['sessions'][1]['closes_at'] = '2026-10-24T20:00:00Z'
    with pytest.raises(ValueError): validate_calendar(doc, NOW)


def test_conflict_rolls_back_new_dates(store):
    last = document(); last['start'] = last['end']; last['sessions'] = last['sessions'][-1:]
    with store.connect() as con:
        import_calendar(con, last, NOW)
        changed = document(); changed['version'] = 'conflict-v2'
        with pytest.raises(ValueError, match='conflicting'):
            import_calendar(con, changed, NOW)
        assert con.execute('SELECT count(*) FROM market_sessions').fetchone()[0] == 1


def test_refuse_real_database_without_mutation(tmp_path):
    real = Store(tmp_path / 'isolated-real.sqlite3', profile='real')
    with real.connect() as con:
        before = list(con.iterdump())
        with pytest.raises(ValueError, match='demo_only'): import_calendar(con, document(), NOW)
        assert list(con.iterdump()) == before


def test_refuse_active_worker(store):
    store.acquire_worker('fixture', now=NOW, execution_mode='collection-only')
    with store.connect() as con:
        with pytest.raises(ValueError, match='worker_active'): import_calendar(con, document(), NOW)
        assert con.execute('SELECT count(*) FROM market_sessions').fetchone()[0] == 0


def test_existing_transaction_is_not_committed_or_rolled_back(store):
    with store.connect() as con:
        con.execute('BEGIN')
        con.execute("INSERT INTO settings VALUES('fixture','true',1,?)", (NOW,))
        with pytest.raises(ValueError, match='existing_transaction'): import_calendar(con, document(), NOW)
        assert con.in_transaction
        assert con.execute("SELECT count(*) FROM settings WHERE key='fixture'").fetchone()[0] == 1
        con.rollback()


def test_cli_existing_demo_and_missing_destination(store, tmp_path):
    data = tmp_path / 'calendar.json'; data.write_text(json.dumps(document()), encoding='utf-8')
    script = Path(__file__).resolve().parents[1] / 'scripts/import_calendar.py'
    args = [sys.executable, '-B', str(script), '--db', str(store.path), '--input', str(data)]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['inserted'] == 4
    missing = tmp_path / 'missing.sqlite3'
    args[args.index('--db') + 1] = str(missing)
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 2 and not missing.exists()
