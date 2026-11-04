import copy
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.adapters import BCBAdapter, GDELTAdapter, RSSAdapter, parse_feed
from backend.core import Conflict, QuotaExceeded, Store, dumps, wal_supported
from backend.evidence import EvidenceError, build_bundle, metrics_for_bars, validate_analysis, validate_bundle
from backend.ingest import canonical_url, ingest_article, ingest_price, upsert_catalog
from backend.safe_http import FetchError, Response, public_addresses, retry_delay, validate_url
from backend.timeutil import UTC, iso, news_window
from backend.worker import Worker
from backend.alerts import evaluate_alerts

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
NOW = "2026-10-26T12:00:00Z"
SOURCE = {"id": "fin_yahoo", "allowed_hostnames": ["finance.yahoo.com"], "www_alias_allowed": False, "publisher_group": "yahoo", "section_allowlist": ["Markets", "Companies"]}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "real.db")


def asset(store):
    upsert_catalog(store, [{"symbol": "PETR4", "name": "Petrobras", "assetType": "stock", "subType": "stock"}], fetched_at="2026-10-01T12:00:00Z")
    return "brapi:PETR4"


def article(store, title="PETR4 tem mudança operacional", content="Mudança da produção foi divulgada.", fetched="2026-10-25T12:00:00Z", **changes):
    return ingest_article(store, {"url": "https://finance.yahoo.com/news/petr4?utm_source=test", "title": title, "content": content, "published_at": "2026-10-24T12:00:00Z", "timestamp_precision": "instant", "access_level": "excerpt", "fetched_at": fetched, **changes}, SOURCE, NOW)


def test_calendar_months_and_timezone():
    assert news_window(NOW) == ("2026-07-26T03:00:00.000000Z", "2026-10-26T12:00:00.000000Z")
    assert news_window("2024-05-31T16:00:00Z")[0] == "2024-02-29T03:00:00.000000Z"
    assert news_window("2025-05-31T16:00:00Z")[0] == "2025-02-28T03:00:00.000000Z"
    assert news_window("2026-01-01T01:00:00Z")[0] == "2025-09-30T03:00:00.000000Z"
    with pytest.raises(ValueError):
        news_window("2026-10-26T12:00:00")


def test_wal_gate_and_profiles(store):
    assert not wal_supported((3, 42, 0))
    assert not wal_supported((3, 51, 2))
    assert wal_supported((3, 44, 6))
    assert wal_supported((3, 54, 1))
    with pytest.raises(Conflict):
        Store(store.path, "demo")


def test_job_idempotency_single_worker_and_recovery(store):
    job = store.enqueue("demo_task", dedupe_key="one")
    assert store.enqueue("demo_task", dedupe_key="one")["job_id"] == job["job_id"]
    store.acquire_worker("first", now="2026-01-01T00:00:00Z")
    with pytest.raises(Conflict):
        store.acquire_worker("second", now="2026-01-01T00:00:10Z")
    store.acquire_worker("first")
    claimed = store.claim("first")
    assert claimed["attempts"] == 1
    with store.transaction() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    store.acquire_worker("second")
    assert store.get_job(job["job_id"])["status"] == "queued"
    assert store.claim("second")["job_id"] == job["job_id"]
    store.finish(job["job_id"], "second")
    assert store.get_job(job["job_id"])["status"] == "succeeded"


def test_cancel_and_retry_limit(store):
    queued = store.enqueue("demo_task")
    assert store.cancel_job(queued["job_id"])["status"] == "cancelled"
    job = store.enqueue("demo_task", max_attempts=1)
    store.acquire_worker("owner")
    store.claim("owner")
    store.finish(job["job_id"], "owner", "retry_wait", retry_after=0)
    assert store.get_job(job["job_id"])["status"] == "failed"
    assert store.retry_job(job["job_id"])["status"] == "queued"


