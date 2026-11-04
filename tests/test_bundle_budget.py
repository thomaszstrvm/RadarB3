"""Bound evidence size without weakening financial provenance or original data."""
import copy
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.core import Store, dumps
from backend.evidence import EvidenceError, build_bundle, fit_bundle_budget, validate_bundle
from backend.ingest import ingest_article, ingest_price, upsert_catalog

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
ASSET = "brapi:TEST3"
AS_OF = "2026-10-31T12:00:00Z"
SOURCE = {"id": "synthetic", "allowed_hostnames": ["example.com"], "publisher_group": "synthetic"}


def make_store(tmp_path, *, verified=False, articles=0):
    store = Store(tmp_path / "budget.sqlite3", "demo")
    upsert_catalog(store, [{"symbol": "TEST3", "name": "Synthetic budget fixture", "assetType": "stock"}], fetched_at="2026-08-01T12:00:00Z")
    first = date(2026, 8, 28)
    if verified:
        with store.transaction() as con:
            for n in range(65):
                day = str(first + timedelta(days=n))
                con.execute("INSERT INTO market_sessions VALUES(?,?,?,?,?,?)", (day, n < 64, day + "T20:00:00Z" if n < 64 else None, "synthetic_only", "v1", "2026-08-01T12:00:00Z"))
    for n in range(64):
        day = str(first + timedelta(days=n))
        ingest_price(store, ASSET, {"session_date": day, "close": 100 + n, "volume": 1000 + n,
            "market_timestamp": day + "T20:00:00Z", "fetched_at": day + "T21:00:00Z",
            "adjustment": "split_adjusted" if verified else "unknown", "complete_session": verified})
    for n in range(articles):
        ingest_article(store, {"url": f"https://example.com/news/{n}", "title": f"TEST3 synthetic news {n}",
            "content": f"Synthetic item {n}. " + "Context only. " * 400, "access_level": "excerpt",
            "published_at": f"2026-10-{28+n}T12:00:00Z", "fetched_at": "2026-10-31T10:00:00Z",
            "timestamp_precision": "instant"}, SOURCE, AS_OF)
    return store


def test_unknown_adjustment_keeps_news_latest_price_and_original_history(tmp_path):
    store = make_store(tmp_path, articles=3)
    before = store.rows("SELECT * FROM price_revisions ORDER BY revision_id")
    bundle = build_bundle(store, ASSET, AS_OF, SCHEMAS)
    assert len(dumps(bundle)) <= 60000
    assert validate_bundle(bundle, SCHEMAS)
    assert len([e for e in bundle["evidence"] if e["record_type"] == "article"]) == 3
    prices = [e for e in bundle["evidence"] if e["record_type"] == "price"]
    assert 0 < len(prices) < 64
    assert json.loads(prices[-1]["content"])["session_date"] == "2026-10-30"
    assert all(m["value"] is None and m["missing_reason"] == "adjustment_unknown" for m in bundle["computed_metrics"])
    assert "bundle_price_evidence_omitted" in bundle["quality_flags"]
    assert any(f": {64-len(prices)};" in gap for gap in bundle["coverage"]["known_gaps"])
    assert bundle["coverage"]["partial_text_count"] == 3
    assert store.rows("SELECT * FROM price_revisions ORDER BY revision_id") == before
    assert build_bundle(store, ASSET, AS_OF, SCHEMAS) == bundle


def test_all_valid_metric_inputs_are_protected_even_when_budget_cannot_fit(tmp_path):
    store = make_store(tmp_path, verified=True)
    bundle = build_bundle(store, ASSET, AS_OF, SCHEMAS, max_characters=200000)
    assert validate_bundle(bundle, SCHEMAS)
    metric = next(m for m in bundle["computed_metrics"] if m["metric_id"] == "price_return_63")
    assert metric["value"] is not None and len(metric["evidence_ids"]) == 64
    original = copy.deepcopy(bundle)
    with pytest.raises(EvidenceError, match="bundle_size_limit"):
        fit_bundle_budget(bundle, 60000)
    assert bundle["evidence"] == original["evidence"]
    assert bundle["computed_metrics"] == original["computed_metrics"]
    assert validate_bundle(bundle, SCHEMAS)


def test_final_identifier_size_is_included_before_persistence(tmp_path):
    store = Store(tmp_path / "empty.sqlite3", "demo")
    upsert_catalog(store, [{"symbol": "TEST3", "assetType": "stock"}])
    bundle = build_bundle(store, ASSET, AS_OF, SCHEMAS)
    exact = len(dumps(bundle))
    before = store.rows("SELECT * FROM evidence_bundles")
    assert len(dumps(build_bundle(store, ASSET, AS_OF, SCHEMAS, max_characters=exact))) == exact
    with pytest.raises(EvidenceError, match="bundle_size_limit"):
        build_bundle(store, ASSET, AS_OF, SCHEMAS, max_characters=exact-1)
    assert store.rows("SELECT * FROM evidence_bundles") == before


def test_null_metric_references_and_latest_price_are_never_discarded(tmp_path):
    store = make_store(tmp_path)
    bundle = build_bundle(store, ASSET, AS_OF, SCHEMAS, max_characters=200000)
    prices = [e for e in bundle["evidence"] if e["record_type"] == "price"]
    bundle["computed_metrics"][0]["evidence_ids"] = [prices[0]["evidence_id"]]
    fit_bundle_budget(bundle, 10000)
    assert {prices[0]["evidence_id"], prices[-1]["evidence_id"]} <= {e["evidence_id"] for e in bundle["evidence"]}
    assert validate_bundle(bundle, SCHEMAS)


def test_oldest_article_removed_whole_when_no_optional_price_remains(tmp_path):
    store = make_store(tmp_path, articles=3)
    bundle = build_bundle(store, ASSET, AS_OF, SCHEMAS, max_characters=200000)
    bundle["evidence"] = [e for e in bundle["evidence"] if e["record_type"] == "article"]
    articles = copy.deepcopy(bundle["evidence"])
    fit_bundle_budget(bundle, len(dumps(bundle)) - 1)
    assert bundle["evidence"] == articles[:-1]
    assert bundle["coverage"]["partial_text_count"] == 2
    assert validate_bundle(bundle, SCHEMAS)
