"""HTTP regression checks for persisted analysis state, using only tmp_path."""
import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.core import dumps
from backend.evidence import build_bundle, save_analysis, validate_analysis
from backend.ingest import ingest_article, upsert_catalog
from backend.timeutil import iso

ROOT = Path(__file__).resolve().parents[1]
ASSET = "brapi:TEST3"
STATUSES = ("insufficient_data", "inconclusive", "supported_hypothesis")


@pytest.fixture
def client(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("This regression test must not call a provider or model")

    monkeypatch.setattr("backend.api.ai_status", lambda: {"enabled": False, "reason": "isolated test"})
    monkeypatch.setattr("backend.safe_http.SafeHTTP.get", forbidden)
    monkeypatch.setattr("backend.cli_runner.AnalysisRunner.run", forbidden)
    app = create_app(tmp_path / "analysis-state.sqlite3", profile="real")
    upsert_catalog(app.state.store, [{"symbol": "TEST3", "name": "Synthetic QA asset", "assetType": "stock"}],
                   fetched_at="2026-10-01T12:00:00Z")
    with TestClient(app, base_url="http://127.0.0.1:8000") as http:
        yield http


def persist_analysis(store, analysis_id, status, *, as_of="2026-10-26T12:00:00Z", created_at=None):
    """Create schema-valid synthetic output without invoking any model."""
    source = {"id": "fin_yahoo", "allowed_hostnames": ["finance.yahoo.com"], "publisher_group": "yahoo"}
    ingest_article(store, {"url": "https://finance.yahoo.com/news/synthetic-analysis-state-test",
                          "title": "TEST3: Synthetic QA event", "content": "Synthetic QA event only.",
                          "published_at": "2026-10-20T12:00:00Z", "fetched_at": "2026-10-21T12:00:00Z",
                          "access_level": "excerpt"}, source, as_of)
    bundle = build_bundle(store, ASSET, as_of, ROOT / "schemas")
    refs = [bundle["evidence"][0]["evidence_id"]]
    analysis = {"schema_version": "1.0", "analysis_id": analysis_id, "as_of": bundle["as_of"],
                "asset_id": ASSET, "status": status, "synthesis": "Synthetic QA only.",
                "claims": [{"kind": "fact", "statement": "Synthetic QA event only.", "evidence_ids": refs}],
                "scenarios": [{"case": "conditional_case", "horizon_days": 30, "description": "Synthetic scenario.",
                               "conditions": ["Synthetic condition"], "invalidation_conditions": ["Synthetic invalidation"],
                               "evidence_ids": refs}] if status == "supported_hypothesis" else [],
                "opportunity": {"classification": "insufficient_data" if status == "insufficient_data" else "watch",
                                "rationale": "Synthetic QA output", "evidence_ids": refs},
                "risks": ["Synthetic QA risk"], "missing_evidence": [], "data_quality_flags": [], "evidence_ids": refs}
    if created_at is None:
        save_analysis(store, analysis, bundle, "synthetic-no-model", analysis_id, "test-schema", ROOT / "schemas")
    else:
        # Exact ties cannot be obtained reliably from the wall clock. Validate
        # the same contract before inserting the explicitly controlled timestamp.
        validate_analysis(analysis, bundle, ROOT / "schemas", analysis_id)
        with store.transaction() as con:
            con.execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (analysis_id, ASSET, bundle["bundle_id"], bundle["as_of"], "synthetic-no-model", analysis_id,
                         "test-schema", status, dumps(analysis), iso(created_at)))
    return analysis


