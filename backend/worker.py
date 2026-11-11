"""Single durable executor. Run with python -m backend.worker --db ... ."""
from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .adapters import BCBAdapter, BrapiAdapter, GDELTAdapter, RSSAdapter
from .alerts import evaluate_alerts
from .cli_runner import AnalysisRunner, status as cli_status
from .cli_process import CLIError, AnalysisMutex
from .analysis_schedule import schedule_analyses
from .core import Conflict, QuotaExceeded, Store, digest, dumps, uid, wal_supported
from .evidence import EvidenceError, build_bundle
from .ingest import build_event
from .safe_http import FetchError
from .timeutil import SAO_PAULO, UTC, instant, iso, news_window
from .execution_mode import resolve_mode, ANALYSIS_KINDS, MODES
from .session_continuity import calendar_status


class Cancelled(RuntimeError):
    pass


class Blocked(RuntimeError):
    pass


def load_source_definitions(config_dir):
    """Read configured sources and documentary probes; never start collection."""
    config_dir = Path(config_dir)
    path = config_dir / "news-sources.json"
    if not path.exists():
        return []
    sources = json.loads(path.read_text(encoding="utf-8-sig"))["sources"]
    verified_path = config_dir / "verified-feeds.json"
    if verified_path.exists():
        document = json.loads(verified_path.read_text(encoding="utf-8-sig"))
        verified = {s["id"]: s for s in document["sources"]}
        from urllib.parse import urlsplit
        for source in sources:
            item = verified.get(source["id"], {})
            if item:
                source["feed_checked_at"] = item.get("checked_at", document.get("checked_at"))
                if "last_operational_probe_at" in item:
                    source["last_operational_probe_at"] = item["last_operational_probe_at"]
            if item.get("status") == "rss_metadata_verified":
                source["rss_urls"] = item["rss_urls"]
                source["rss_allowed_hostnames"] = [urlsplit(url).hostname for url in item["rss_urls"]]
                source["rss_sections"] = item.get("rss_sections", {url: "Business" if source["id"] == "us_nyt" else "World" for url in item["rss_urls"] if source["id"] in {"us_nyt", "us_fox"}})
            elif item.get("status"):
                source["feed_status"] = item["status"]
    return sources


