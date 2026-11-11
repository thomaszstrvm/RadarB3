"""Consistent verified SQLite backup/restore; destinations must always be new."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend  # Load verified SQLite DLL before importing sqlite3.
import sqlite3
from backend.backup_integrity import connect_readonly, inspect_database, snapshot

def copy_verified(operation, source, destination):
    if operation not in ('backup', 'restore'):
        raise ValueError('invalid_operation')
    src, dst = Path(source).resolve(), Path(destination).resolve()
    source_paths = {Path(str(src) + suffix) for suffix in ('', '-wal', '-shm', '-journal')}
    if not src.is_file() or dst in source_paths:
        raise ValueError('invalid_source_or_related_destination')
    if dst.exists() or any(Path(str(dst) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise FileExistsError('destination_must_be_new_including_sidecars')
    with closing(connect_readonly(src)) as reader:
        reader.execute('BEGIN')
        before = snapshot(reader)  # Pins a committed snapshot, including WAL.
        if not before['integrity_ok'] or before['foreign_key_violations']:
            raise ValueError('source_integrity_or_foreign_key_failure')
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        deadline = time.monotonic() + 120
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError('backup_timeout')
        with closing(sqlite3.connect(dst, timeout=5)) as writer:
            reader.backup(writer, pages=256, progress=progress, sleep=0.05)
            writer.commit()
            writer.execute('PRAGMA journal_mode=DELETE')
        after = inspect_database(dst)
        # SQLite backup can increment destination schema_version to invalidate caches.
        # Schema SQL and all table contents must nevertheless match exactly.
        if {k:v for k,v in before.items() if k != 'schema_version'} != {k:v for k,v in after.items() if k != 'schema_version'}:
            raise ValueError('destination_verification_failed')
    with dst.open('rb') as stream:
        file_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'format_version': 1, 'status': 'ok', 'operation': operation, 'verified': True,
            'verified_at': datetime.now(timezone.utc).isoformat(), 'snapshot': after,
            'source_schema_version': before['schema_version'],
            'destination_sha256': file_hash, 'schedule_activated': False,
            'retention_or_deletion_performed': False}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'restore'])
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--report', type=Path, help='Optional new JSON receipt; never overwritten')
    args = parser.parse_args(argv)
    report = None
    try:
        if args.report:
            resolved = args.report.resolve()
            if any(resolved == Path(str(path.resolve()) + suffix) for path in (args.source, args.destination) for suffix in ('', '-wal', '-shm', '-journal')):
                raise ValueError('invalid_report_destination')
            report = resolved.open('x', encoding='utf-8')
        result = copy_verified(args.operation, args.source, args.destination)
        if report:
            json.dump(result, report, ensure_ascii=True, sort_keys=True, indent=2)
            report.flush()
            os.fsync(report.fileno())
    except (OSError, ValueError, sqlite3.Error) as error:
        # No exception text: errors can contain paths, SQL or row values.
        print(json.dumps({'format_version': 1, 'status': 'error', 'verified': False,
                          'operation': args.operation, 'error_type': type(error).__name__,
                          'message': 'Copy not verified. Keep source; inspect any new destination before use.',
                          'retention_or_deletion_performed': False}))
        return 1
    finally:
        if report:
            report.close()
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
