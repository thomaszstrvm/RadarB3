"""Offline calendar imports; reviewed packages have separate availability clocks."""
from __future__ import annotations

from datetime import date, timedelta
import copy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .timeutil import SAO_PAULO, instant, iso
from .core import digest, dumps

PACKAGE_ID = 'b3-cash-20260731-20261103-v1'
PACKAGE_SHA256 = '91ef3bf00dbf0a799d7b664462231842290680877f1679f5c29f4c4c3b67967e'
REVIEWED_PACKAGES = {
    PACKAGE_ID: PACKAGE_SHA256,
    'b3-cash-20260731-20261231-v2': '1e011b91df51f70c1c590226f6729f9ac86e2833bc28302a082654c232e9778f',
}


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("calendar_invalid_date")
    return date.fromisoformat(value)


def validate_calendar(document, now=None):
    """Return canonical daily rows, requiring explicit open and closed dates."""
    if not isinstance(document, dict) or set(document) != {
        'schema_version', 'source', 'version', 'verified_at', 'start', 'end', 'sessions'
    }:
        raise ValueError('calendar_invalid_document')
    if type(document['schema_version']) is not int or document['schema_version'] != 1:
        raise ValueError('calendar_unsupported_schema')
    source = document['source']
    if not isinstance(source, str) or len(source) > 2000 or source != source.strip() or any(ord(ch) < 33 or ord(ch) == 127 for ch in source):
        raise ValueError('calendar_invalid_source')
    url = urlsplit(source)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('calendar_invalid_source')
    version = document['version']
    if not isinstance(version, str) or not version.strip() or len(version) > 120 or version != version.strip():
        raise ValueError('calendar_invalid_version')
    if not isinstance(document['verified_at'], str) or not document['verified_at']:
        raise ValueError('calendar_invalid_verification')
    verified = iso(document['verified_at'])
    if instant(verified) > instant(now or iso()):
        raise ValueError('calendar_future_verification')
    start, end = _day(document['start']), _day(document['end'])
    count = (end - start).days + 1
    if not 1 <= count <= 3660:
        raise ValueError('calendar_invalid_range')
    sessions = document['sessions']
    if not isinstance(sessions, list) or len(sessions) != count:
        raise ValueError('calendar_incomplete_range')
    rows = {}
    for item in sessions:
        if not isinstance(item, dict) or set(item) != {'session_date', 'is_open', 'closes_at'}:
            raise ValueError('calendar_invalid_session')
        day = _day(item['session_date'])
        if not start <= day <= end or day in rows:
            raise ValueError('calendar_duplicate_or_outside_range')
        if type(item['is_open']) is not bool:
            raise ValueError('calendar_invalid_open_flag')
        closes = item['closes_at']
        if item['is_open']:
            if not isinstance(closes, str) or instant(closes).astimezone(SAO_PAULO).date() != day:
                raise ValueError('calendar_invalid_close')
            closes = iso(closes)
        elif closes is not None:
            raise ValueError('calendar_closed_day_has_close')
        rows[day] = {'session_date': day.isoformat(), 'is_open': int(item['is_open']),
                     'closes_at': closes, 'source': source, 'version': version, 'verified_at': verified}
    return [rows[start + timedelta(days=i)] for i in range(count)]