class Worker:
    def __init__(self, store: Store, config_dir: str | Path | None = None, schema_dir: str | Path | None = None, handlers=None, execution_mode=None, reconciliation_mutex_factory=AnalysisMutex):
        self.store = store
        self.execution_mode = resolve_mode(execution_mode)
        root = Path(__file__).resolve().parent.parent
        self.config_dir = Path(config_dir) if config_dir else root / "config"
        self.schema_dir = Path(schema_dir) if schema_dir else root / "schemas"
        self.owner = uid("worker")
        self.stop_event = threading.Event()
        self.current_job = None
        self.lease_lost = False
        self.handlers = handlers or {}
        self._heartbeat_thread = None
        self.reconciliation_mutex_factory = reconciliation_mutex_factory

    def sources(self):
        return load_source_definitions(self.config_dir)

    def _heartbeats(self):
        while not self.stop_event.wait(15):
            try:
                self.store.heartbeat(self.owner, self.current_job)
            except Exception:
                self.lease_lost = True
                self.stop_event.set()
                break

    def start(self, *, recover_job_ids=None, consumer_scope='restricted'):
        if self.execution_mode == 'manual-analysis':
            # A stale heartbeat alone cannot prove that a CLI call ended. The
            # runner holds this same mutex until its contained subtree is closed.
            with self.reconciliation_mutex_factory():
                self.store.acquire_worker(self.owner, execution_mode=self.execution_mode, recover_job_ids=[], analysis_mutex_held=True, consumer_scope='restricted')
                self.store.worker_owner = self.owner
                try:
                    self._interrupt_previous_manual_jobs()
                except BaseException:
                    self.store.worker_owner = None
                    self.store.release_worker(self.owner)
                    raise
        else:
            try:
                self.store.acquire_worker(self.owner, execution_mode=self.execution_mode, recover_job_ids=recover_job_ids, consumer_scope=consumer_scope)
            except Conflict as exc:
                if str(exc) != 'manual_takeover_guard_required':
                    raise
                # The transactional check closes the race between inspecting a
                # lease and acquiring it. Normal modes cannot evict a live CLI.
                with self.reconciliation_mutex_factory():
                    self.store.acquire_worker(self.owner, execution_mode=self.execution_mode,
                                              recover_job_ids=recover_job_ids, analysis_mutex_held=True,
                                              consumer_scope=consumer_scope)
            self.store.worker_owner = self.owner
        self._heartbeat_thread = threading.Thread(target=self._heartbeats, daemon=True, name="radar-worker-heartbeat")
        self._heartbeat_thread.start()

    def _interrupt_previous_manual_jobs(self):
        """Caller holds AnalysisMutex and the new fencing lease; never replay."""
        with self.store.transaction() as con:
            rows = con.execute("""SELECT job_id,attempts FROM jobs WHERE kind='analyse'
                AND status IN ('queued','running','retry_wait')
                AND json_type(payload_json,'$.manual_owner')='text'
                AND json_extract(payload_json,'$.manual_owner')<>?""", (self.owner,)).fetchall()
            for job in rows:
                uncertain = job['attempts'] > 0
                con.execute("""UPDATE jobs SET status='blocked',owner=NULL,lease_until=NULL,
                    updated_at=?,error_code='manual_session_interrupted',result_json=? WHERE job_id=?""",
                    (iso(), dumps({'usage_uncertain': uncertain}), job['job_id']))
                con.execute("""UPDATE job_runs SET status='interrupted',finished_at=?,
                    error_code='manual_session_interrupted' WHERE job_id=? AND finished_at IS NULL""",
                    (iso(), job['job_id']))
                con.execute('INSERT INTO audit_events(event_type,created_at,payload_json) VALUES(?,?,?)',
                    ('manual_session_interrupted', iso(), dumps({'job_id': job['job_id'], 'usage_uncertain': uncertain})))

    def manual_job_ids(self):
        return [row['job_id'] for row in self.store.rows("""SELECT job_id FROM jobs
            WHERE kind='analyse' AND status='queued' AND cancel_requested=0
            AND json_extract(payload_json,'$.manual_owner')=?
            ORDER BY created_at,job_id LIMIT 1""", (self.owner,))]

    def check_cancelled(self, job):
        current = self.store.get_job(job["job_id"])
        if self.lease_lost:
            raise Conflict("worker_lease_lost")
        if self.stop_event.is_set():
            raise InterruptedError("worker_shutdown")
        if current["cancel_requested"]:
            raise Cancelled("cancel_requested")

    def schedule(self):
        if self.execution_mode == 'manual-analysis':
            return
        if self.store.profile == "demo" or not self.store.setting("schedule_enabled", True):
            return
        now = datetime.now(UTC)
        # Buckets coalesce missed runs rather than enqueueing every missed hour.
        interval = max(60, int(self.store.setting("news_interval_minutes", 60)))
        news_bucket = int(now.timestamp()) // (interval * 60)
        self.store.enqueue("news", {"as_of": iso(now)}, f"schedule:news:{news_bucket}")
        week = int(now.timestamp()) // (7 * 86400)
        self.store.enqueue("catalog", {}, f"schedule:catalog:{week}", priority=30)
        # Price automatic jobs require an explicitly verified exchange session.
        day = now.astimezone(SAO_PAULO).date().isoformat()
        self.store.enqueue("macro", {"as_of": iso(now)}, "schedule:macro:" + day, priority=40)
        calendar = calendar_status(self.store, iso(now))
        if calendar['details']['automatic_prices'] == 'ready':
            self.store.enqueue("prices", {"as_of": iso(now)}, "schedule:prices:" + day, priority=40)
        # A scheduling decision is not a provider attempt or successful collection.
        # Keep those clocks unchanged while clearing obsolete errors on transitions.
        with self.store.transaction() as con:
            con.execute("""INSERT INTO provider_state
                (provider_id,status,last_attempt_at,last_success_at,error_code,next_attempt_at,details_json)
                VALUES('market_calendar',?,NULL,NULL,?,?,?)
                ON CONFLICT(provider_id) DO UPDATE SET status=excluded.status,
                error_code=excluded.error_code,next_attempt_at=excluded.next_attempt_at,
                details_json=excluded.details_json""",
                (calendar['status'], calendar['error_code'], calendar['next_attempt_at'],
                 dumps({**calendar['details'], 'manual_price_fetch': 'available'})))
        if self.execution_mode == 'analysis-enabled':
            schedule_analyses(self.store, self.config_dir / "ai-runtime.json", self.config_dir.parent / "docs" / "cli-validation.json", iso(now))

    def run_once(self, *, job_ids=None):
        if self.execution_mode == 'manual-analysis' and job_ids is None:
            raise Conflict('manual_job_scope_required')
        if self._heartbeat_thread is None:
            self.start(recover_job_ids=job_ids)
        self.store.heartbeat(self.owner)
        job = self.store.claim(self.owner, job_ids=job_ids)
        if not job:
            return False
        self.current_job = job["job_id"]
        try:
            self.check_cancelled(job)
            self.ensure_job_allowed(job)
            result = self.handlers[job["kind"]](job) if job["kind"] in self.handlers else self.execute(job)
            self.check_cancelled(job)
            self.store.finish(job["job_id"], self.owner, result.get("status", "succeeded"), result)
        except Cancelled:
            self.store.finish(job["job_id"], self.owner, "cancelled", error_code="cancel_requested")
        except InterruptedError:
            if self.execution_mode == 'manual-analysis':
                self.store.finish(job["job_id"], self.owner, "blocked", result={'usage_uncertain': True}, error_code="manual_session_interrupted")
            else:
                self.store.finish(job["job_id"], self.owner, "retry_wait", error_code="worker_shutdown", retry_after=0)
        except QuotaExceeded:
            self.store.finish(job["job_id"], self.owner, "paused", error_code="local_request_budget_exhausted")
        except CLIError as exc:
            self.store.provider_status("codex_cli", "unavailable", exc.code, {"usage_uncertain": exc.uncertain, "paid_fallback": False})
            self.store.finish(job["job_id"], self.owner, "blocked", result={"usage_uncertain": exc.uncertain}, error_code=exc.code)
        except Blocked as exc:
            self.store.finish(job["job_id"], self.owner, "blocked", error_code=str(exc))
        except FetchError as exc:
            provider_id = "bcb_sgs" if job["kind"] in {"macro", "bcb"} else job["kind"]
            self.store.provider_status(provider_id, "unavailable", exc.code)
            self.store.finish(job["job_id"], self.owner, "retry_wait" if exc.retryable else "blocked", error_code=exc.code, retry_after=exc.retry_after)
        except (ValueError, KeyError, EvidenceError):
            self.store.finish(job["job_id"], self.owner, "failed", error_code="invalid_pipeline_data")
        except Conflict:
            # A worker that lost its fencing lease must not acknowledge work.
            raise
        except Exception as exc:
            # Never persist unfiltered provider exception text: it could contain URL tokens.
            self.store.finish(job["job_id"], self.owner, "failed", error_code="pipeline_" + type(exc).__name__)
        finally:
            self.current_job = None
        evaluate_alerts(self.store)
        return True

    def ensure_job_allowed(self, job):
        if 'reanalyze' in job.get('payload', {}):
            if type(job['payload']['reanalyze']) is not bool:
                raise Blocked('manual_reanalysis_scope_required')
            if job['payload']['reanalyze'] and self.execution_mode != 'manual-analysis':
                raise Blocked('manual_reanalysis_scope_required')
        if self.execution_mode == 'manual-analysis':
            if job['kind'] != 'analyse' or job.get('payload', {}).get('manual_owner') != self.owner:
                raise Blocked('manual_job_scope_required')
        elif job.get('payload', {}).get('manual_owner') is not None:
            raise Blocked('manual_job_scope_required')
        if job['kind'] in ANALYSIS_KINDS and self.execution_mode == 'collection-only':
            raise Blocked('analysis_disabled_by_execution_mode')

    def execute(self, job):
        self.ensure_job_allowed(job)
        if self.store.profile == "demo" and job["kind"] not in {"diagnostics", "bundle", "analyse", "analysis", "maintenance", "demo_task"}:
            raise Blocked("demo_network_collection_disabled")
        kind, payload = job["kind"], job["payload"]
        as_of = payload.get("as_of") or iso()
        if kind in {"diagnostics", "probe"}:
            return {"database": self.store.coverage(), "ai": cli_status(self.config_dir / "ai-runtime.json", self.config_dir.parent / "docs" / "cli-validation.json"), "sources_configured": len(self.sources())}
        if kind == "refresh":
            children = [self.store.enqueue(child, {"as_of": as_of}, f"refresh:{job['job_id']}:{child}")["job_id"] for child in ("catalog", "news", "prices", "macro")]
            return {"children": children}
        if kind == "demo_task":
            return {"message": "Tarefa local concluída; nenhum dado financeiro criado."}
        if kind == "catalog":
            adapter = BrapiAdapter(self.store)
            page = job["checkpoint"].get("page", 1)
            total = job["checkpoint"].get("total", 0)
            while page <= 100:
                self.check_cancelled(job)
                result = adapter.catalog_page(page)
                total += result["received"]
                self.store.checkpoint(job["job_id"], self.owner, {"page": page + 1, "total": total}, min(0.99, total / max(total + 1, result.get("total_items") or total + 100)))
                if not result["has_next"]:
                    self.store.provider_status("brapi_catalog", "available", details={"catalog_received": total, "provider_catalog_complete": True, "b3_completeness": "not_certified"})
                    # Re-associate previously collected articles after catalogue arrival.
                    for article in self.store.rows("SELECT article_id FROM articles ORDER BY article_id"):
                        self.check_cancelled(job)
                        build_event(self.store, article["article_id"])
                    return {"received": total, "provider_catalog_complete": True}
                page += 1
            return {"status": "partial", "received": total, "gaps": ["catalog_page_safety_limit"]}
        if kind in {"news", "refresh_news"}:
            sources = [s for s in self.sources() if s.get("enabled") and (not payload.get("source_id") or s["id"] == payload["source_id"])]
            results = []
            for i, source in enumerate(sources):
                self.check_cancelled(job)
                try:
                    result = RSSAdapter(self.store, source).collect(as_of)
                except FetchError as exc:
                    # A provider outage must not starve unrelated sources.
                    result = {"status": "retry_wait" if exc.retryable else "blocked", "reason": exc.code, "retry_after": exc.retry_after}
                except QuotaExceeded:
                    result = {"status": "paused", "reason": "local_request_budget_exhausted"}
                    self.store.provider_status(source["id"], "unavailable", result["reason"])
                results.append({"source_id": source["id"], **result})
                self.store.checkpoint(job["job_id"], self.owner, {"source_id": source["id"]}, (i + 1) / max(1, len(sources)))
            return {"status": "partial" if any(r["status"] != "succeeded" for r in results) or not results else "succeeded", "sources": results, "gaps": [r["source_id"] for r in results if r["status"] != "succeeded"]}
        if kind in {"prices", "refresh_prices"}:
            assets = self.store.rows("SELECT * FROM assets WHERE eligible=1 ORDER BY asset_id")
            if payload.get("asset_id"):
                assets = [a for a in assets if a["asset_id"] == payload["asset_id"]]
            if not assets:
                raise Blocked("eligible_catalog_required")
            results, failures = [], []
            adapter = BrapiAdapter(self.store)
            completed = set(job["checkpoint"].get("completed", []))
            for i, asset in enumerate(assets):
                self.check_cancelled(job)
                if asset["asset_id"] in completed:
                    continue
                try:
                    results.append({"asset_id": asset["asset_id"], **adapter.prices(asset, as_of)})
                    completed.add(asset["asset_id"])
                except FetchError as exc:
                    if exc.retryable:
                        raise
                    failures.append({"asset_id": asset["asset_id"], "reason": exc.code})
                self.store.checkpoint(job["job_id"], self.owner, {"completed": sorted(completed)}, (i + 1) / len(assets))
            return {"status": "partial" if failures else "succeeded", "prices": results, "gaps": failures}
        if kind in {"backfill", "gdelt"}:
            sources = [s for s in self.sources() if s.get("enabled") and (not payload.get("source_id") or s["id"] == payload["source_id"])]
            start, end = news_window(as_of)
            queued = []
            for source in sources:
                adapter = GDELTAdapter(self.store, source)
                partition = adapter.partition(start, end)
                child = self.store.enqueue("gdelt_partition", {"source_id": source["id"], "partition_id": partition}, "gdelt:" + partition, priority=70)
                queued.append(child["job_id"])
            return {"children": queued, "coverage": "pending_partition_jobs"}
        if kind == "gdelt_partition":
            source = next((s for s in self.sources() if s["id"] == payload["source_id"]), None)
            if not source:
                raise Blocked("source_not_configured")
            result = GDELTAdapter(self.store, source).collect_partition(payload["partition_id"])
            for child in result["children"]:
                self.store.enqueue("gdelt_partition", {"source_id": source["id"], "partition_id": child}, "gdelt:" + child, priority=70)
            return {**result, "status": "partial" if result["status"] in {"split", "truncated"} else "succeeded", "partition_status": result["status"]}
        if kind in {"macro", "bcb"}:
            return BCBAdapter(self.store).collect(int(payload.get("series_code", 432)), as_of)
        if kind in {"bundle", "analyse", "analysis"}:
            bundle = build_bundle(self.store, payload["asset_id"], as_of, self.schema_dir)
            if kind == "bundle":
                return {"bundle_id": bundle["bundle_id"], "evidence_count": len(bundle["evidence"]), "snapshot_as_of": bundle["as_of"], "requested_as_of": iso(as_of), "cache_reused": bundle["as_of"] != iso(as_of)}
            runner = AnalysisRunner(self.store, schema_dir=self.schema_dir, prompt_dir=self.schema_dir.parent / "prompts",
                                    config_path=self.config_dir / "ai-runtime.json",
                                    report_path=self.config_dir.parent / "docs" / "cli-validation.json")
            limits = {'repair_limit': 0} if self.execution_mode == 'manual-analysis' else {}
            if payload.get('reanalyze') is True:
                limits['new_request_key'] = job['job_id']
            result = runner.run(bundle, cancel_check=lambda: self.check_cancelled(job), job_id=job['job_id'], **limits)
            return {"analysis_id": result["analysis"]["analysis_id"], "bundle_id": bundle["bundle_id"],
                    "snapshot_as_of": bundle["as_of"], "requested_as_of": iso(as_of),
                    "cache_reused": result["cache_reused"], "model": result["model"],
                    "usage": result["usage"], "calls": result["calls"]}
        if kind == "maintenance":
            return {"integrity_check": self.store.rows("PRAGMA integrity_check")[0]["integrity_check"]}
        raise Blocked("unsupported_job_kind")

    def close(self):
        self.stop_event.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2)
        if self.store.worker_owner == self.owner:
            self.store.worker_owner = None
        self.store.release_worker(self.owner)

    def run_forever(self, poll_seconds=2):
        # Only this continuous loop commits to draining the general queue.
        # Starting a heartbeat or executing one bounded pass is insufficient.
        self.start(consumer_scope='general' if self.execution_mode != 'manual-analysis' else 'restricted')
        last_schedule = 0.0
        try:
            while not self.stop_event.is_set():
                if self.execution_mode != 'manual-analysis' and time.monotonic() - last_schedule > 60:
                    self.schedule()
                    last_schedule = time.monotonic()
                scope = {'job_ids': self.manual_job_ids()} if self.execution_mode == 'manual-analysis' else {}
                if not self.run_once(**scope):
                    self.stop_event.wait(poll_seconds)
        finally:
            self.close()


def main():
    parser = argparse.ArgumentParser(description="Radar B3: worker local único")
    parser.add_argument("--db", required=True)
    parser.add_argument("--profile", choices=["real", "demo"], default="real")
    parser.add_argument("--config-dir")
    parser.add_argument("--schema-dir")
    parser.add_argument("--once", action="store_true")
    parser.add_argument('--execution-mode', choices=MODES, default=os.environ.get('RADAR_EXECUTION_MODE', 'collection-only'))
    args = parser.parse_args()
    os.environ['RADAR_EXECUTION_MODE'] = args.execution_mode
    store = Store(args.db, args.profile, wal=wal_supported())
    worker = Worker(store, args.config_dir, args.schema_dir, execution_mode=args.execution_mode)
    signal.signal(signal.SIGINT, lambda *_: worker.stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop_event.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: worker.stop_event.set())
    try:
        if args.once:
            try:
                if args.execution_mode == 'manual-analysis':
                    worker.start()
                    worker.run_once(job_ids=worker.manual_job_ids())
                else:
                    worker.run_once()
            finally:
                worker.close()
        else:
            worker.run_forever()
    except (Conflict, CLIError) as exc:
        print("Worker não iniciado: " + str(exc))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
