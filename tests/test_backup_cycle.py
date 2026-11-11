"""Synthetic-only backup cycle tests. pytest tmp_path owns every database."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import patch

import backend
import sqlite3
import pytest

from scripts.backup_cycle import backup_once, cycle_lock, inventory, main


def source(tmp_path):
    path = tmp_path / 'synthetic.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sample (id INTEGER PRIMARY KEY, value BLOB)')
        db.execute('INSERT INTO sample VALUES (1, ?)', (b'example\x00',))
    return path


def test_verified_unique_backups_and_source_preserved(tmp_path):
    src, root = source(tmp_path), tmp_path / 'backups'
    before = src.read_bytes()
    first, second = backup_once(src, root), backup_once(src, root)
    assert first['verified'] is True
    assert first['run_id'] != second['run_id']
    assert src.read_bytes() == before
    assert first['snapshot']['tables']['sample']['blob_cells'] == 1
    plan = inventory(root, keep_latest=1, min_age_days=1,
                     now=datetime.now(timezone.utc) + timedelta(days=2))
    assert sorted(x['decision'] for x in plan['entries']) == ['keep', 'review_retention_only']
    assert all(x['deletion_authorized'] is False for x in plan['entries'])
    assert (root / '.backup-cycle.lock').exists()


def test_lock_rejects_overlap_and_releases_without_deletion(tmp_path):
    src, root = source(tmp_path), tmp_path / 'backups'
    root.mkdir()
    with cycle_lock(root):
        with pytest.raises(OSError):
            backup_once(src, root)
    assert list(root.iterdir()) == [root / '.backup-cycle.lock']
    assert backup_once(src, root)['verified']


def test_wal_committed_snapshot_and_uncommitted_not_copied(tmp_path):
    src, root = source(tmp_path), tmp_path / 'backups'
    db = sqlite3.connect(src)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('INSERT INTO sample VALUES (2, ?)', (b'committed',))
        db.commit()
        db.execute('INSERT INTO sample VALUES (3, ?)', (b'uncommitted',))
        wal = Path(str(src) + '-wal')
        before = (src.read_bytes(), wal.read_bytes())
        receipt = backup_once(src, root)
        assert receipt['snapshot']['tables']['sample']['rows'] == 2
        assert before == (src.read_bytes(), wal.read_bytes())
        assert db.execute('SELECT COUNT(*) FROM sample').fetchone()[0] == 3
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize('keep,age', [(0, 1), (1, 0), (-1, 4), (True, 2), (1, 1.5), (1, 36501)])
def test_policy_rejected(tmp_path, keep, age):
    with pytest.raises(ValueError):
        inventory(tmp_path, keep_latest=keep, min_age_days=age)


@pytest.mark.parametrize('damage', ['bad_json', 'hash', 'path', 'future', 'naive', 'extra', 'shape'])
def test_invalid_receipts_preserved(tmp_path, damage):
    root = tmp_path / 'backups'
    receipt = backup_once(source(tmp_path), root)
    run = root / receipt['run_id']
    path = run / 'receipt.json'
    if damage == 'bad_json':
        path.write_text('{', encoding='utf-8')
    elif damage == 'hash':
        (run / 'database.sqlite3').write_bytes(b'corrupt synthetic output')
    elif damage == 'extra':
        (run / 'database.sqlite3-wal').write_bytes(b'incomplete')
    else:
        if damage == 'path':
            receipt['database_name'] = '../synthetic.sqlite3'
        elif damage == 'future':
            receipt['verified_at'] = '2999-01-01T00:00:00+00:00'
        elif damage == 'naive':
            receipt['started_at'] = '2026-01-01T00:00:00'
        elif damage == 'shape':
            receipt = []
        path.write_text(json.dumps(receipt), encoding='utf-8')
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    plan = inventory(root, keep_latest=1, min_age_days=1)
    assert plan['entries'][0]['decision'] == 'preserve_for_review'
    assert before == {p.name: p.read_bytes() for p in run.iterdir()}


def test_failed_copy_leaves_partial_for_review_and_unlocks(tmp_path):
    root, src = tmp_path / 'backups', source(tmp_path)
    with patch('scripts.backup_cycle.copy_verified', side_effect=ValueError('synthetic_failure')):
        with pytest.raises(ValueError):
            backup_once(src, root)
    assert backup_once(src, root)['verified']
    entries = inventory(root, keep_latest=1, min_age_days=1)['entries']
    assert sorted(x['decision'] for x in entries) == ['keep', 'preserve_for_review']


def test_inventory_does_not_open_paths_from_receipts(tmp_path):
    root = tmp_path / 'backups'
    receipt = backup_once(source(tmp_path), root)
    path = root / receipt['run_id'] / 'receipt.json'
    receipt['destination'] = str(tmp_path / 'not-read.sqlite3')
    path.write_text(json.dumps(receipt), encoding='utf-8')
    with patch('sqlite3.connect', side_effect=AssertionError('inventory must not open SQLite')):
        assert inventory(root, keep_latest=1, min_age_days=1)['entries'][0]['decision'] == 'keep'


def test_no_existing_root_created_by_inventory_and_cli_errors(tmp_path, capsys):
    absent = tmp_path / 'absent'
    assert main(['inventory', '--root', str(absent), '--keep-latest', '1', '--min-age-days', '1']) == 1
    assert not absent.exists()
    assert json.loads(capsys.readouterr().out)['status'] == 'error'


def test_source_inside_backup_root_refused(tmp_path):
    with pytest.raises(ValueError):
        backup_once(source(tmp_path), tmp_path)


def test_retention_age_boundary_and_minimum_count(tmp_path):
    root, src = tmp_path / 'backups', source(tmp_path)
    oldest = backup_once(src, root)
    backup_once(src, root)
    boundary = datetime.fromisoformat(oldest['started_at']) + timedelta(days=30)
    before = inventory(root, keep_latest=1, min_age_days=30, now=boundary - timedelta(microseconds=1))
    assert all(item['decision'] == 'keep' for item in before['entries'])
    at = inventory(root, keep_latest=1, min_age_days=30, now=boundary)
    assert sum(item['decision'] == 'review_retention_only' for item in at['entries']) == 1
    assert all(item['decision'] == 'keep' for item in
               inventory(root, keep_latest=2, min_age_days=30, now=boundary)['entries'])


def test_collision_never_overwrites(tmp_path):
    root, src = tmp_path / 'backups', source(tmp_path)
    from uuid import UUID
    instant = datetime.now(timezone.utc)
    with patch('scripts.backup_cycle.utc', return_value=instant), patch(
            'scripts.backup_cycle.uuid4', return_value=UUID(int=1)):
        receipt = backup_once(src, root)
        path = root / receipt['run_id'] / 'receipt.json'
        before = path.read_bytes()
        with pytest.raises(FileExistsError):
            backup_once(src, root)
        assert path.read_bytes() == before


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm', '-journal'])
@pytest.mark.parametrize('link_kind', ['symlink', 'reparse_point'])
def test_source_and_sidecar_links_refused_before_copy(tmp_path, suffix, link_kind):
    src, root = source(tmp_path), tmp_path / 'backups'
    suspect = Path(str(src) + suffix)
    before = src.read_bytes()
    original_lstat = Path.lstat

    def synthetic_lstat(path, *args, **kwargs):
        if path == suspect:
            # No real link or permission changes; simulate even a dangling link.
            return SimpleNamespace(
                st_mode=stat.S_IFLNK if link_kind == 'symlink' else stat.S_IFREG,
                st_file_attributes=0x400 if link_kind == 'reparse_point' else 0)
        return original_lstat(path, *args, **kwargs)

    with patch.object(Path, 'lstat', synthetic_lstat), patch(
            'scripts.backup_cycle.copy_verified') as copy, patch(
            'sqlite3.connect', side_effect=AssertionError('must refuse before SQLite')):
        with pytest.raises(ValueError, match='linked_path_not_supported'):
            backup_once(src, root)
        copy.assert_not_called()
        assert not root.exists()
    assert src.read_bytes() == before
