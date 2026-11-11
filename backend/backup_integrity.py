"""Typed SQLite fingerprints; never import Store or migrate a database."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def cell(value):
    if value is None:
        return ['null']
    if isinstance(value, bytes):
        return ['blob', len(value), hashlib.sha256(value).hexdigest()]
    if isinstance(value, float):
        return ['real', value.hex()]
    return ['integer' if isinstance(value, int) else 'text', value]

def snapshot(connection):
    """Caller holds a read transaction. Row values never leave this function."""
    integrity_ok = list(connection.execute('PRAGMA integrity_check')) == [('ok',)]
    violations = sum(1 for _ in connection.execute('PRAGMA foreign_key_check'))
    schema = list(connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name,tbl_name'))
    tables = {}
    for name, in connection.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        hashes = []
        blobs = 0
        for row in connection.execute('SELECT * FROM ' + quoted):
            hashes.append(digest([cell(value) for value in row]))
            blobs += sum(isinstance(value, bytes) for value in row)
        tables[name] = {'rows': len(hashes), 'sha256': digest(sorted(hashes)), 'blob_cells': blobs}
    return {'format_version': 1, 'integrity_ok': integrity_ok,
            'foreign_key_violations': violations, 'schema_sha256': digest(schema),
            'schema_version': connection.execute('PRAGMA schema_version').fetchone()[0],
            'user_version': connection.execute('PRAGMA user_version').fetchone()[0],
            'application_id': connection.execute('PRAGMA application_id').fetchone()[0],
            'tables': tables}

def connect_readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    connection.execute('PRAGMA query_only=ON')
    connection.execute('PRAGMA trusted_schema=OFF')
    return connection

def inspect_database(path):
    with closing(connect_readonly(path)) as connection:
        connection.execute('BEGIN')
        return snapshot(connection)

