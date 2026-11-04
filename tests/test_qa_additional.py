"""Isolated QA additions and regressions for the three corrected defects.

No operational database, configuration, network provider or model is invoked.
The suite-wide subprocess guard remains active for every test here.
"""
import json
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.cli_process import CLIError
from backend.cli_runner import AnalysisRunner
from backend.core import Conflict, QuotaExceeded, Store, dumps
from backend.evidence import build_bundle, save_analysis, validate_bundle
from backend.ingest import ingest_article, ingest_price, upsert_catalog
from backend.timeutil import publication_in_window
from test_cli_runner import encode_events, setup_runner  # Isolated fake executor fixture.


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
AS_OF = "2026-10-26T12:00:00Z"
ASSET = "brapi:TEST3"


@pytest.fixture
def qa_store(tmp_path):
    store = Store(tmp_path / "qa.sqlite3")
    upsert_catalog(store, [{"symbol": "TEST3", "name": "Synthetic QA only", "assetType": "stock"}], fetched_at="2026-10-01T12:00:00Z")
    return store


@pytest.mark.parametrize("published,precision,included", [
    ("2026-07-26T03:00:00Z", "instant", True),
    ("2026-07-26T02:59:59.999999Z", "instant", False),
    (AS_OF, "instant", True),
    ("2026-10-26T12:00:00.000001Z", "instant", False),
    ("2026-07-26", "date", True),
    ("2026-07-25", "date", False),
    ("2026-10-27", "date", False),
])
def test_window_inclusive_edges_and_precision(published, precision, included):
    assert publication_in_window(published, precision, AS_OF) is included


@pytest.mark.parametrize("market_timestamp", [None, "2026-10-23T20:00:00Z"])
def test_future_session_must_not_enter_validated_bundle(qa_store, market_timestamp):
    with pytest.raises(ValueError, match="^future_session_date$"):
        ingest_price(qa_store, ASSET, {
            "session_date": "2026-10-27", "close": 999,
            "market_timestamp": market_timestamp, "fetched_at": "2026-10-25T12:00:00Z",
        })
    bundle = build_bundle(qa_store, ASSET, AS_OF, SCHEMAS)
    prices = [json.loads(e["content"]) for e in bundle["evidence"] if e["record_type"] == "price"]
    assert validate_bundle(bundle, SCHEMAS) is True
    observed = {"ingest_status": "rejected_future_session", "as_of": bundle["as_of"], "price_sessions": [p["session_date"] for p in prices], "market_timestamp": market_timestamp}
    print("QA_OBSERVED " + dumps(observed))
    assert not prices, observed


def test_analysis_state_reflects_saved_analysis(qa_store, monkeypatch):
    monkeypatch.setattr("backend.api.ai_status", lambda: {"enabled": False, "reason": "isolated QA"})
    bundle = build_bundle(qa_store, ASSET, AS_OF, SCHEMAS)
    analysis = {
        "schema_version": "1.0", "analysis_id": "qa-abstention", "as_of": bundle["as_of"],
        "asset_id": ASSET, "status": "insufficient_data", "synthesis": "Dados insuficientes.",
        "claims": [], "scenarios": [],
        "opportunity": {"classification": "insufficient_data", "rationale": "Sem evidências suficientes.", "evidence_ids": []},
        "risks": [], "missing_evidence": ["Notícias"], "data_quality_flags": [], "evidence_ids": [],
    }
    save_analysis(qa_store, analysis, bundle, "synthetic-no-model", "qa-prompt", "qa-schema", SCHEMAS)
    # Invoke the actual query handlers without binding a socket or ASGI transport.
    app = create_app(qa_store.path)
    handlers = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    row = handlers["/api/v1/assets"](search="", watchlist=False, sector="", type="", quality="", state="analysed", sort="ticker", page=1, page_size=50)["items"][0]
    detail = handlers["/api/v1/assets/{asset_id}"](ASSET)
    assert detail["analysis"]["analysis_id"] == "qa-abstention"
    observed = {"analysis_present": True, "stored_status": detail["analysis"]["status"], "list_state": row["analysis_state"], "detail_state": detail["analysis_state"]}
    print("QA_OBSERVED " + dumps(observed))
    assert row["analysis_state"] == detail["analysis_state"] == "insufficient_data", observed


def test_repair_cannot_exceed_last_daily_slot(setup_runner):
    runner, bundle, calls, *_ = setup_runner
    runner.store.set_setting("max_analyses_per_day", 1)

    def invalid_executor(*args, **kwargs):
        calls.append("synthetic-invalid-output")
        return encode_events({"bad": "schema"})

    runner.executor = invalid_executor
    with pytest.raises(QuotaExceeded, match="local_request_budget_exhausted"):
        runner.run(bundle)
    assert calls == ["synthetic-invalid-output"]
    assert runner.store.rows("SELECT status FROM quota_ledger") == [{"status": "consumed"}]
    assert len(runner.store.rows("SELECT * FROM analysis_runs")) == 1
    assert runner.store.rows("SELECT * FROM analyses") == []