def import_calendar(connection, document, now=None):
    """Atomically add verified-by-operator rows to an inactive demo database.

    Existing conflicting dates are immutable. A repeated identical import is a
    no-op. The caller owns the connection; no Store initialization or migration.
    """
    rows = validate_calendar(document, now)
    if connection.in_transaction:
        raise ValueError('calendar_existing_transaction')
    connection.execute('BEGIN IMMEDIATE')
    try:
        profile = connection.execute("SELECT value_json FROM settings WHERE key='profile'").fetchone()
        if not profile or json.loads(profile[0]) != 'demo':
            raise ValueError('calendar_demo_only')
        lease = connection.execute('SELECT expires_at FROM worker_lease WHERE singleton=1').fetchone()
        if lease and instant(lease[0]) > instant(now or iso()):
            raise ValueError('calendar_worker_active')
        inserted = unchanged = 0
        for row in rows:
            values = tuple(row[k] for k in ('session_date', 'is_open', 'closes_at', 'source', 'version', 'verified_at'))
            existing = connection.execute('SELECT session_date,is_open,closes_at,source,version,verified_at FROM market_sessions WHERE session_date=?', (row['session_date'],)).fetchone()
            if existing is not None:
                if tuple(existing) != values:
                    raise ValueError('calendar_conflicting_session')
                unchanged += 1
            else:
                connection.execute('INSERT INTO market_sessions(session_date,is_open,closes_at,source,version,verified_at) VALUES(?,?,?,?,?,?)', values)
                inserted += 1
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return {'inserted': inserted, 'unchanged': unchanged, 'source': document['source'], 'version': document['version']}


def package_rows(document, now=None):
    """Validate a reviewed documentary snapshot, including its entire payload."""
    if not isinstance(document, dict) or len(dumps(document)) > 20_000:
        raise ValueError('calendar_package_not_reviewed')
    package_id = document.get('package_id')
    if not isinstance(package_id, str) or digest(document) != REVIEWED_PACKAGES.get(package_id):
        raise ValueError('calendar_package_not_reviewed')
    return validate_calendar({
        'schema_version': 1, 'source': document['sources'][0]['url'], 'version': package_id,
        'verified_at': document['verified_at'], 'start': document['start'], 'end': document['end'],
        'sessions': [{'session_date': day, 'is_open': opened, 'closes_at': closes}
                     for day, opened, closes in document['sessions']]}, now)


def validate_package_envelope(envelope, as_of):
    if not isinstance(envelope, dict) or set(envelope) != {'package_id','document_sha256','available_at','document'}:
        raise ValueError('calendar_package_envelope_invalid')
    document = envelope['document']
    rows = package_rows(document, as_of)
    if envelope['package_id'] != document['package_id'] or envelope['document_sha256'] != REVIEWED_PACKAGES[document['package_id']]:
        raise ValueError('calendar_package_identity_mismatch')
    if not instant(document['verified_at']) <= instant(envelope['available_at']) <= instant(as_of):
        raise ValueError('calendar_package_unavailable_at_cutoff')
    return rows


def stored_package(record, as_of):
    package_id, sha, verified, available, payload = record
    document = json.loads(payload)
    envelope = {'package_id':package_id, 'document_sha256':sha, 'available_at':available, 'document':document}
    rows = validate_package_envelope(envelope, as_of)
    if verified != iso(document['verified_at']):
        raise ValueError('calendar_package_clock_mismatch')
    return envelope, rows


def storage_projection(rows, previous):
    """Retain the original provenance of the already imported cumulative prefix."""
    if len(rows) <= len(previous) or any(
        (a['session_date'], a['is_open'], a['closes_at']) != (b['session_date'], b['is_open'], b['closes_at'])
        for a, b in zip(rows, previous)
    ):
        raise ValueError('calendar_conflicting_session')
    return previous + rows[len(previous):]