def test_quota_survives_restart_and_unknown_consumption(store):
    reservation = store.reserve_quota("x", "2026-10", 2, "req", 1)
    store.settle_quota(reservation, "uncertain")
    again = Store(store.path)
    again.reserve_quota("x", "2026-10", 2, "req", 1)
    again.reserve_quota("x", "2026-10", 2, "req2", 1)
    with pytest.raises(QuotaExceeded):
        again.reserve_quota("x", "2026-10", 2)


def test_catalog_types_and_fractional_pending(store):
    upsert_catalog(store, [{"symbol": "STCK3", "assetType": "stock"}, {"symbol": "UNIT11", "assetType": "stock", "subType": "unit"}, {"symbol": "FUND11", "assetType": "fund"}, {"symbol": "UNKN11"}, {"symbol": "STCK3F", "assetType": "stock"}])
    rows = {r["ticker"]: r for r in store.list_assets()}
    assert rows["UNIT11"]["asset_type"] == "unit"
    assert rows["FUND11"]["eligible"] == 0
    assert rows["UNKN11"]["asset_type"] == "unknown"
    assert rows["STCK3F"]["eligible"] == 0
    assert store.coverage()["eligible_assets"] == 2


def test_article_dedup_revisions_and_point_in_time(store):
    aid = asset(store)
    first = article(store)
    assert article(store, fetched="2026-10-25T13:00:00Z")["status"] == "duplicate"
    revised = article(store, content="Produção corrigida após divulgação.", fetched="2026-10-26T11:00:00Z")
    assert revised["status"] == "revised"
    assert len(store.rows("SELECT * FROM article_revisions")) == 2
    old_bundle = build_bundle(store, aid, "2026-10-25T14:00:00Z", SCHEMAS)
    assert first["revision_id"] in [e["evidence_id"] for e in old_bundle["evidence"]]
    assert revised["revision_id"] not in [e["evidence_id"] for e in old_bundle["evidence"]]
    assert canonical_url("https://finance.yahoo.com/news/petr4?a=1&utm_medium=x#anchor") == "https://finance.yahoo.com/news/petr4?a=1"
    with store.connect() as con, pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE article_revisions SET content='changed'")


def test_unknown_and_future_article_dates_excluded(store):
    aid = asset(store)
    assert article(store, published_at="2026-10-27T12:00:00Z")["status"] == "outside_window"
    article(store, published_at=None, timestamp_precision="unknown")
    bundle = build_bundle(store, aid, NOW, SCHEMAS)
    assert not bundle["evidence"]
    assert bundle["computed_metrics"][0]["value"] is None


def test_price_revisions_null_and_adjustment_block(store):
    aid = asset(store)
    bar = {"session_date": "2026-10-23", "close": 35, "volume": None, "market_timestamp": "2026-10-23T20:00:00Z", "fetched_at": "2026-10-23T21:00:00Z"}
    assert ingest_price(store, aid, bar)["status"] == "created"
    assert ingest_price(store, aid, bar)["status"] == "duplicate"
    ingest_price(store, aid, {**bar, "close": 36, "fetched_at": "2026-10-24T21:00:00Z"})
    assert len(store.rows("SELECT * FROM price_revisions")) == 2
    assert store.list_assets()[0]["price"] == 36
    assert store.rows("SELECT volume FROM price_revisions")[0]["volume"] is None
    with pytest.raises(ValueError):
        ingest_price(store, aid, {**bar, "close": float("nan")})


def bars(adjustment="split_adjusted", count=22):
    result = [{"provider": "fixture", "currency": "BRL", "adjustment": adjustment, "close": 100 + i, "volume": 1000 if i < count - 1 else 2000, "complete_session": True, "session_date": f"2026-10-{i+1:02d}", "market_timestamp": f"2026-10-{i+1:02d}T20:00:00Z", "revision_id": f"p{i}"} for i in range(count)]

    from datetime import date, timedelta
    for bar in result:
        day = date.fromisoformat(bar['session_date'])
        bar['calendar'] = {'session_date': day.isoformat(), 'closes_at': bar['market_timestamp'],
                           'previous_open_session': (day-timedelta(days=1)).isoformat(), 'closed_dates': [],
                           'source': 'https://example.org/calendar', 'version': 'synthetic',
                           'verified_at': '2026-09-01T00:00:00Z',
                           'expected_last_session': result[-1]['session_date']}
    return result


