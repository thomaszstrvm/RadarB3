"""Price session chronology, with isolated databases and no external executors."""
import json
from pathlib import Path

import pytest

from backend.core import Store, digest, dumps
from backend.evidence import EvidenceError, build_bundle, validate_bundle
from backend.ingest import ingest_price, upsert_catalog
from backend.timeutil import iso


SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
ASSET = "brapi:TEST3"


@pytest.fixture
def price_store(tmp_path):
    store = Store(tmp_path / "price-session.sqlite3")
    upsert_catalog(store, [{"symbol": "TEST3", "name": "Synthetic session test", "assetType": "stock"}], fetched_at="2024-01-01T12:00:00Z")
    return store


@pytest.mark.parametrize("session,fetched", [
    ("2026-10-27", "2026-10-25T12:00:00Z"),
    ("2026-10-26", "2026-10-26T02:59:59.999999Z"),
    ("2027-01-01", "2027-01-01T02:59:59Z"),
])
@pytest.mark.parametrize("market", [None, "2024-01-01T00:00:00Z"])
def test_ingest_rejects_future_session_without_partial_writes(price_store, session, fetched, market):
    with pytest.raises(ValueError, match="^future_session_date$"):
        ingest_price(price_store, ASSET, {"session_date": session, "close": 10, "market_timestamp": market, "fetched_at": fetched})
    assert price_store.rows("SELECT * FROM price_bars") == []
    assert price_store.rows("SELECT * FROM price_revisions") == []


@pytest.mark.parametrize("session,fetched", [
    ("2026-10-25", "2026-10-26T02:59:59.999999Z"),
    ("2026-10-26", "2026-10-26T03:00:00Z"),
    ("2026-10-26", "2026-10-26T00:00:00-03:00"),
    ("2026-12-31", "2027-01-01T02:59:59Z"),
    ("2024-02-29", "2024-03-01T02:59:59Z"),
])
def test_local_day_boundaries_keep_valid_session(price_store, session, fetched):
    # Midnight UTC is a daily date marker, not an assertion of a trade instant.
    bar = {"session_date": session, "close": 10, "market_timestamp": session + "T00:00:00Z", "fetched_at": fetched, "quality_flags": ["daily_bar_timestamp_not_trade_time"]}
    assert ingest_price(price_store, ASSET, bar)["status"] == "created"
    bundle = build_bundle(price_store, ASSET, fetched, SCHEMAS)
    price = next(e for e in bundle["evidence"] if e["record_type"] == "price")
    assert json.loads(price["content"])["session_date"] == session
    assert price["timestamp_precision"] == "date"
    assert validate_bundle(bundle, SCHEMAS) is True


def insert_legacy_price(store, session, market, fetched):
    """Bypass the ingester to represent an existing row written before the fix."""
    bar_id = "legacy-bar-" + session
    revision_id = "legacy-price-" + session
    with store.transaction() as con:
        con.execute("INSERT INTO price_bars VALUES(?,?,?,?,?,?)", (bar_id, ASSET, "legacy-other-provider", session, "unknown", revision_id))
        con.execute("INSERT INTO price_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (revision_id, bar_id, digest(session), 999, None, None, None, None, "BRL", iso(market) if market else None, iso(fetched), iso(fetched), 0, "[]"))


@pytest.mark.parametrize("market", [None, "2026-10-23T20:00:00Z"])
@pytest.mark.parametrize("session,as_of", [
    ("2026-10-27", "2026-10-26T12:00:00Z"),
    ("2026-10-26", "2026-10-26T02:59:59.999999Z"),
])
def test_bundle_filters_legacy_future_session_before_provider_choice(price_store, market, session, as_of):
    ingest_price(price_store, ASSET, {"session_date": "2026-10-24", "close": 10, "fetched_at": "2026-10-25T12:00:00Z"}, provider="valid-provider")
    insert_legacy_price(price_store, session, market, "2026-10-25T12:00:00Z")
    assert len(price_store.rows("SELECT * FROM price_bars")) == 2
    bundle = build_bundle(price_store, ASSET, as_of, SCHEMAS)
    prices = [e for e in bundle["evidence"] if e["record_type"] == "price"]
    assert len(prices) == 1
    assert prices[0]["source_id"] == "valid-provider"
    assert json.loads(prices[0]["content"])["session_date"] == "2026-10-24"
    assert all(metric["value"] is None for metric in bundle["computed_metrics"])
    assert len(price_store.rows("SELECT * FROM price_bars")) == 2  # No legacy DB rewrite.


@pytest.mark.parametrize("market", [None, "2026-10-23T20:00:00Z"])
@pytest.mark.parametrize("future_session,as_of", [
    ("2026-10-27", "2026-10-26T12:00:00Z"),
    ("2026-10-26", "2026-10-26T02:59:59.999999Z"),
])
def test_semantic_validator_rejects_future_session_with_valid_hash(price_store, market, future_session, as_of):
    ingest_price(price_store, ASSET, {"session_date": "2026-10-24", "close": 10, "market_timestamp": market, "fetched_at": "2026-10-25T12:00:00Z"})
    bundle = build_bundle(price_store, ASSET, as_of, SCHEMAS)
    price = next(e for e in bundle["evidence"] if e["record_type"] == "price")
    content = json.loads(price["content"])
    content["session_date"] = future_session
    price["content"] = dumps(content)
    price["content_sha256"] = digest(price["content"])
    with pytest.raises(EvidenceError, match="^future_evidence:session_date$"):
        validate_bundle(bundle, SCHEMAS)


@pytest.mark.parametrize("invalid_session", [None, "2026-02-30", "not-a-date"])
def test_semantic_validator_reports_invalid_session(price_store, invalid_session):
    ingest_price(price_store, ASSET, {"session_date": "2026-10-24", "close": 10, "fetched_at": "2026-10-25T12:00:00Z"})
    bundle = build_bundle(price_store, ASSET, "2026-10-26T12:00:00Z", SCHEMAS)
    price = next(e for e in bundle["evidence"] if e["record_type"] == "price")
    content = json.loads(price["content"])
    content["session_date"] = invalid_session
    price["content"] = dumps(content)
    price["content_sha256"] = digest(price["content"])
    with pytest.raises(EvidenceError, match="^invalid_price_session_date$"):
        validate_bundle(bundle, SCHEMAS)