def import_calendar_package(connection, document, sources_dir, *, expected_profile='demo'):
    """Import once, atomically, with actual availability and preserved clocks."""
    if not isinstance(expected_profile,str) or expected_profile not in {'demo','real'}:
        raise ValueError('calendar_invalid_profile')
    document = copy.deepcopy(document)
    rows = package_rows(document)
    package_id = document['package_id']
    sha = REVIEWED_PACKAGES[package_id]
    directory = Path(sources_dir).resolve()
    for source in document['sources']:
        path = (directory/source['filename']).resolve()
        if path.parent != directory:
            raise ValueError('calendar_source_path_invalid')
        with path.open('rb') as stream:
            body = stream.read(8_000_001)
        if len(body)>8_000_000 or not body.startswith(b'%PDF-') or hashlib.sha256(body).hexdigest()!=source['sha256']:
            raise ValueError('calendar_source_hash_mismatch')
    if connection.in_transaction:
        raise ValueError('calendar_existing_transaction')
    connection.execute('BEGIN IMMEDIATE')
    try:
        available_at = iso()
        if instant(document['verified_at']) > instant(available_at):
            raise ValueError('calendar_future_verification')
        profile = connection.execute("SELECT value_json FROM settings WHERE key='profile'").fetchone()
        if not profile or json.loads(profile[0]) != expected_profile:
            raise ValueError('calendar_profile_mismatch')
        if connection.execute('SELECT 1 FROM worker_lease').fetchone():
            raise ValueError('calendar_worker_present')
        if connection.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','retry_wait') LIMIT 1").fetchone():
            raise ValueError('calendar_job_active')
        connection.execute('''CREATE TABLE IF NOT EXISTS calendar_packages(
            package_id TEXT PRIMARY KEY, document_sha256 TEXT NOT NULL UNIQUE,
            verified_at TEXT NOT NULL, available_at TEXT NOT NULL, payload_json TEXT NOT NULL)''')
        for action in ('UPDATE','DELETE'):
            connection.execute(f'''CREATE TRIGGER IF NOT EXISTS calendar_packages_no_{action.lower()}
                BEFORE {action} ON calendar_packages BEGIN SELECT RAISE(ABORT,'immutable_record'); END''')
            suffix = '' if package_id == PACKAGE_ID else '_' + sha[:12]
            connection.execute(f'''CREATE TRIGGER IF NOT EXISTS calendar_package_sessions_no_{action.lower()}{suffix}
                BEFORE {action} ON market_sessions WHEN OLD.version='{package_id}'
                BEGIN SELECT RAISE(ABORT,'immutable_record'); END''')
        if package_id != PACKAGE_ID:
            previous = connection.execute('SELECT * FROM calendar_packages WHERE package_id=?',(PACKAGE_ID,)).fetchone()
            if not previous:
                raise ValueError('calendar_predecessor_required')
            _, prefix = stored_package(tuple(previous), available_at)
            rows = storage_projection(rows, prefix)
        existing = connection.execute('SELECT * FROM calendar_packages WHERE package_id=?',(package_id,)).fetchone()
        canonical = dumps(document)
        if existing:
            if tuple(existing)[:3] != (package_id,sha,iso(document['verified_at'])) or existing[4] != canonical:
                raise ValueError('calendar_conflicting_package')
            if not instant(document['verified_at']) <= instant(existing[3]) <= instant(available_at):
                raise ValueError('calendar_availability_invalid')
            available_at = existing[3]
        inserted = unchanged = 0
        for row in rows:
            values = tuple(row[key] for key in ('session_date','is_open','closes_at','source','version','verified_at'))
            current = connection.execute('SELECT session_date,is_open,closes_at,source,version,verified_at FROM market_sessions WHERE session_date=?',(row['session_date'],)).fetchone()
            if current:
                if tuple(current) != values:
                    raise ValueError('calendar_conflicting_session')
                unchanged += 1
            elif existing or row['version'] != package_id:
                raise ValueError('calendar_incomplete_existing_package')
            else:
                connection.execute('INSERT INTO market_sessions VALUES(?,?,?,?,?,?)',values)
                inserted += 1
        if not existing:
            connection.execute('INSERT INTO calendar_packages VALUES(?,?,?,?,?)',
                (package_id,sha,iso(document['verified_at']),available_at,canonical))
            connection.execute('INSERT INTO audit_events(event_type,created_at,payload_json) VALUES(?,?,?)',
                ('calendar_package_imported',available_at,dumps({'package_id':package_id,'document_sha256':sha,'days':len(rows)})))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return {'inserted':inserted,'unchanged':unchanged,
            'package_id':package_id,'document_sha256':sha,'available_at':available_at}