def test_alert_http_exposes_stored_body(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.api.ai_status", lambda: {"enabled": False, "reason": "isolated QA"})
    app = create_app(tmp_path / "alerts.sqlite3")
    body = "Mensagem sintética de alerta para verificar o contrato."
    with app.state.store.transaction() as con:
        con.execute("INSERT INTO alerts VALUES(?,?,?,?,?,?,?,?,?)", ("qa-alert", "qa-dedupe", None, "source_failure", "Alerta sintético", body, AS_OF, 0, 0))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.get("/api/v1/alerts")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["body"] == body
        print("QA_OBSERVED " + dumps({"alert_body_preserved": True, "message_field_present": "message" in item}))


def test_uncertain_attempt_stays_blocked_in_new_runner(setup_runner):
    runner, bundle, calls, config, report, _ = setup_runner

    def timeout_executor(*args, **kwargs):
        calls.append("synthetic-timeout")
        raise CLIError("cli_timeout_usage_uncertain", uncertain=True)

    runner.executor = timeout_executor
    with pytest.raises(CLIError, match="timeout"):
        runner.run(bundle)
    restarted = AnalysisRunner(Store(runner.store.path), schema_dir=SCHEMAS, prompt_dir=ROOT / "prompts", config_path=config, report_path=report, executor=timeout_executor, mutex_factory=nullcontext)
    with pytest.raises(CLIError, match="previous_attempt_requires_review"):
        restarted.run(bundle)
    assert calls == ["synthetic-timeout"]
    assert restarted.store.rows("SELECT status FROM quota_ledger") == [{"status": "uncertain"}]
    assert restarted.store.rows("SELECT * FROM analyses") == []


def test_backup_restores_committed_wal_and_excludes_pending_write(tmp_path):
    source = Store(tmp_path / "wal-source.sqlite3", wal=True)
    # Pin an earlier snapshot so committed new pages must remain in the WAL.
    keeper = source.connect()
    writer = None
    try:
        keeper.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        keeper.execute("BEGIN")
        assert keeper.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
        upsert_catalog(source, [{"symbol": "TEST3", "name": "Synthetic WAL QA", "assetType": "stock"}], fetched_at="2026-10-01T12:00:00Z")
        ingest_article(source, {"url": "https://finance.yahoo.com/news/qa-wal", "title": "TEST3 evento sintético", "content": "Conteúdo sintético para backup.", "published_at": "2026-10-24T12:00:00Z", "fetched_at": "2026-10-25T12:00:00Z", "timestamp_precision": "instant", "access_level": "excerpt"}, {"id": "fin_yahoo", "allowed_hostnames": ["finance.yahoo.com"], "publisher_group": "yahoo"}, AS_OF)
        ingest_price(source, ASSET, {"session_date": "2026-10-23", "close": 10, "market_timestamp": "2026-10-23T20:00:00Z", "fetched_at": "2026-10-25T12:00:00Z"})
        build_bundle(source, ASSET, AS_OF, SCHEMAS)
        source.enqueue("demo_task", dedupe_key="qa-wal-job")
        reservation = source.reserve_quota("synthetic", "2026-10-26", 1, "qa-wal-reservation")
        source.settle_quota(reservation, "uncertain")
        wal_file = Path(str(source.path) + "-wal")
        assert wal_file.stat().st_size > 0
        assert source.rows("PRAGMA journal_mode")[0]["journal_mode"] == "wal"
        writer = source.connect()
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE assets SET name='UNCOMMITTED QA' WHERE asset_id=?", (ASSET,))
        backup = source.backup(tmp_path / "restored.sqlite3")
        restored = Store(backup, wal=True)
        tables = ("assets", "asset_aliases", "articles", "article_revisions", "price_bars", "price_revisions", "events", "event_versions", "evidence_bundles", "jobs", "quota_ledger")
        for table in tables:
            assert restored.rows(f"SELECT * FROM {table} ORDER BY rowid") == source.rows(f"SELECT * FROM {table} ORDER BY rowid"), table
        assert restored.rows("SELECT name FROM assets")[0]["name"] == "Synthetic WAL QA"
        assert restored.rows("PRAGMA integrity_check") == [{"integrity_check": "ok"}]
        assert restored.rows("PRAGMA foreign_key_check") == []
        with pytest.raises(Conflict, match="backup_requires_new_destination"):
            source.backup(backup)
    finally:
        if writer is not None:
            writer.rollback()
            writer.close()
        keeper.rollback()
        keeper.close()
