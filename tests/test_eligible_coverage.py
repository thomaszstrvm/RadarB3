"""Coverage is record presence in one eligible universe, not data quality."""
from contextlib import closing
from pathlib import Path
import socket
import backend  # Load the verified SQLite runtime before importing sqlite3.
import sqlite3
import subprocess

import pytest

from backend.core import Store


@pytest.fixture(autouse=True)
def isolated_only(monkeypatch, tmp_path):
    original = sqlite3.connect

    def temporary_database(database, *args, **kwargs):
        assert Path(database).resolve().is_relative_to(tmp_path.resolve())
        return original(database, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail('Coverage tests cannot use network, subprocesses or inference')

    monkeypatch.setattr(sqlite3, 'connect', temporary_database)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def add_asset(con, asset_id, eligible=1, active=1, asset_type='stock'):
    con.execute("INSERT INTO assets(asset_id,ticker,name,asset_type,eligible,active,provider,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (asset_id, asset_id, 'Synthetic asset', asset_type, eligible, active,
                 'synthetic', '2026-11-04T12:00:00Z'))


def optional_tables(con):
    # Only record identity is needed by this read projection; no ingestion runs.
    for name in ('price_vintages', 'fundamental_releases'):
        con.execute(f'CREATE TABLE {name}(record_id TEXT PRIMARY KEY, asset_id TEXT REFERENCES assets(asset_id))')


def test_absent_optional_storage_is_unknown_and_known_empty_storage_is_zero(tmp_path):
    store = Store(tmp_path / 'coverage.sqlite3', profile='demo')
    initial = store.coverage()
    assert initial['eligible_assets'] == 0
    assert initial['eligible_assets_with_legacy_prices'] == 0
    assert initial['eligible_assets_with_news'] == 0
    assert initial['eligible_assets_with_price_vintages'] is None
    assert initial['eligible_assets_with_fundamentals'] is None
    with store.transaction() as con:
        optional_tables(con)
    present = store.coverage()
    assert present['eligible_assets_with_price_vintages'] == 0
    assert present['eligible_assets_with_fundamentals'] == 0


def test_coverage_deduplicates_and_uses_eligible_denominator_without_merging_prices(tmp_path):
    store = Store(tmp_path / 'coverage.sqlite3', profile='demo')
    with store.transaction() as con:
        optional_tables(con)
        add_asset(con, 'E1')
        add_asset(con, 'E2', active=0)
        add_asset(con, 'F1', eligible=0, asset_type='fund')
        add_asset(con, 'U1', eligible=0, asset_type='unknown')
        for index, asset in enumerate(('E1', 'E1', 'F1')):
            con.execute('INSERT INTO price_vintages VALUES(?,?)', (str(index), asset))
        for index, asset in enumerate(('E2', 'E2', 'F1')):
            con.execute('INSERT INTO fundamental_releases VALUES(?,?)', (str(index), asset))
        for index, asset in enumerate(('E1', 'E1', 'E2', 'F1')):
            con.execute('INSERT INTO price_bars VALUES(?,?,?,?,?,NULL)',
                        (str(index), asset, 'synthetic', f'2026-10-{index+1:02}', 'unknown'))
        for index, asset in enumerate(('E1', 'E1', 'F1')):
            con.execute('INSERT INTO events VALUES(?,?,1)', (str(index), str(index)))
            con.execute('INSERT INTO event_asset_links VALUES(?,?,?,?)',
                        (str(index), asset, 'candidate', 'Synthetic candidate'))
    actual = store.coverage()
    assert actual['eligible_assets'] == 2
    assert actual['unclassified_assets'] == 1
    assert actual['assets_with_prices'] == 3  # Legacy global field stays compatible.
    assert actual['assets_with_news'] == 2
    assert actual['eligible_assets_with_price_vintages'] == 1
    assert actual['eligible_assets_with_legacy_prices'] == 2
    assert actual['eligible_assets_with_news'] == 1
    assert actual['eligible_assets_with_fundamentals'] == 1
    assert not store.rows('SELECT * FROM jobs')
    assert not store.rows('SELECT * FROM quota_ledger')
    assert not store.rows('SELECT * FROM analyses')


def test_numerator_and_denominator_remain_in_same_snapshot_during_catalog_update(tmp_path, monkeypatch):
    store = Store(tmp_path / 'coverage.sqlite3', profile='demo', wal=True)
    with store.transaction() as con:
        add_asset(con, 'E1')
    connect = store.connect
    committed = []

    def traced_connection():
        connection = connect()

        def trace(sql):
            if "asset_type='unknown'" not in sql or committed:
                return
            with closing(connect()) as writer:
                add_asset(writer, 'E2')
                writer.execute("INSERT INTO price_bars VALUES('new','E2','synthetic','2026-11-03','unknown',NULL)")
                writer.commit()
            committed.append(True)

        connection.set_trace_callback(trace)
        return connection

    monkeypatch.setattr(store, 'connect', traced_connection)
    snapshot = store.coverage()
    assert committed == [True]
    assert snapshot['eligible_assets'] == 1
    assert snapshot['eligible_assets_with_legacy_prices'] == 0
    current = store.coverage()
    assert current['eligible_assets'] == 2
    assert current['eligible_assets_with_legacy_prices'] == 1
