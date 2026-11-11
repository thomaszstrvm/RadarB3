"""Recovery invariants using only synthetic isolated databases."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import pytest
from backend.backup_integrity import inspect_database
spec=importlib.util.spec_from_file_location('backup_under_test',Path(__file__).resolve().parents[1]/'scripts/backup.py')
backup=importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)

def database(path):
    c=sqlite3.connect(path)
    c.executescript("""
    PRAGMA foreign_keys=ON;
    PRAGMA user_version=12;
    PRAGMA application_id=123;
    CREATE TABLE analyses(id INTEGER PRIMARY KEY, body BLOB);
    CREATE TABLE relations(id INTEGER PRIMARY KEY, analysis_id INTEGER REFERENCES analyses(id));
    CREATE TABLE quota_ledger(id INTEGER PRIMARY KEY, status TEXT);
    INSERT INTO analyses VALUES(1, x'00ff');
    INSERT INTO relations VALUES(1,1);
    INSERT INTO quota_ledger VALUES(1,'consumed'),(2,'uncertain');
    CREATE INDEX ledger_state ON quota_ledger(status);
    CREATE VIEW summary AS SELECT id FROM analyses;
    """)
    c.commit()
    return c

def test_round_trip_all_data(tmp_path):
    src=tmp_path/'source.db'; database(src).close()
    b=tmp_path/'backup.db'; r=tmp_path/'restored.db'
    report=backup.copy_verified('backup',src,b)
    restored=backup.copy_verified('restore',b,r)
    assert report['verified'] and restored['verified']
    a=inspect_database(src); c=inspect_database(r)
    assert a['tables']==c['tables']
    assert a['schema_sha256']==c['schema_sha256']
    assert c['tables']['analyses']['blob_cells']==1
    assert c['tables']['quota_ledger']['rows']==2
    with sqlite3.connect(r) as conn:
        assert conn.execute('SELECT status FROM quota_ledger ORDER BY id').fetchall()==[('consumed',),('uncertain',)]
        assert conn.execute('SELECT analysis_id FROM relations').fetchall()==[(1,)]
    assert c['user_version']==12 and c['application_id']==123

def test_wal_committed_not_uncommitted(tmp_path):
    src=tmp_path/'wal.db'; c=database(src)
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA wal_autocheckpoint=0')
    c.execute("INSERT INTO quota_ledger VALUES(3,'consumed')"); c.commit()
    assert Path(str(src)+'-wal').stat().st_size>0
    c.execute("INSERT INTO quota_ledger VALUES(4,'uncertain')")
    try:
        result=backup.copy_verified('backup',src,tmp_path/'new.db')
        assert result['snapshot']['tables']['quota_ledger']['rows']==3
    finally:
        c.rollback(); c.close()

@pytest.mark.parametrize('suffix',['','-wal','-shm','-journal'])
def test_refuses_existing_destinations(tmp_path,suffix):
    src=tmp_path/'src.db'; database(src).close()
    dst=tmp_path/'dst.db'; protected=Path(str(dst)+suffix); protected.write_bytes(b'preserve')
    with pytest.raises(FileExistsError):backup.copy_verified('restore',src,dst)
    assert protected.read_bytes()==b'preserve'

def test_same_source_refused(tmp_path):
    src=tmp_path/'src.db'; database(src).close()
    before=src.read_bytes()
    with pytest.raises(ValueError):backup.copy_verified('restore',src,src)
    assert src.read_bytes()==before

def test_foreign_key_corruption_refused(tmp_path):
    src=tmp_path/'src.db'; c=database(src)
    c.execute('PRAGMA foreign_keys=OFF'); c.execute('INSERT INTO relations VALUES(2,999)'); c.commit(); c.close()
    with pytest.raises(ValueError):backup.copy_verified('backup',src,tmp_path/'dst.db')
    assert not (tmp_path/'dst.db').exists()

def test_corrupt_file_no_destination(tmp_path):
    src=tmp_path/'src.db'; src.write_bytes(b'not sqlite confidential example')
    with pytest.raises(sqlite3.DatabaseError):backup.copy_verified('backup',src,tmp_path/'dst.db')
    assert not (tmp_path/'dst.db').exists()

def test_blob_null_and_type_hashes(tmp_path):
    src=tmp_path/'src.db'; c=database(src); a=inspect_database(src)
    c.execute('UPDATE analyses SET body=NULL'); c.commit(); b=inspect_database(src)
    assert a['tables']['analyses']['sha256']!=b['tables']['analyses']['sha256']
    assert a['tables']['quota_ledger']==b['tables']['quota_ledger']
    c.execute("UPDATE analyses SET body='00ff'"); c.commit(); d=inspect_database(src); c.close()
    assert d['tables']['analyses']['sha256'] not in (a['tables']['analyses']['sha256'],b['tables']['analyses']['sha256'])

def test_receipt_and_no_overwrite(tmp_path,capsys):
    src=tmp_path/'src.db'; database(src).close()
    dst=tmp_path/'dst.db'; receipt=tmp_path/'receipt.json'
    assert backup.main(['backup',str(src),str(dst),'--report',str(receipt)])==0
    payload=json.loads(receipt.read_text())
    assert payload['verified'] and payload['verified_at']
    assert '00ff' not in receipt.read_text()
    before=receipt.read_bytes()
    assert backup.main(['backup',str(src),str(tmp_path/'other.db'),'--report',str(receipt)])==1
    assert receipt.read_bytes()==before
    assert not (tmp_path/'other.db').exists()

def test_error_output_no_paths_or_row_contents(tmp_path,capsys):
    src=tmp_path/'private-source.db'; src.write_bytes(b'secret-value')
    assert backup.main(['backup',str(src),str(tmp_path/'private-dest.db')])==1
    text=capsys.readouterr().out
    assert 'secret-value' not in text and str(tmp_path) not in text
    assert json.loads(text)['verified'] is False

def test_verification_failure_retains_new_file(tmp_path,monkeypatch):
    src=tmp_path/'src.db'; database(src).close()
    dst=tmp_path/'dst.db'
    original=backup.inspect_database
    def changed(path):
        result=original(path)
        result['schema_sha256']='mismatch'
        return result
    monkeypatch.setattr(backup,'inspect_database',changed)
    with pytest.raises(ValueError):backup.copy_verified('backup',src,dst)
    assert dst.exists() and src.exists()

@pytest.mark.parametrize('suffix',['','-wal','-shm','-journal'])
def test_report_cannot_be_source_or_sidecar(tmp_path,suffix):
    src=tmp_path/'src.db'; database(src).close()
    before=src.read_bytes()
    assert backup.main(['backup',str(src),str(tmp_path/'dst.db'),'--report',str(src)+suffix])==1
    assert src.read_bytes()==before
    assert not (tmp_path/'dst.db').exists()

def test_missing_source_not_created(tmp_path):
    src=tmp_path/'missing.db'
    with pytest.raises(ValueError):backup.copy_verified('backup',src,tmp_path/'dst.db')
    assert not src.exists() and not (tmp_path/'dst.db').exists()

def test_row_order_independent_and_duplicate_sensitive(tmp_path):
    src=tmp_path/'src.db'; c=database(src)
    c.execute('CREATE TABLE unordered(value)')
    c.executemany('INSERT INTO unordered VALUES(?)',[(1,),('1',),(None,),(b'1',)])
    c.commit(); before=inspect_database(src)['tables']['unordered']
    c.execute('DELETE FROM unordered')
    c.executemany('INSERT INTO unordered VALUES(?)',[(b'1',),(None,),('1',),(1,)])
    c.commit(); reordered=inspect_database(src)['tables']['unordered']
    assert before==reordered
    c.execute('INSERT INTO unordered VALUES(1)');c.commit()
    assert inspect_database(src)['tables']['unordered']['sha256']!=before['sha256']
    c.close()



@pytest.mark.parametrize('suffix',['-wal','-shm','-journal'])
def test_destination_cannot_be_absent_source_sidecar(tmp_path,suffix):
    src=tmp_path/'src.db'; database(src).close()
    dst=Path(str(src)+suffix); assert not dst.exists()
    before=src.read_bytes()
    with pytest.raises(ValueError):backup.copy_verified('backup',src,dst)
    assert src.read_bytes()==before and not dst.exists()