def test_metric_math_and_invalid_adjustments():
    result = metrics_for_bars(bars())
    assert result[0]["value"] == pytest.approx(121 / 120 - 1)
    assert result[-1]["value"] == 2
    assert result[3]["missing_reason"] == "insufficient_observations"
    assert metrics_for_bars(bars("unknown"))[0]["value"] is None
    raw = bars("raw")
    raw[-1]["close"] /= 2
    assert metrics_for_bars(raw)[0]["value"] is None
    incomplete = bars()
    incomplete[-1]["complete_session"] = False
    assert metrics_for_bars(incomplete)[-1]["value"] is None
    no_volume = bars()
    no_volume[-1]["volume"] = None
    assert metrics_for_bars(no_volume)[-1]["missing_reason"] == "missing_volume"


def test_bundle_reproducibility_hashes_and_future_rejection(store):
    aid = asset(store)
    article(store)
    bundle = build_bundle(store, aid, NOW, SCHEMAS)
    assert bundle == build_bundle(store, aid, NOW, SCHEMAS)
    invalid = copy.deepcopy(bundle)
    invalid["evidence"][0]["fetched_at"] = "2027-01-01T00:00:00Z"
    with pytest.raises(EvidenceError, match="future_evidence"):
        validate_bundle(invalid, SCHEMAS)
    invalid = copy.deepcopy(bundle)
    invalid["evidence"][0]["content"] = "tampered"
    with pytest.raises(EvidenceError, match="hash_mismatch"):
        validate_bundle(invalid, SCHEMAS)


def analysis_for(bundle):
    eid = bundle["evidence"][0]["evidence_id"]
    return {"schema_version": "1.0", "analysis_id": "a1", "as_of": bundle["as_of"], "asset_id": bundle["asset"]["asset_id"], "status": "inconclusive", "synthesis": "Fato requer investigação.", "claims": [{"kind": "fact", "statement": "Mudança da produção foi divulgada.", "evidence_ids": [eid]}], "scenarios": [], "opportunity": {"classification": "watch", "rationale": "Aguardar confirmação.", "evidence_ids": [eid]}, "risks": ["Dados limitados"], "missing_evidence": ["Exposição"], "data_quality_flags": [], "evidence_ids": [eid]}


def test_analysis_rejects_invented_refs_numbers_and_identity(store):
    aid = asset(store)
    article(store)
    bundle = build_bundle(store, aid, NOW, SCHEMAS)
    valid = analysis_for(bundle)
    assert validate_analysis(valid, bundle, SCHEMAS)
    for field, value in (("asset_id", "other"), ("as_of", "2025-01-01T00:00:00Z")):
        invalid = copy.deepcopy(valid)
        invalid[field] = value
        with pytest.raises(EvidenceError):
            validate_analysis(invalid, bundle, SCHEMAS)
    invalid = copy.deepcopy(valid)
    invalid["claims"][0]["evidence_ids"] = ["invented"]
    with pytest.raises(EvidenceError):
        validate_analysis(invalid, bundle, SCHEMAS)
    invalid = copy.deepcopy(valid)
    invalid["claims"][0]["statement"] = "Lucro cresceu 999%."
    with pytest.raises(EvidenceError, match="number_not_in"):
        validate_analysis(invalid, bundle, SCHEMAS)


@pytest.mark.parametrize("url", ["http://good.test/a", "https://good.test.evil/a", "https://good.test@evil.test/a", "https://good.test:444/a", "https://127.0.0.1/", "https://good.test/a#x"])
def test_safe_http_url_policy(url):
    with pytest.raises(FetchError):
        validate_url(url, {"good.test"})


def test_public_dns_rejects_private_and_mixed():
    resolver = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 443))]
    with pytest.raises(FetchError, match="non_public"):
        public_addresses("good.test", resolver)
    assert retry_delay("120") == 120


