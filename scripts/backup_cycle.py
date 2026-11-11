"""One verified backup; retention inventory is always a read-only simulation."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.backup import copy_verified
import sqlite3

RUN_PATTERN = re.compile(r'backup-\d{8}T\d{12}Z-[0-9a-f]{32}')


def utc(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone_required')
    return value.astimezone(timezone.utc)


def safe_path(path):
    """Reject links/junctions, including ancestors, before touching content."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('linked_path_not_supported')
    return path


@contextmanager
def cycle_lock(root):
    """OS handle lock, automatically released on crash; file is never deleted."""
    path = safe_path(root / '.backup-cycle.lock')
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError('lock_must_be_regular_single_link_file')
    with path.open('a+b') as handle:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def backup_once(source, root):
    source, root = safe_path(source), safe_path(root)
    # SQLite can open these independently of the main file. Check even dangling
    # links before allocating output or delegating to the SQLite backup API.
    for suffix in ('-wal', '-shm', '-journal'):
        safe_path(Path(str(source) + suffix))
    if not source.is_file() or source.is_relative_to(root):
        raise ValueError('source_must_exist_outside_backup_root')
    root.mkdir(parents=True, exist_ok=True)
    with cycle_lock(root):
        started = utc()
        run_id = 'backup-' + started.strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex
        run = root / run_id
        run.mkdir()  # Atomic exclusive allocation; no overwrite or retry cleanup.
        receipt = copy_verified('backup', source, run / 'database.sqlite3')
        receipt.update(cycle_version=1, run_id=run_id, started_at=started.isoformat(),
                       database_name='database.sqlite3')
        with (run / 'receipt.json').open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, sort_keys=True, ensure_ascii=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        # Publication is successful only if the persisted receipt is readable.
        if json.loads((run / 'receipt.json').read_text(encoding='utf-8')) != receipt:
            raise ValueError('receipt_readback_failed')
        return receipt


def inventory(root, *, keep_latest, min_age_days, now=None):
    """Hash-only recheck; never SQLite-open inventory files or follow receipt paths."""
    if type(keep_latest) is not int or not 1 <= keep_latest <= 100000:
        raise ValueError('keep_latest_must_be_1_to_100000')
    if type(min_age_days) is not int or not 1 <= min_age_days <= 36500:
        raise ValueError('min_age_days_must_be_1_to_36500')
    now, root = utc(now), safe_path(root)
    if not root.is_dir():
        raise ValueError('backup_root_must_exist')
    entries, valid = [], []
    for run in sorted(root.iterdir()):
        if run.name == '.backup-cycle.lock':
            continue
        item = {'run_id': run.name, 'state': 'unverified', 'decision': 'preserve_for_review',
                'deletion_authorized': False}
        entries.append(item)
        try:
            safe_path(run)
            if not RUN_PATTERN.fullmatch(run.name) or not run.is_dir():
                raise ValueError('unrecognized_entry')
            receipt_path = safe_path(run / 'receipt.json')
            database = safe_path(run / 'database.sqlite3')
            if {p.name for p in run.iterdir()} != {'receipt.json', 'database.sqlite3'}:
                raise ValueError('unexpected_files_or_sidecars')
            if (not receipt_path.is_file() or receipt_path.stat().st_size > 4 * 1024 * 1024
                    or receipt_path.stat().st_nlink != 1):
                raise ValueError('receipt_invalid')
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            if not isinstance(receipt, dict):
                raise ValueError('receipt_invalid')
            if (receipt.get('cycle_version') != 1 or receipt.get('format_version') != 1
                    or receipt.get('run_id') != run.name or receipt.get('database_name') != 'database.sqlite3'
                    or receipt.get('status') != 'ok' or receipt.get('verified') is not True
                    or receipt.get('operation') != 'backup'):
                raise ValueError('receipt_invalid')
            started = utc(datetime.fromisoformat(receipt['started_at']))
            verified = utc(datetime.fromisoformat(receipt['verified_at']))
            if started > verified or verified > now or run.name[7:29] != started.strftime('%Y%m%dT%H%M%S%fZ'):
                raise ValueError('receipt_clock_invalid')
            snap = receipt.get('snapshot')
            if not isinstance(snap, dict) or snap.get('integrity_ok') is not True or snap.get('foreign_key_violations') != 0:
                raise ValueError('receipt_integrity_invalid')
            if not database.is_file() or database.stat().st_nlink != 1:
                raise ValueError('database_invalid')
            with database.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != receipt.get('destination_sha256'):
                raise ValueError('hash_mismatch')
            age = (now - started).total_seconds() / 86400
            item.update(state='receipt_and_hash_match', age_days=age, decision='keep')
            valid.append((started, run.name, item))
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            # Unknown/corrupt/partial entries never become retention candidates.
            continue
    for index, (_, _, item) in enumerate(sorted(valid, key=lambda row: row[:2], reverse=True)):
        if index >= keep_latest and item['age_days'] >= min_age_days:
            item['decision'] = 'review_retention_only'
    return {'format_version': 1, 'simulation_only': True, 'as_of': now.isoformat(),
            'policy': {'keep_latest': keep_latest, 'min_age_days': min_age_days,
                       'age_unit': 'elapsed_24_hour_days_UTC', 'rpo_rto_agreed': False},
            'retention_or_deletion_performed': False, 'entries': entries}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    once = sub.add_parser('once')
    once.add_argument('--source', type=Path, required=True)
    once.add_argument('--root', type=Path, required=True)
    plan = sub.add_parser('inventory')
    plan.add_argument('--root', type=Path, required=True)
    plan.add_argument('--keep-latest', type=int, required=True)
    plan.add_argument('--min-age-days', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        result = (backup_once(args.source, args.root) if args.command == 'once' else
                  inventory(args.root, keep_latest=args.keep_latest, min_age_days=args.min_age_days))
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({'status': 'error', 'error_type': type(error).__name__,
                          'message': 'Keep all artifacts. Verify paths, lock and partial output before retrying.',
                          'schedule_activated': False, 'retention_or_deletion_performed': False}))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
