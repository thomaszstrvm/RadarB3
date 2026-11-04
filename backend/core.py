from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .timeutil import UTC, instant, iso


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256((value if isinstance(value, str) else dumps(value)).encode()).hexdigest()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class Conflict(RuntimeError):
    pass


class QuotaExceeded(RuntimeError):
    pass


def wal_supported(version: tuple[int, ...] | None = None) -> bool:
    v = version or sqlite3.sqlite_version_info
    return v >= (3, 51, 3) or (3, 50, 7) <= v < (3, 51, 0) or (3, 44, 6) <= v < (3, 45, 0)


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value_json TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assets(asset_id TEXT PRIMARY KEY,ticker TEXT NOT NULL UNIQUE,name TEXT NOT NULL,asset_type TEXT NOT NULL,eligible INTEGER NOT NULL DEFAULT 0,sector TEXT,currency TEXT NOT NULL DEFAULT 'BRL',provider TEXT NOT NULL,provider_id TEXT,classification_reason TEXT,active INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS asset_aliases(asset_id TEXT NOT NULL REFERENCES assets(asset_id),alias TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(asset_id,alias));
CREATE TABLE IF NOT EXISTS universe_snapshots(snapshot_id TEXT PRIMARY KEY,provider TEXT NOT NULL,as_of TEXT NOT NULL,total INTEGER NOT NULL,eligible INTEGER NOT NULL,unclassified INTEGER NOT NULL,complete INTEGER NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS articles(article_id TEXT PRIMARY KEY,canonical_url TEXT NOT NULL UNIQUE,source_id TEXT NOT NULL,first_seen_at TEXT NOT NULL,latest_revision_id TEXT,cluster_key TEXT);
CREATE TABLE IF NOT EXISTS article_revisions(revision_id TEXT PRIMARY KEY,article_id TEXT NOT NULL REFERENCES articles(article_id),content_hash TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,access_level TEXT NOT NULL,published_at TEXT,modified_at TEXT,fetched_at TEXT NOT NULL,available_at TEXT NOT NULL,timestamp_precision TEXT NOT NULL,publisher_group TEXT,original_publisher TEXT,quality_flags_json TEXT NOT NULL,UNIQUE(article_id,content_hash));
CREATE TABLE IF NOT EXISTS article_discoveries(article_id TEXT NOT NULL REFERENCES articles(article_id),method TEXT NOT NULL,seen_at TEXT NOT NULL,PRIMARY KEY(article_id,method));
CREATE TABLE IF NOT EXISTS price_bars(bar_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(asset_id),provider TEXT NOT NULL,session_date TEXT NOT NULL,adjustment TEXT NOT NULL,latest_revision_id TEXT,UNIQUE(asset_id,provider,session_date,adjustment));
CREATE TABLE IF NOT EXISTS price_revisions(revision_id TEXT PRIMARY KEY,bar_id TEXT NOT NULL REFERENCES price_bars(bar_id),content_hash TEXT NOT NULL,close REAL,open REAL,high REAL,low REAL,volume REAL,currency TEXT NOT NULL,market_timestamp TEXT,fetched_at TEXT NOT NULL,available_at TEXT NOT NULL,complete_session INTEGER NOT NULL DEFAULT 0,quality_flags_json TEXT NOT NULL,UNIQUE(bar_id,content_hash));
CREATE TABLE IF NOT EXISTS market_sessions(session_date TEXT PRIMARY KEY,is_open INTEGER NOT NULL,closes_at TEXT,source TEXT NOT NULL,version TEXT NOT NULL,verified_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS macro_observations(observation_id TEXT PRIMARY KEY,series_code INTEGER NOT NULL,observation_date TEXT NOT NULL,value REAL,unit TEXT NOT NULL,fetched_at TEXT NOT NULL,content_hash TEXT NOT NULL,UNIQUE(series_code,observation_date,content_hash));
CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,cluster_key TEXT NOT NULL UNIQUE,latest_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS event_versions(event_id TEXT NOT NULL REFERENCES events(event_id),version INTEGER NOT NULL,payload_json TEXT NOT NULL,content_hash TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(event_id,version),UNIQUE(event_id,content_hash));
CREATE TABLE IF NOT EXISTS event_asset_links(event_id TEXT NOT NULL REFERENCES events(event_id),asset_id TEXT NOT NULL REFERENCES assets(asset_id),link_type TEXT NOT NULL,mechanism TEXT NOT NULL,PRIMARY KEY(event_id,asset_id));
CREATE TABLE IF NOT EXISTS evidence_bundles(bundle_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(asset_id),as_of TEXT NOT NULL,content_hash TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analyses(analysis_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(asset_id),bundle_id TEXT NOT NULL REFERENCES evidence_bundles(bundle_id),as_of TEXT NOT NULL,model TEXT NOT NULL,prompt_hash TEXT NOT NULL,schema_hash TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(bundle_id,model,prompt_hash,schema_hash));
CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload_json TEXT NOT NULL,dedupe_key TEXT NOT NULL UNIQUE,status TEXT NOT NULL,priority INTEGER NOT NULL DEFAULT 50,attempts INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 3,not_before TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,owner TEXT,lease_until TEXT,heartbeat_at TEXT,cancel_requested INTEGER NOT NULL DEFAULT 0,progress REAL NOT NULL DEFAULT 0,checkpoint_json TEXT NOT NULL DEFAULT '{}',result_json TEXT,error_code TEXT);
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status,not_before,priority,created_at);
CREATE TABLE IF NOT EXISTS job_runs(run_id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(job_id),owner TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,status TEXT NOT NULL,error_code TEXT);
CREATE TABLE IF NOT EXISTS worker_lease(singleton INTEGER PRIMARY KEY CHECK(singleton=1),owner TEXT NOT NULL,heartbeat_at TEXT NOT NULL,expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quota_ledger(reservation_id TEXT PRIMARY KEY,provider TEXT NOT NULL,period TEXT NOT NULL,units INTEGER NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(provider,period,reservation_id));
CREATE TABLE IF NOT EXISTS http_backoff(host TEXT PRIMARY KEY,next_attempt_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS provider_state(provider_id TEXT PRIMARY KEY,status TEXT NOT NULL,last_attempt_at TEXT,last_success_at TEXT,error_code TEXT,next_attempt_at TEXT,details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS http_cache(url_hash TEXT PRIMARY KEY,etag TEXT,last_modified TEXT,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS host_requests(host TEXT PRIMARY KEY,last_requested_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS coverage_partitions(partition_id TEXT PRIMARY KEY,provider TEXT NOT NULL,source_id TEXT,start_at TEXT NOT NULL,end_at TEXT NOT NULL,status TEXT NOT NULL,count INTEGER NOT NULL DEFAULT 0,truncated INTEGER NOT NULL DEFAULT 0,parent_id TEXT,error_code TEXT,query_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watchlist(asset_id TEXT PRIMARY KEY REFERENCES assets(asset_id),created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(alert_id TEXT PRIMARY KEY,dedupe_key TEXT NOT NULL UNIQUE,asset_id TEXT REFERENCES assets(asset_id),kind TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,is_read INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS alert_rules(rule_id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,config_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY,event_type TEXT NOT NULL,created_at TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quarantine(id INTEGER PRIMARY KEY,record_type TEXT NOT NULL,reason TEXT NOT NULL,record_hash TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mutation_receipts(key TEXT PRIMARY KEY,method TEXT NOT NULL,path TEXT NOT NULL,request_hash TEXT NOT NULL,status_code INTEGER NOT NULL,response_json TEXT NOT NULL,completed_at TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: str | Path, profile: str = "real", wal: bool = False):
        if profile not in {"real", "demo"}:
            raise ValueError("invalid_profile")
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.profile = profile
        # API stores remain unfenced. A worker opts its own Store instance into
        # lease fencing after acquisition; every pipeline transaction then checks
        # ownership before writing and again immediately before commit.
        self.worker_owner = None
        if wal and not wal_supported():
            raise RuntimeError("sqlite_wal_fix_not_verified")
        with self.connect() as con:
            con.execute(f"PRAGMA journal_mode={'WAL' if wal else 'DELETE'}")
            con.executescript(SCHEMA)
            con.execute('BEGIN IMMEDIATE')
            lease_columns = {r[1] for r in con.execute('PRAGMA table_info(worker_lease)')}
            if 'execution_mode' not in lease_columns:
                con.execute("ALTER TABLE worker_lease ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'analysis-enabled'")
            if 'consumer_scope' not in lease_columns:
                con.execute("ALTER TABLE worker_lease ADD COLUMN consumer_scope TEXT NOT NULL DEFAULT 'unknown'")
            if 'consumer_scope_owner' not in lease_columns:
                con.execute("ALTER TABLE worker_lease ADD COLUMN consumer_scope_owner TEXT")
            con.execute("INSERT OR IGNORE INTO schema_migrations VALUES(1,?)", (iso(),))
            con.execute("INSERT OR IGNORE INTO schema_migrations VALUES(2,?)", (iso(),))
            con.execute("INSERT OR IGNORE INTO schema_migrations VALUES(3,?)", (iso(),))
            row = con.execute("SELECT value_json FROM settings WHERE key='profile'").fetchone()
            if row and json.loads(row[0]) != profile:
                raise Conflict("profile_database_mismatch")
            con.execute("INSERT OR IGNORE INTO settings(key,value_json,updated_at) VALUES('profile',?,?)", (dumps(profile), iso()))
            for table in ("article_revisions", "price_revisions", "event_versions", "evidence_bundles", "analyses"):
                con.executescript(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'immutable_record'); END; CREATE TRIGGER IF NOT EXISTS immutable_{table}_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'immutable_record'); END;")

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    @contextmanager
    def transaction(self):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            self._check_worker_fence(con)
            yield con
            self._check_worker_fence(con)
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _check_worker_fence(self, con):
        if self.worker_owner is not None:
            if not con.execute("SELECT 1 FROM worker_lease WHERE singleton=1 AND owner=? AND expires_at>?", (self.worker_owner, iso())).fetchone():
                raise Conflict("worker_lease_lost")

    def rows(self, query: str, args=()) -> list[dict]:
        with self.connect() as con:
            return [dict(r) for r in con.execute(query, args)]

    def setting(self, key: str, default=None):
        rows = self.rows("SELECT value_json FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value_json"]) if rows else default

    def set_setting(self, key: str, value):
        if key == "profile":
            raise Conflict("profile_is_immutable")
        with self.transaction() as con:
            con.execute("INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,version=version+1,updated_at=excluded.updated_at", (key, dumps(value), iso()))

    def enqueue(self, kind: str, payload: dict | None = None, dedupe_key: str | None = None, priority=50, max_attempts=3, not_before=None, *, connection=None) -> dict:
        now = iso()
        job_id = uid("job")
        # An API command can share its transaction with the durable receipt.
        with nullcontext(connection) if connection is not None else self.transaction() as con:
            con.execute("INSERT OR IGNORE INTO jobs(job_id,kind,payload_json,dedupe_key,status,priority,max_attempts,not_before,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (job_id, kind, dumps(payload or {}), dedupe_key or job_id, "queued", priority, max_attempts, iso(not_before) if not_before else now, now, now))
            row = con.execute("SELECT * FROM jobs WHERE dedupe_key=?", (dedupe_key or job_id,)).fetchone()
        return self.decode_job(dict(row))

    @staticmethod
    def decode_job(row):
        for col in ("payload_json", "checkpoint_json", "result_json"):
            row[col.removesuffix("_json")] = json.loads(row.pop(col)) if row.get(col) else None
        return row

    def list_jobs(self, limit=100):
        return [self.decode_job(r) for r in self.rows("SELECT * FROM jobs ORDER BY created_at DESC,job_id LIMIT ?", (min(limit, 500),))]

    def get_job(self, job_id):
        rows = self.rows("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        return self.decode_job(rows[0]) if rows else None

    def acquire_worker(self, owner: str, ttl=90, now=None, execution_mode='analysis-enabled', *, recover_job_ids=None, analysis_mutex_held=False, consumer_scope='unknown'):
        from .execution_mode import resolve_mode
        execution_mode = resolve_mode(execution_mode)
        if consumer_scope not in {'unknown', 'restricted', 'general'}:
            raise ValueError('invalid_consumer_scope')
        if consumer_scope == 'general' and (execution_mode == 'manual-analysis' or recover_job_ids is not None):
            consumer_scope = 'restricted'
        now = instant(now or iso())
        with self.transaction() as con:
            row = con.execute("SELECT * FROM worker_lease WHERE singleton=1").fetchone()
            if row and row["owner"] != owner and instant(row["expires_at"]) > now:
                raise Conflict("another_worker_is_active")
            if row and row['owner'] != owner and row['execution_mode'] == 'manual-analysis' and not analysis_mutex_held:
                raise Conflict('manual_takeover_guard_required')
            # Bind the capability to its issuer: an older executable's UPSERT
            # does not know these columns and must not inherit general intake.
            con.execute("INSERT INTO worker_lease(singleton,owner,heartbeat_at,expires_at,execution_mode,consumer_scope,consumer_scope_owner) VALUES(1,?,?,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET owner=excluded.owner,heartbeat_at=excluded.heartbeat_at,expires_at=excluded.expires_at,execution_mode=excluded.execution_mode,consumer_scope=excluded.consumer_scope,consumer_scope_owner=excluded.consumer_scope_owner", (owner, iso(now), iso(now + timedelta(seconds=ttl)), execution_mode, consumer_scope, owner))
            # None recovers the normal queue. An explicit scope, including empty,
            # lets a bounded utility leave unrelated abandoned jobs untouched.
            # Manual jobs are never recovered into the general queue. Their
            # owner-scoped reconciliation requires the analysis mutex as well.
            recovery_sql = " AND json_extract(payload_json,'$.manual_owner') IS NULL"
            recovery_args = ()
            if recover_job_ids is not None:
                recovery_args = tuple(recover_job_ids)
                recovery_sql += ' AND job_id IN (' + ','.join('?' for _ in recovery_args) + ')' if recovery_args else ' AND 0'
            stale = con.execute("SELECT job_id,cancel_requested,attempts,max_attempts FROM jobs WHERE status='running' AND (owner<>? OR lease_until<=?)" + recovery_sql, (owner, iso(now), *recovery_args)).fetchall()
            for job in stale:
                state = "cancelled" if job["cancel_requested"] else ("failed" if job["attempts"] >= job["max_attempts"] else "queued")
                con.execute("UPDATE jobs SET status=?,owner=NULL,lease_until=NULL,updated_at=?,error_code='worker_interrupted' WHERE job_id=?", (state, iso(now), job["job_id"]))
                con.execute("UPDATE job_runs SET status='interrupted',finished_at=?,error_code='worker_interrupted' WHERE job_id=? AND finished_at IS NULL", (iso(now), job["job_id"]))

    def heartbeat(self, owner: str, job_id=None, ttl=90):
        now = iso()
        expiry = iso(datetime.now(UTC) + timedelta(seconds=ttl))
        with self.transaction() as con:
            updated = con.execute("UPDATE worker_lease SET heartbeat_at=?,expires_at=? WHERE singleton=1 AND owner=? AND expires_at>?", (now, expiry, owner, now)).rowcount
            if not updated:
                raise Conflict("worker_lease_lost")
            if job_id:
                con.execute("UPDATE jobs SET heartbeat_at=?,lease_until=? WHERE job_id=? AND owner=? AND status='running'", (now, expiry, job_id, owner))

    def release_worker(self, owner):
        with self.transaction() as con:
            con.execute("DELETE FROM worker_lease WHERE owner=?", (owner,))

    def claim(self, owner: str, ttl=90, *, job_ids=None):
        now = iso()
        with self.transaction() as con:
            lease = con.execute("SELECT * FROM worker_lease WHERE singleton=1 AND owner=? AND expires_at>?", (owner, now)).fetchone()
            if not lease:
                raise Conflict("worker_lease_required")
            scope_sql = ''
            scope_args = ()
            if job_ids is not None:
                # A bounded claim cannot promise to drain newly admitted jobs.
                # Revoke even an earlier general declaration before selection,
                # including an empty scope, under the same admission write lock.
                con.execute("UPDATE worker_lease SET consumer_scope='restricted',consumer_scope_owner=? WHERE singleton=1 AND owner=?", (owner, owner))
                scope_args = tuple(job_ids)
                if not scope_args:
                    return None
                scope_sql = ' AND job_id IN (' + ','.join('?' for _ in scope_args) + ')'
            if lease['execution_mode'] == 'manual-analysis':
                if job_ids is None:
                    raise Conflict('manual_job_scope_required')
                eligible = "kind='analyse' AND status='queued' AND json_extract(payload_json,'$.manual_owner')=?"
                policy_args = (owner,)
            else:
                eligible = "json_extract(payload_json,'$.manual_owner') IS NULL AND (?='analysis-enabled' OR kind NOT IN ('analyse','analysis'))"
                policy_args = (lease['execution_mode'],)
            row = con.execute("SELECT * FROM jobs WHERE status IN ('queued','retry_wait') AND not_before<=? AND cancel_requested=0 AND " + eligible + scope_sql + " ORDER BY priority,created_at,job_id LIMIT 1", (now, *policy_args, *scope_args)).fetchone()
            if not row:
                return None
            con.execute("UPDATE jobs SET status='running',attempts=attempts+1,owner=?,lease_until=?,heartbeat_at=?,updated_at=? WHERE job_id=?", (owner, iso(datetime.now(UTC) + timedelta(seconds=ttl)), now, now, row["job_id"]))
            con.execute("INSERT INTO job_runs VALUES(?,?,?,?,NULL,'running',NULL)", (uid("run"), row["job_id"], owner, now))
            result = dict(con.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone())
        return self.decode_job(result)

    def checkpoint(self, job_id, owner, checkpoint, progress):
        with self.transaction() as con:
            if not con.execute("UPDATE jobs SET checkpoint_json=?,progress=?,updated_at=? WHERE job_id=? AND owner=? AND status='running'", (dumps(checkpoint), max(0, min(1, progress)), iso(), job_id, owner)).rowcount:
                raise Conflict("job_not_owned")

    def finish(self, job_id, owner, status="succeeded", result=None, error_code=None, retry_after=None):
        if status not in {"succeeded", "partial", "failed", "cancelled", "blocked", "retry_wait", "paused"}:
            raise ValueError("invalid_job_status")
        with self.transaction() as con:
            row = con.execute("SELECT * FROM jobs WHERE job_id=? AND owner=? AND status='running'", (job_id, owner)).fetchone()
            if not row:
                raise Conflict("job_not_owned")
            if row["cancel_requested"]:
                status = "cancelled"
            if status == "retry_wait" and row["attempts"] >= row["max_attempts"]:
                status = "failed"
            delay = retry_after if retry_after is not None else min(3600, 2 ** row["attempts"] * 10 + random.uniform(0, 5))
            con.execute("UPDATE jobs SET status=?,result_json=?,error_code=?,owner=NULL,lease_until=NULL,updated_at=?,not_before=?,progress=CASE WHEN ?='succeeded' THEN 1 ELSE progress END WHERE job_id=?", (status, dumps(result) if result is not None else None, error_code, iso(), iso(datetime.now(UTC) + timedelta(seconds=delay)), status, job_id))
            con.execute("UPDATE job_runs SET status=?,finished_at=?,error_code=? WHERE job_id=? AND owner=? AND finished_at IS NULL", (status, iso(), error_code, job_id, owner))

    def cancel_job(self, job_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self.transaction() as con:
            row = con.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row[0] not in {"queued", "running", "retry_wait", "paused", "blocked"}:
                raise Conflict("job_already_finished")
            con.execute("UPDATE jobs SET cancel_requested=1,status=CASE WHEN status='running' THEN status ELSE 'cancelled' END,updated_at=? WHERE job_id=?", (iso(), job_id))
            result = dict(con.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone())
        return self.decode_job(result)

    def retry_job(self, job_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self.transaction() as con:
            if not con.execute("UPDATE jobs SET status='queued',cancel_requested=0,attempts=0,not_before=?,updated_at=?,error_code=NULL WHERE job_id=? AND status IN ('failed','blocked','paused','cancelled','partial')", (iso(), iso(), job_id)).rowcount:
                raise Conflict("job_cannot_retry")
            result = dict(con.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone())
        return self.decode_job(result)

    def reserve_quota(self, provider, period, limit, reservation_id=None, units=1):
        if units <= 0:
            raise ValueError("positive_quota_units_required")
        reservation_id = reservation_id or uid("request")
        with self.transaction() as con:
            existing = con.execute("SELECT * FROM quota_ledger WHERE reservation_id=?", (reservation_id,)).fetchone()
            if existing:
                if existing["provider"] != provider or existing["period"] != period or existing["units"] != units:
                    raise Conflict("reservation_identity_mismatch")
                return reservation_id
            consumed = con.execute("SELECT COALESCE(SUM(units),0) FROM quota_ledger WHERE provider=? AND period=? AND status<>'released'", (provider, period)).fetchone()[0]
            if consumed + units > limit:
                raise QuotaExceeded("local_request_budget_exhausted")
            con.execute("INSERT INTO quota_ledger VALUES(?,?,?,?,?,?)", (reservation_id, provider, period, units, "reserved", iso()))
        return reservation_id

    def settle_quota(self, reservation_id, status="consumed"):
        if status not in {"consumed", "uncertain", "released"}:
            raise ValueError("invalid_quota_status")
        with self.transaction() as con:
            con.execute("UPDATE quota_ledger SET status=? WHERE reservation_id=? AND status='reserved'", (status, reservation_id))

    def provider_status(self, provider_id, status, error_code=None, details=None, next_attempt_at=None):
        now = iso()
        with self.transaction() as con:
            con.execute("INSERT INTO provider_state VALUES(?,?,?,?,?,?,?) ON CONFLICT(provider_id) DO UPDATE SET status=excluded.status,last_attempt_at=excluded.last_attempt_at,last_success_at=COALESCE(excluded.last_success_at,provider_state.last_success_at),error_code=excluded.error_code,next_attempt_at=excluded.next_attempt_at,details_json=excluded.details_json", (provider_id, status, now, now if status in {"available", "partial"} else None, error_code, next_attempt_at, dumps(details or {})))

    def list_assets(self, limit=5000):
        return self.rows("SELECT a.*,p.close AS price,p.market_timestamp,p.fetched_at AS price_fetched_at,p.quality_flags_json AS price_quality_flags_json,b.session_date,b.adjustment,b.provider AS price_provider,EXISTS(SELECT 1 FROM watchlist w WHERE w.asset_id=a.asset_id) AS watched FROM assets a LEFT JOIN price_bars b ON b.bar_id=(SELECT x.bar_id FROM price_bars x WHERE x.asset_id=a.asset_id ORDER BY x.session_date DESC,x.provider LIMIT 1) LEFT JOIN price_revisions p ON p.revision_id=b.latest_revision_id ORDER BY a.ticker LIMIT ?", (limit,))

    def list_articles(self, limit=100):
        return self.rows("SELECT a.*,r.* FROM articles a JOIN article_revisions r ON r.revision_id=a.latest_revision_id ORDER BY r.published_at DESC,a.article_id LIMIT ?", (limit,))

    def coverage(self):
        with self.connect() as con:
            count = lambda q: con.execute(q).fetchone()[0]
            priced='SELECT asset_id FROM price_bars'
            if count("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='price_vintages'"):
                priced+=' UNION SELECT asset_id FROM price_vintages'
            return {"catalog_assets": count("SELECT COUNT(*) FROM assets"), "eligible_assets": count("SELECT COUNT(*) FROM assets WHERE eligible=1"), "unclassified_assets": count("SELECT COUNT(*) FROM assets WHERE asset_type='unknown'"), "assets_with_prices": count('SELECT COUNT(DISTINCT asset_id) FROM ('+priced+')'), "assets_with_news": count("SELECT COUNT(DISTINCT asset_id) FROM event_asset_links"), "assets_analyzed": count("SELECT COUNT(DISTINCT asset_id) FROM analyses"), "articles": count("SELECT COUNT(*) FROM articles"), "events": count("SELECT COUNT(*) FROM events"), "truncated_queries_count": count("SELECT COUNT(*) FROM coverage_partitions WHERE truncated=1"), "profile": self.profile, "sqlite_version": sqlite3.sqlite_version, "journal_mode": con.execute("PRAGMA journal_mode").fetchone()[0]}

    def backup(self, destination: str | Path):
        target = Path(destination).resolve()
        if target == self.path or target.exists():
            raise Conflict("backup_requires_new_destination")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(target) as dest:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup_integrity_failure")
        return str(target)