def test_rss_section_dates_and_malicious_xml():
    xml = b'<rss><channel><item><title>PETR4</title><link>https://finance.yahoo.com/news/a</link><category>Companies</category><pubDate>Sun, 25 Oct 2026 12:00:00 GMT</pubDate><description>Production update</description></item><item><title>Sports</title><link>https://finance.yahoo.com/news/sport</link><category>Sports</category></item></channel></rss>'
    result = parse_feed(xml, SOURCE, NOW)
    assert len(result) == 1
    assert result[0]["timestamp_precision"] == "instant"
    assert result[0]["access_level"] == "excerpt"
    with pytest.raises(FetchError):
        parse_feed(b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///secret">]><rss/>', SOURCE)


class FakeHTTP:
    def __init__(self, payload):
        self.payload = payload
    def get(self, *a, **k):
        return Response(200, dumps(self.payload).encode(), {})


def test_gdelt_partition_splits_and_marks_saturation(store):
    records = [{"url": f"https://finance.yahoo.com/news/{i}", "title": f"PETR4 item {i}", "seendate": "20261025T120000Z"} for i in range(250)]
    adapter = GDELTAdapter(store, SOURCE, FakeHTTP({"articles": records}))
    partition = adapter.partition("2026-10-25T00:00:00Z", "2026-10-26T00:00:00Z")
    assert len(adapter.collect_partition(partition)["children"]) == 2
    short = adapter.partition("2026-10-25T00:00:00Z", "2026-10-25T00:30:00Z")
    assert adapter.collect_partition(short)["status"] == "truncated"
    assert len(store.rows("SELECT * FROM articles")) == 250
    assert all(r["published_at"] is None for r in store.list_articles())


def test_bcb_discards_future_observation(store):
    adapter = BCBAdapter(store, FakeHTTP([{"data": "23/10/2026", "valor": "14.9"}, {"data": "04/11/2026", "valor": "15.0"}]))
    assert adapter.collect(432, NOW)["observations"] == 1


def test_worker_runs_and_preserves_failure(store):
    job = store.enqueue("demo_task")
    worker = Worker(store)
    try:
        assert worker.run_once()
        assert store.get_job(job["job_id"])["status"] == "succeeded"
        bad = store.enqueue("impossible")
        worker.run_once()
        assert store.get_job(bad["job_id"])["status"] == "blocked"
    finally:
        worker.close()


def test_backup_restores_consistently(store, tmp_path):
    asset(store)
    article(store)
    backup = store.backup(tmp_path / "backup.db")
    restored = Store(backup)
    assert restored.coverage()["articles"] == 1
    assert restored.rows("PRAGMA integrity_check")[0]["integrity_check"] == "ok"


def synthetic_calendar(store):
    # Synthetic fixture, including a Saturday opening: not a real B3 calendar.
    from backend.market_calendar import import_calendar
    document = {'schema_version': 1, 'source': 'https://example.org/calendar',
                'version': 'synthetic', 'verified_at': '2026-10-22T12:00:00Z',
                'start': '2026-10-23', 'end': '2026-10-27',
                'sessions': [{'session_date': f'2026-10-{day}', 'is_open': day != 25,
                              'closes_at': f'2026-10-{day}T20:00:00Z' if day != 25 else None}
                             for day in range(23, 28)]}
    with store.connect() as con:
        import_calendar(con, document)


def test_nonnull_metric_recomputed_from_price_evidence(tmp_path):
    store = Store(tmp_path / "calendar-demo.sqlite3", "demo")
    synthetic_calendar(store)
    aid = asset(store)
    for day, close in ((23, 100), (24, 110)):
        ingest_price(store, aid, {"session_date": f"2026-10-{day}", "close": close, "volume": 1000, "market_timestamp": f"2026-10-{day}T20:00:00Z", "fetched_at": f"2026-10-{day}T21:00:00Z", "adjustment": "split_adjusted", "complete_session": True})
    bundle = build_bundle(store, aid, NOW, SCHEMAS)
    metric = bundle["computed_metrics"][0]
    assert metric["value"] == pytest.approx(0.1)
    ev = next(e for e in bundle["evidence"] if e["evidence_id"] == metric["evidence_id"])
    forged = json.loads(ev["content"])
    forged["value"] = metric["value"] = 0.5
    ev["content"] = dumps(forged)
    from backend.core import digest
    ev["content_sha256"] = digest(ev["content"])
    with pytest.raises(EvidenceError, match="recalculation_mismatch"):
        validate_bundle(bundle, SCHEMAS)


def test_total_feed_failure_does_not_claim_last_success(store):
    source = {**SOURCE, "rss_urls": ["https://finance.yahoo.com/rss"]}
    adapter = RSSAdapter(store, source, FakeHTTP({"not": "xml"}))
    result = adapter.collect(NOW)
    assert result["status"] == "blocked"
    state = store.rows("SELECT * FROM provider_state WHERE provider_id=?", (SOURCE["id"],))[0]
    assert state["status"] == "unavailable"
    assert state["last_success_at"] is None


def test_alert_dedup_rearm_and_cooldown(store):
    with store.transaction() as con:
        con.execute("INSERT INTO alert_rules VALUES('r1','Falha','source_failure',1,?,?,?)", (dumps({"cooldown_minutes": 1}), "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
    store.provider_status("p1", "unavailable", "timeout")
    assert evaluate_alerts(store, NOW) == 1
    assert evaluate_alerts(store, "2026-10-26T12:10:00Z") == 0
    store.provider_status("p1", "available")
    assert evaluate_alerts(store, "2026-10-26T12:11:00Z") == 0
    store.provider_status("p1", "unavailable", "timeout")
    assert evaluate_alerts(store, "2026-10-26T12:12:00Z") == 1
    assert len(store.rows("SELECT * FROM alerts")) == 2


def test_worker_builds_bundle_but_never_invokes_ai(store, tmp_path):
    aid = asset(store)
    job = store.enqueue("analyse", {"asset_id": aid})
    isolated_config = tmp_path / "disabled-config"
    isolated_config.mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "cli-validation.json").write_text("{}", encoding="utf-8")
    (isolated_config / "ai-runtime.json").write_text(dumps({"enabled": False}), encoding="utf-8")
    worker = Worker(store, config_dir=isolated_config, schema_dir=SCHEMAS)
    try:
        worker.run_once()
    finally:
        worker.close()
    assert store.get_job(job["job_id"])["status"] == "blocked"
    assert store.get_job(job["job_id"])["error_code"] == "analysis_isolation_not_verified"
    assert len(store.rows("SELECT * FROM evidence_bundles")) == 1
    assert store.rows("SELECT * FROM analyses") == []


def test_semantic_bundle_cache_preserves_snapshot_clocks_and_expires_daily(tmp_path):
    store = Store(tmp_path / "calendar-demo.sqlite3", "demo")
    synthetic_calendar(store)
    aid = asset(store)
    article(store)
    for day, close in ((23, 100), (24, 110)):
        ingest_price(store, aid, {"session_date": f"2026-10-{day}", "close": close, "market_timestamp": f"2026-10-{day}T20:00:00Z", "fetched_at": f"2026-10-{day}T21:00:00Z", "adjustment": "split_adjusted", "complete_session": True})
    original = build_bundle(store, aid, NOW, SCHEMAS)
    reused = build_bundle(store, aid, "2026-10-26T13:00:00Z", SCHEMAS)
    assert reused == original
    assert reused["as_of"] == iso(NOW)
    metrics = [e for e in reused["evidence"] if e["record_type"] == "computed_metric"]
    assert metrics and all(e["available_at"] == original["as_of"] for e in metrics)
    assert len(store.rows("SELECT * FROM evidence_bundles")) == 1
    expired = build_bundle(store, aid, "2026-10-27T13:00:00Z", SCHEMAS)
    assert expired["bundle_id"] != original["bundle_id"]
    assert expired["window"]["start"] != original["window"]["start"]
    assert len(store.rows("SELECT * FROM evidence_bundles")) == 2


def test_semantic_cache_never_reuses_future_snapshot_and_changes_with_evidence(store):
    aid = asset(store)
    article(store)
    later = build_bundle(store, aid, "2026-10-26T13:00:00Z", SCHEMAS)
    earlier = build_bundle(store, aid, NOW, SCHEMAS)
    assert earlier["as_of"] == iso(NOW)
    assert earlier["bundle_id"] != later["bundle_id"]
    assert earlier == build_bundle(store, aid, NOW, SCHEMAS)
    article(store, content="Produção corrigida.", fetched="2026-10-26T13:30:00Z")
    changed = build_bundle(store, aid, "2026-10-26T14:00:00Z", SCHEMAS)
    assert changed["bundle_id"] != later["bundle_id"]
    store.set_setting("horizons_days", [30])
    reconfigured = build_bundle(store, aid, "2026-10-26T15:00:00Z", SCHEMAS)
    assert reconfigured["bundle_id"] != changed["bundle_id"]


def test_worker_fence_rejects_ingest_after_takeover_and_rolls_back_before_commit(store):
    aid = asset(store)
    store.acquire_worker("old_worker")
    store.worker_owner = "old_worker"
    other_process_store = Store(store.path)
    with other_process_store.transaction() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    other_process_store.acquire_worker("new_worker")
    with pytest.raises(Conflict, match="worker_lease_lost"):
        ingest_price(store, aid, {"session_date": "2026-10-23", "close": 35})
    assert store.rows("SELECT * FROM price_bars") == []
    # Even a lease that expires inside a transaction cannot commit its writes.
    store.worker_owner = "new_worker"
    with pytest.raises(Conflict, match="worker_lease_lost"):
        with store.transaction() as con:
            con.execute("UPDATE assets SET name='must rollback' WHERE asset_id=?", (aid,))
            con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    assert store.rows("SELECT name FROM assets WHERE asset_id=?", (aid,))[0]["name"] == "Petrobras"


def test_daily_bar_evidence_is_date_precision(store):
    aid = asset(store)
    ingest_price(store, aid, {"session_date": "2026-10-23", "close": 35, "market_timestamp": "2026-10-23T00:00:00Z", "fetched_at": "2026-10-24T12:00:00Z", "quality_flags": ["daily_bar_timestamp_not_trade_time"]})
    bundle = build_bundle(store, aid, NOW, SCHEMAS)
    prices = [e for e in bundle["evidence"] if e["record_type"] == "price"]
    assert prices[0]["timestamp_precision"] == "date"
    assert "daily_bar_timestamp_not_trade_time" in json.loads(store.list_assets()[0]["price_quality_flags_json"])


def test_calendar_bundle_is_self_contained_and_rejects_future_provenance(tmp_path):
    store = Store(tmp_path / 'calendar-bundle.sqlite3', 'demo')
    synthetic_calendar(store)
    aid = asset(store)
    for day, close in ((23, 100), (24, 110)):
        ingest_price(store, aid, {'session_date': f'2026-10-{day}', 'close': close,
                     'market_timestamp': f'2026-10-{day}T20:00:00Z',
                     'fetched_at': f'2026-10-{day}T21:00:00Z',
                     'adjustment': 'split_adjusted', 'complete_session': True})
    bundle = build_bundle(store, aid, NOW, SCHEMAS)
    assert bundle['computed_metrics'][0]['value'] == pytest.approx(0.1)
    with store.transaction() as con:
        con.execute('DELETE FROM market_sessions')  # Only this disposable fixture.
    assert validate_bundle(bundle, SCHEMAS)
    rebuilt = build_bundle(store, aid, NOW, SCHEMAS)
    assert rebuilt['computed_metrics'][0]['missing_reason'] == 'calendar_unverified'
    tampered = copy.deepcopy(bundle)
    price = next(e for e in tampered['evidence'] if e['record_type'] == 'price')
    content = json.loads(price['content'])
    content['calendar']['verified_at'] = '2026-10-27T12:00:00Z'
    price['content'] = dumps(content)
    from backend.core import digest
    price['content_sha256'] = digest(price['content'])
    with pytest.raises(EvidenceError, match='future_calendar_evidence'):
        validate_bundle(tampered, SCHEMAS)
