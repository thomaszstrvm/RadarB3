"""QA verifier only: in-memory databases, no real runtime or operation."""
import importlib.util
from pathlib import Path
import backend  # Keep the approved SQLite preload before importing sqlite3.
import sqlite3
import pytest

QA=Path(__file__).resolve().parents[1]/'docs/qa-20261031/vale3-gates'


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(QA))
    spec=importlib.util.spec_from_file_location('calendar_operation_verifier_qa',QA/'verify_calendar_operation.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.fixture
def databases():
    baseline=sqlite3.connect(':memory:');current=sqlite3.connect(':memory:')
    for con in (baseline,current):
        con.execute('CREATE TABLE audit_events(id INTEGER PRIMARY KEY,event_type TEXT NOT NULL,created_at TEXT NOT NULL,payload_json TEXT NOT NULL)')
        con.execute('INSERT INTO audit_events VALUES(1,?,?,?)',('historical','2026-10-31T12:00:00Z','{"preserved":true}'))
    current.execute('INSERT INTO audit_events VALUES(2,?,?,?)',('calendar_package_imported','2026-11-01T03:00:00Z','{"days":96}'))
    yield current,baseline
    current.close();baseline.close()


def test_single_append_preserves_all_old_fields(verifier,databases):
    current,baseline=databases
    verifier.verify_audit_append(current,baseline)


@pytest.mark.parametrize('field,value',[
    ('event_type','modified'),('created_at','2026-10-30T12:00:00Z'),
    ('payload_json','{"preserved":false}'),('id',0),
])
def test_old_event_change_is_rejected_even_with_correct_count(verifier,databases,field,value):
    current,baseline=databases
    current.execute(f'UPDATE audit_events SET {field}=? WHERE id=1',(value,))
    assert current.execute('SELECT count(*) FROM audit_events').fetchone()[0]==baseline.execute('SELECT count(*) FROM audit_events').fetchone()[0]+1
    assert current.execute("SELECT count(*) FROM audit_events WHERE event_type='calendar_package_imported'").fetchone()[0]==1
    with pytest.raises(RuntimeError,match='historical_audit_changed'):
        verifier.verify_audit_append(current,baseline)
