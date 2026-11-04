"""Prepared operational guards exercised only in memory; never run the plan."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import socket
import subprocess

import pytest
from test_price_vintages import store

QA=Path(__file__).resolve().parents[1]/'docs/qa-20261031/price-vintages-direction'

def load(name):
    sys.path.insert(0,str(QA))
    try:
        spec=importlib.util.spec_from_file_location('vintage_qa_'+name,QA/(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    finally:sys.path.pop(0)

def db():
    con=sqlite3.connect(':memory:')
    con.executescript("CREATE TABLE settings(key,value_json); INSERT INTO settings VALUES('profile','\"demo\"'); CREATE TABLE worker_lease(owner); CREATE TABLE jobs(status); CREATE TABLE audit_events(id INTEGER PRIMARY KEY,event_type,created_at,payload_json); INSERT INTO audit_events VALUES(1,'old','date','preserved');")
    return con

@pytest.mark.parametrize('mutation,reason',[("UPDATE settings SET value_json='\"real\"'",'profile'),("INSERT INTO worker_lease VALUES('live')",'worker'),("INSERT INTO jobs VALUES('running')",'pending')])
def test_import_refuses_profile_worker_or_active_job(mutation,reason):
    module=load('import_reviewed_vintage')
    with db() as con:
        con.execute(mutation)
        with pytest.raises(RuntimeError,match=reason):module.inactive(con,'demo')

def test_audit_append_does_not_hide_changed_old_payload_or_replaced_id():
    module=load('verify_vintage_operation')
    with db() as baseline,db() as current:
        current.execute("INSERT INTO audit_events VALUES(2,'new','later','append')")
        module.preserve_append(current,baseline)
        current.execute("UPDATE audit_events SET payload_json='changed' WHERE id=1")
        with pytest.raises(RuntimeError,match='historical_audit_changed'):module.preserve_append(current,baseline)

def test_hash_check_refuses_changed_reviewed_file(tmp_path,monkeypatch):
    module=load('verify_vintage_operation');monkeypatch.setattr(module,'ROOT',tmp_path)
    (tmp_path/'public.txt').write_text('changed',encoding='utf-8')
    with pytest.raises(RuntimeError,match='reviewed_file_changed'):module.check_hashes({'reviewed_hashes':{'public.txt':'0'*64}})


def test_verifier_import_has_no_network_or_process_side_effects():
    before=(socket.getaddrinfo,socket.socket.connect,socket.socket.connect_ex,subprocess.Popen)
    load('verify_vintage_operation')
    assert (socket.getaddrinfo,socket.socket.connect,socket.socket.connect_ex,subprocess.Popen)==before


def test_copy_guard_is_enabled_before_first_filesystem_action(tmp_path,monkeypatch):
    module=load('check_candidate_copy')
    monkeypatch.setattr(module,'COPY',tmp_path/'not-created.sqlite3')
    # Stop before source bytes, copy, SQLite or network. Check guard activation
    # in the first assertion, then restore globals through pytest's context.
    original=(socket.getaddrinfo,socket.socket.connect,socket.socket.connect_ex,subprocess.Popen)
    class StopProof(Exception):pass
    def first_assertion():
        assert all(item==module.blocked for item in (socket.getaddrinfo,socket.socket.connect,socket.socket.connect_ex,subprocess.Popen))
        raise StopProof
    monkeypatch.setattr(module,'wal_supported',first_assertion)
    try:
        with pytest.raises(StopProof):module.main()
    finally:
        socket.getaddrinfo,socket.socket.connect,socket.socket.connect_ex,subprocess.Popen=original


def test_import_helper_closes_precheck_and_preserves_WAL(store,monkeypatch,capsys):
    module=load('import_reviewed_vintage')
    from backend.core import Store
    from test_price_vintages import FETCHED
    Store(store.path,'demo',wal=True)
    monkeypatch.setattr(sys,'argv',['import_reviewed_vintage.py','--db',str(store.path),'--profile','demo'])
    module.main()
    with store.connect() as con:
        assert con.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
        assert con.execute('SELECT count(*) FROM price_vintages').fetchone()[0]==1
        assert con.execute('SELECT fetched_at FROM price_vintages').fetchone()[0]==FETCHED
    assert '"inserted": true' in capsys.readouterr().out


def test_previous_helper_lock_is_reproduced_before_capture_in_temporary_WAL(store,monkeypatch):
    module=load('import-helper-before-fix')
    from backend.core import Store
    Store(store.path,'demo',wal=True)
    monkeypatch.setattr(sys,'argv',['old_helper','--db',str(store.path),'--profile','demo'])
    with pytest.raises(sqlite3.OperationalError,match='database is locked'):module.main()
    assert not store.rows("SELECT 1 FROM sqlite_master WHERE name='price_vintages'")


def test_verifier_read_context_closes_connection_even_after_failure(tmp_path):
    module=load('verify_vintage_operation');path=tmp_path/'read-only.sqlite3'
    with sqlite3.connect(path) as made:made.execute('CREATE TABLE proof(value)')
    made.close()
    with pytest.raises(RuntimeError):
        with module.read(path) as con:raise RuntimeError('synthetic interruption')
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):con.execute('SELECT 1')
