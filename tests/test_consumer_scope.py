"""Consumer declarations are owned, bounded by dispatch, and never inferred from liveness."""
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
import socket
import sqlite3
import subprocess

import pytest

from backend.core import Conflict, Store
from backend.timeutil import instant, iso
from backend.worker import Worker
from scripts import collect_daily, collect_once


@pytest.fixture(autouse=True)
def isolated_only(monkeypatch, tmp_path):
    connect = sqlite3.connect

    def temporary_database(database, *args, **kwargs):
        assert Path(database).resolve().is_relative_to(tmp_path.resolve())
        return connect(database, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail('Network, subprocesses and inference are forbidden in consumer tests')

    monkeypatch.setattr(sqlite3, 'connect', temporary_database)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr('backend.worker.AnalysisRunner.run', forbidden)
    monkeypatch.setattr('backend.safe_http.SafeHTTP.get', forbidden)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'consumer.sqlite3')


def lease(store):
    return store.rows('SELECT * FROM worker_lease')[0]


def test_legacy_lease_migrates_to_unknown_without_touching_ownership(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    stamp = iso()
    expiry = iso(instant(stamp) + timedelta(seconds=90))
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE worker_lease(singleton INTEGER PRIMARY KEY,owner TEXT NOT NULL,heartbeat_at TEXT NOT NULL,expires_at TEXT NOT NULL)')
        con.execute('INSERT INTO worker_lease VALUES(1,?,?,?)', ('legacy', stamp, expiry))
    migrated = Store(path)
    row = lease(migrated)
    assert row['consumer_scope'] == 'unknown'
    assert row['consumer_scope_owner'] is None
    assert (row['owner'], row['heartbeat_at'], row['expires_at']) == ('legacy', stamp, expiry)
    assert lease(Store(path)) == row
    migrated.heartbeat('legacy')
    assert lease(migrated)['consumer_scope'] == 'unknown'
    assert lease(migrated)['consumer_scope_owner'] is None


def test_direct_acquisition_without_capability_remains_unknown(store):
    store.acquire_worker('library', execution_mode='collection-only')
    store.heartbeat('library')
    assert lease(store)['consumer_scope'] == 'unknown'
    assert lease(store)['consumer_scope_owner'] == 'library'


def test_old_executable_takeover_cannot_inherit_new_owner_capability(store):
    store.acquire_worker('updated-worker', execution_mode='collection-only', consumer_scope='general')
    stamp = iso()
    expiry = iso(instant(stamp) + timedelta(seconds=90))
    # This is the exact pre-migration shape: unknown columns survive the UPSERT.
    with store.transaction() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
        con.execute('''INSERT INTO worker_lease(singleton,owner,heartbeat_at,expires_at,execution_mode)
            VALUES(1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET owner=excluded.owner,
            heartbeat_at=excluded.heartbeat_at,expires_at=excluded.expires_at,execution_mode=excluded.execution_mode''',
            ('old-executable', stamp, expiry, 'collection-only'))
    row = lease(store)
    assert row['consumer_scope'] == 'general'
    assert row['consumer_scope_owner'] == 'updated-worker'
    assert row['owner'] == 'old-executable'
    assert row['consumer_scope_owner'] != row['owner']


@pytest.mark.parametrize('recovery', [[], ['own-job']])
def test_explicit_recovery_scope_cannot_advertise_general(store, recovery):
    store.acquire_worker('bounded', consumer_scope='general', recover_job_ids=recovery)
    assert lease(store)['consumer_scope'] == 'restricted'


def test_invalid_declaration_is_rejected_before_acquisition(store):
    with pytest.raises(ValueError, match='invalid_consumer_scope'):
        store.acquire_worker('invalid', consumer_scope='all')
    assert not store.rows('SELECT * FROM worker_lease')


def test_start_and_one_shot_do_not_advertise_continuous_intake(store):
    worker = Worker(store, execution_mode='collection-only', handlers={'fixture': lambda job: {}})
    job = store.enqueue('fixture')
    try:
        worker.start()
        assert lease(store)['consumer_scope'] == 'restricted'
        assert worker.run_once()
        assert lease(store)['consumer_scope'] == 'restricted'
        assert store.get_job(job['job_id'])['status'] == 'succeeded'
    finally:
        worker.close()


def test_implicit_one_shot_start_does_not_advertise_general(store):
    worker = Worker(store, execution_mode='collection-only')
    try:
        assert not worker.run_once()
        assert lease(store)['consumer_scope'] == 'restricted'
    finally:
        worker.close()


def test_continuous_loop_advertises_owned_general_consumption(store, monkeypatch):
    observed = []
    worker = Worker(store, execution_mode='collection-only')
    monkeypatch.setattr(worker, 'schedule', lambda: None)

    def once(**kwargs):
        observed.append(lease(store))
        assert kwargs == {}
        worker.stop_event.set()
        return False

    monkeypatch.setattr(worker, 'run_once', once)
    worker.run_forever()
    assert len(observed) == 1
    assert observed[0]['consumer_scope'] == 'general'
    assert observed[0]['consumer_scope_owner'] == observed[0]['owner'] == worker.owner
    assert not store.rows('SELECT * FROM worker_lease')


@pytest.mark.parametrize('empty', [False, True])
def test_scoped_pass_after_general_start_revokes_before_handler(store, empty):
    observed = []
    worker = Worker(store, execution_mode='collection-only', handlers={'fixture': lambda job: observed.append(lease(store)) or {}})
    unrelated = store.enqueue('fixture', priority=0)
    own = store.enqueue('fixture', priority=50)
    try:
        worker.start(consumer_scope='general')
        assert lease(store)['consumer_scope'] == 'general'
        assert worker.run_once(job_ids=[] if empty else [own['job_id']]) is not empty
        assert lease(store)['consumer_scope'] == 'restricted'
        assert lease(store)['consumer_scope_owner'] == worker.owner
        assert store.get_job(unrelated['job_id']) == unrelated
        assert all(row['consumer_scope'] == 'restricted' for row in observed)
        store.heartbeat(worker.owner)
        assert lease(store)['consumer_scope'] == 'restricted'
    finally:
        worker.close()


def test_direct_scoped_claim_revokes_declaration_even_with_empty_ids(store):
    store.acquire_worker('general', consumer_scope='general')
    assert store.claim('general', job_ids=[]) is None
    assert lease(store)['consumer_scope'] == 'restricted'


def test_lost_owner_cannot_revoke_successor_capability(store):
    store.acquire_worker('first', consumer_scope='general')
    store.worker_owner = 'first'
    successor = Store(store.path)
    with successor.transaction() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    successor.acquire_worker('next', consumer_scope='general')
    before = lease(successor)
    with pytest.raises(Conflict, match='worker_lease_lost'):
        store.claim('first', job_ids=[])
    assert lease(successor) == before


def test_manual_analysis_never_declares_general(store):
    worker = Worker(store, execution_mode='manual-analysis', reconciliation_mutex_factory=nullcontext)
    try:
        worker.start(consumer_scope='general')
        assert lease(store)['execution_mode'] == 'manual-analysis'
        assert lease(store)['consumer_scope'] == 'restricted'
        assert not worker.run_once(job_ids=[])
    finally:
        worker.close()


@pytest.mark.parametrize('utility', ['once', 'daily'])
def test_utilities_are_restricted_from_start_and_preserve_unrelated_queue(store, monkeypatch, utility):
    scopes = []
    handled = []

    class ObservedWorker(Worker):
        def start(self, **kwargs):
            super().start(**kwargs)
            scopes.append(lease(self.store)['consumer_scope'])

    def handle(job):
        scopes.append(lease(store)['consumer_scope'])
        handled.append(job['job_id'])
        return {}

    def factory(db, **kwargs):
        return ObservedWorker(db, handlers={kind: handle for kind in ('news', 'macro', 'prices', 'catalog')}, **kwargs)

    unrelated = [store.enqueue('news', priority=0), store.enqueue('analyse', priority=0)]
    if utility == 'once':
        invoke = lambda: collect_once.collect_once(store, worker_factory=factory, request_key='scope-fixture')
    else:
        monkeypatch.setattr(collect_daily, 'daily_plan', lambda *args: ({'news': 'daily:news:2026-11-04'}, {}))
        invoke = lambda: collect_daily.collect_daily(store, worker_factory=factory, as_of='2026-11-04T12:00:00Z')
    first = invoke()
    handled_before = list(handled)
    runs_before = store.rows('SELECT * FROM job_runs ORDER BY run_id')
    second = invoke()
    assert scopes and set(scopes) == {'restricted'}
    assert handled == handled_before
    assert store.rows('SELECT * FROM job_runs ORDER BY run_id') == runs_before
    assert [job['job_id'] for job in first['jobs']] == [job['job_id'] for job in second['jobs']]
    assert [store.get_job(job['job_id']) for job in unrelated] == unrelated
    assert not store.rows('SELECT * FROM worker_lease')
    assert not store.rows('SELECT * FROM quota_ledger')
