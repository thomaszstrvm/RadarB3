"""Cumulative B3 calendar, tested with reviewed files and disposable SQLite only."""
import json

import pytest
from jsonschema import Draft202012Validator

from backend import market_calendar, price_vintages
import sqlite3  # The backend preloads the verified SQLite DLL on Windows.
from backend.core import Store, digest, dumps
from backend.evidence import build_bundle, validate_bundle
from backend.ingest import ingest_price
from backend.session_continuity import calendar_context, calendar_status, continuity_reason
from backend.timeutil import instant, iso
from test_price_vintages import ASSET, NOW, PRIOR, ROOT, body, capture, store


DIRECTORY = ROOT / 'docs/qa-20261103/daily/calendar'
SCHEMAS = ROOT / 'schemas'
IMPORTED = '2026-11-04T06:00:00.000000Z'
AFTER = '2026-11-04T06:05:00.000000Z'
OLD = 'b3-cash-20260731-20261103-v1'
NEW = 'b3-cash-20260731-20261231-v2'


def reviewed_document(new):
    path = DIRECTORY / 'calendar-package-v2.json' if new else PRIOR / 'calendar-package-v1.json'
    return json.loads(path.read_text(encoding='utf-8'))


def import_extension(store, monkeypatch):
    monkeypatch.setattr(market_calendar, 'iso', lambda value=None: iso(value or IMPORTED))
    with store.connect() as connection:
        return market_calendar.import_calendar_package(connection, reviewed_document(True), DIRECTORY / 'sources')


@pytest.mark.parametrize('version', ['1.1', '1.2'])
@pytest.mark.parametrize('new', [False, True], ids=['original', 'cumulative'])
def test_schemas_bind_both_ids_hash_and_document_counts(version, new):
    schema = json.loads((SCHEMAS / f'evidence-bundle-v{version}.schema.json').read_text(encoding='utf-8'))
    validator = Draft202012Validator(schema['properties']['calendar_package'])
    document = reviewed_document(new)
    package = {'package_id': document['package_id'], 'document_sha256': digest(document),
               'available_at': IMPORTED, 'document': document}
    other = reviewed_document(not new)
    assert validator.is_valid(package)
    assert not validator.is_valid({**package, 'document_sha256': digest(other)})
    assert not validator.is_valid({**package, 'package_id': other['package_id']})
    assert not validator.is_valid({**package, 'document': {**document, 'package_id': other['package_id']}})
    for field in ('sessions', 'sources'):
        bad = {**document, field: document[field][:-1]}
        assert not validator.is_valid({**package, 'document': bad})


def test_import_preserves_immutable_prefix_and_availability(store, monkeypatch):
    before = store.rows('SELECT * FROM market_sessions ORDER BY session_date')
    original = store.rows('SELECT * FROM calendar_packages')
    first = import_extension(store, monkeypatch)
    assert first['inserted'] == 58 and first['unchanged'] == len(before) == 96
    dates = store.rows('SELECT * FROM market_sessions ORDER BY session_date')
    assert dates[:96] == before and {row['version'] for row in dates[96:]} == {NEW}
    assert store.rows('SELECT * FROM calendar_packages WHERE package_id=?', (OLD,)) == original
    second = import_extension(store, monkeypatch)
    assert (second['inserted'], second['unchanged'], second['available_at']) == (0, 154, first['available_at'])
    assert store.rows("SELECT COUNT(*) AS n FROM audit_events WHERE event_type='calendar_package_imported'")[0]['n'] == 2
    for day in ('2026-11-03', '2026-11-04'):
        with store.connect() as connection, pytest.raises(sqlite3.IntegrityError, match='immutable_record'):
            connection.execute('UPDATE market_sessions SET is_open=0 WHERE session_date=?', (day,))
    assert calendar_context(store, [], '2026-11-04T05:59:59Z')[1]['package_id'] == OLD
    assert calendar_context(store, [], IMPORTED)[1]['package_id'] == NEW
    assert calendar_status(store, '2027-01-01T15:00:00Z')['status'] == 'unavailable'


def test_extension_without_predecessor_does_not_create_a_partial_calendar(tmp_path, monkeypatch):
    empty = Store(tmp_path / 'empty.sqlite3', profile='demo')
    with pytest.raises(ValueError, match='calendar_predecessor_required'):
        import_extension(empty, monkeypatch)
    assert not empty.rows('SELECT session_date FROM market_sessions')
    assert not empty.rows("SELECT name FROM sqlite_master WHERE name='calendar_packages'")


def test_holidays_do_not_mask_an_absent_open_session(store, monkeypatch):
    import_extension(store, monkeypatch)
    as_of = '2026-11-23T23:59:00Z'
    observed = [{'session_date': day} for day in ('2026-11-19', '2026-11-23')]
    series, _ = calendar_context(store, observed, as_of)
    assert continuity_reason(series) is None
    assert series[-1]['calendar']['closed_dates'] == ['2026-11-20', '2026-11-21', '2026-11-22']
    series, _ = calendar_context(store, [{'session_date': '2026-11-18'}, observed[1]], as_of)
    assert continuity_reason(series) == 'missing_market_session'
    assert continuity_reason(calendar_context(store, observed[:1], as_of)[0]) == 'missing_latest_market_session'


@pytest.mark.parametrize('expiry', ['2026-11-06', '2026-11-19', '2026-12-23', '2026-12-30'])
def test_expiry_correction_period_ends_at_twenty_local(store, monkeypatch, expiry):
    import_extension(store, monkeypatch)
    sample = [{'session_date': expiry}]
    assert calendar_context(store, sample, expiry + 'T19:59:59-03:00')[0][0]['calendar'] is None
    after = calendar_context(store, sample, expiry + 'T20:00:01-03:00')[0]
    assert after[0]['calendar']['closes_at'] == expiry + 'T23:00:00.000000Z'
    assert continuity_reason(after) is None


@pytest.mark.parametrize('version', ['1.1', '1.2'])
def test_build_and_replay_before_and_after_import(store, monkeypatch, version):
    if version == '1.2':
        capture(store)
    else:
        ingest_price(store, ASSET['asset_id'], {'session_date': '2026-10-30', 'close': 100,
                     'market_timestamp': '2026-10-30T18:00:00-03:00', 'fetched_at': NOW})
    original = build_bundle(store, ASSET['asset_id'], NOW, SCHEMAS)
    assert original['schema_version'] == version and original['calendar_package']['package_id'] == OLD
    import_extension(store, monkeypatch)
    if version == '1.2':
        # An earlier acquisition retains its original calendar after import.
        assert build_bundle(store, ASSET['asset_id'], AFTER, SCHEMAS)['calendar_package']['package_id'] == OLD
        monkeypatch.setattr(price_vintages, 'iso', lambda value=None: iso(value or AFTER))
        payload = json.loads(body())
        series = payload['results'][0]['historicalDataPrice']
        series.append({**series[-1], 'date': int(instant('2026-11-03T03:00:00Z').timestamp())})
        capture(store, dumps(payload).encode(), fetched='2026-11-04T06:04:59Z')
    fresh = build_bundle(store, ASSET['asset_id'], AFTER, SCHEMAS)
    assert fresh['schema_version'] == version and fresh['calendar_package']['package_id'] == NEW
    assert json.loads(next(item['content'] for item in fresh['evidence']
                           if item['record_type'] == 'price'))['calendar_ref'] == NEW
    validate_bundle(fresh, SCHEMAS)
    validate_bundle(original, SCHEMAS)
    assert build_bundle(store, ASSET['asset_id'], NOW, SCHEMAS) == original