@pytest.mark.parametrize("status", STATUSES)
def test_saved_status_agrees_across_asset_detail_filters_and_analysis_exports(client, status):
    saved = persist_analysis(client.app.state.store, "qa-saved", status)
    row = client.get("/api/v1/assets").json()["items"][0]
    detail = client.get(f"/api/v1/assets/{ASSET}").json()
    assert row["analysis_state"] == detail["analysis_state"] == detail["analysis"]["status"] == status
    assert detail["analysis"]["analysis_id"] == saved["analysis_id"]
    for state in (status, "analysed"):
        filtered = client.get("/api/v1/assets", params={"state": state}).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["analysis_state"] == status
    assert client.get("/api/v1/assets", params={"state": "indisponível"}).json()["total"] == 0
    for other in set(STATUSES) - {status}:
        assert client.get("/api/v1/assets", params={"state": other}).json()["total"] == 0
    exported = client.get("/api/v1/exports/analyses", params={"format": "json", "search": "TEST3"}).json()["items"]
    assert exported[0]["status"] == row["analysis_state"]
    csv_response = client.get("/api/v1/exports/analyses", params={"format": "csv", "search": "TEST3"})
    assert list(csv.DictReader(io.StringIO(csv_response.text)))[0]["status"] == status


@pytest.mark.parametrize('kind,total', [('unknown',1), ('fund',0)])
def test_overview_analysis_count_matches_navigable_catalogue(client, kind, total):
    store = client.app.state.store
    persist_analysis(store, 'qa-reclassified', 'inconclusive')
    with store.transaction() as con:
        con.execute('UPDATE assets SET eligible=0,asset_type=? WHERE asset_id=?',(kind,ASSET))
    overview = client.get('/api/v1/overview').json()
    assert overview['counts']['analysed'] == 1
    assert overview['market_counts']['analysed'] == client.get('/api/v1/assets?type=all&state=analysed').json()['total'] == total


def test_asset_without_saved_analysis_remains_unavailable_even_with_queued_job(client):
    store = client.app.state.store
    store.enqueue("analyse", {"asset_id": ASSET}, "qa-never-executed")
    row = client.get("/api/v1/assets").json()["items"][0]
    detail = client.get(f"/api/v1/assets/{ASSET}").json()
    assert row["analysis_state"] == detail["analysis_state"] == "indisponível"
    assert detail["analysis"] is None
    assert client.get("/api/v1/assets", params={"state": "indisponível"}).json()["total"] == 1
    assert client.get("/api/v1/assets", params={"state": "analysed"}).json()["total"] == 0
    assert client.get("/api/v1/exports/analyses").json()["items"] == []
    assert store.rows("SELECT * FROM quota_ledger") == []


def test_latest_cutoff_wins_over_later_insertion_of_historical_analysis(client):
    store = client.app.state.store
    latest = persist_analysis(store, "qa-latest", "inconclusive", as_of="2026-10-26T12:00:00Z")
    persist_analysis(store, "qa-historical", "insufficient_data", as_of="2026-10-25T12:00:00Z")
    row = client.get("/api/v1/assets").json()["items"][0]
    detail = client.get(f"/api/v1/assets/{ASSET}").json()
    assert row["analysis_state"] == detail["analysis_state"] == "inconclusive"
    assert detail["analysis"]["analysis_id"] == latest["analysis_id"]
    assert client.get("/api/v1/assets", params={"state": "insufficient_data"}).json()["total"] == 0


def test_equal_cutoffs_use_same_created_time_and_id_tiebreaker_in_list_and_detail(client):
    store = client.app.state.store
    persist_analysis(store, "qa-z-older", "supported_hypothesis", created_at="2026-10-26T12:01:00Z")
    persist_analysis(store, "qa-b-latest", "inconclusive", created_at="2026-10-26T12:02:00Z")
    persist_analysis(store, "qa-a-latest", "insufficient_data", created_at="2026-10-26T12:02:00Z")
    for _ in range(2):
        row = client.get("/api/v1/assets").json()["items"][0]
        detail = client.get(f"/api/v1/assets/{ASSET}").json()
        assert row["analysis_state"] == detail["analysis_state"] == "inconclusive"
        assert detail["analysis"]["analysis_id"] == "qa-b-latest"
    assert client.get("/api/v1/assets", params={"state": "supported_hypothesis"}).json()["total"] == 0
