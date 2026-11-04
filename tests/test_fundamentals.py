"""Isolated local fixtures only. No network, real database, worker or LLM."""
import copy
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from backend import fundamentals as f
from backend.core import Store, digest, dumps
from backend.evidence import build_bundle, validate_bundle, validate_analysis, EvidenceError
from backend.ingest import upsert_catalog, ingest_article
from backend.numeric_support import evidence_quantities, numbers_supported
from backend.timeutil import iso
from test_cli_runner import final_analysis

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "docs/qa-20261031/data-readiness/fundamentals"
MANIFEST = STAGE / "vale3-3t26-reviewed-v1.json"
PDF = STAGE / "vale-3t26-earnings-release.pdf"
SCHEMAS = ROOT / "schemas"
ASSET = "brapi:VALE3"
IMPORTED = "2026-10-31T06:00:00.000000Z"
BEFORE = "2026-10-31T05:59:59.000000Z"


def document():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(f, "iso", lambda: IMPORTED)
    value = Store(tmp_path / "isolated-demo.sqlite3", "demo")
    upsert_catalog(value, [{"symbol": "VALE3", "name": "Vale S.A.", "assetType": "stock"}], fetched_at="2026-10-30T12:00:00Z")
    return value


def import_one(store):
    with store.connect() as con:
        return f.import_fundamentals(con, document(), PDF)


def test_reviewed_coefficients_and_source_pdf_are_fixed():
    doc = f.validate_manifest(document())
    f.verify_pdf(PDF, doc)
    assert digest(doc) == f.MAPPING_SHA256
    assert doc["document_type"] == "earnings_release"
    assert doc["review_role"] == "development_agent_independent_review"
    assert len(doc["observations"]) == 7
    assert len({o["account"] for o in doc["observations"]}) == 4
    assert [Decimal(o["value"]) for o in doc["observations"]] == list(map(Decimal, ["10599", "30355", "1255", "4523", "2519", "6810", "4841"]))
    assert all(o["scale"] == 1_000_000 and o["currency"] == "USD" for o in doc["observations"])


@pytest.mark.parametrize("path,value", [
    (("document_type",), "ITR"), (("mapping_version",), "2.0"),
    (("review_reference",), "self_approved"), (("issuer", "cnpj"), "00.000.000/0001-00"),
    (("asset", "asset_id"), "brapi:PETR4"), (("source", "document_url"), "https://attacker.invalid/doc"),
    (("source", "pdf_sha256"), "a" * 64), (("publication", "date"), "2026-10-28"),
    (("observations", 0, "value"), "10598"), (("observations", 0, "value"), "10599000000"),
    (("observations", 0, "value"), 10599), (("observations", 0, "value"), True),
    (("observations", 0, "value"), "NaN"), (("observations", 0, "value"), "10.599"),
    (("observations", 0, "currency"), "BRL"), (("observations", 0, "scale"), 1),
    (("observations", 0, "location", "pdf_page"), 25),
    (("observations", 0, "period", "start"), "2026-01-01"),
    (("observations", 0, "period", "end"), "2026-12-31"),
])
def test_changed_map_cannot_self_attest(path, value):
    doc = document(); target = doc
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValueError, match="fundamentals_"):
        f.validate_manifest(doc)


def test_missing_or_duplicate_observations_refused():
    doc = document(); doc["observations"][0]["value"] = None
    with pytest.raises(ValueError, match="null_requires_reason"):
        f.validate_manifest(doc)
    doc["observations"][0]["missing_reason"] = "not_reported"
    with pytest.raises(ValueError, match="mapping_not_reviewed"):
        f.validate_manifest(doc)  # A new missing value also needs a reviewed map.
    doc = document(); doc["observations"][1] = copy.deepcopy(doc["observations"][0])
    with pytest.raises(ValueError, match="duplicate_observation"):
        f.validate_manifest(doc)


def test_changed_pdf_rolls_back_without_creating_table(store, tmp_path):
    altered = tmp_path / "changed.pdf"
    raw = bytearray(PDF.read_bytes()); raw[-12] ^= 1; altered.write_bytes(raw)
    with store.connect() as con:
        before = list(con.iterdump())
        with pytest.raises(ValueError, match="pdf_hash_mismatch"):
            f.import_fundamentals(con, document(), altered)
        assert list(con.iterdump()) == before


def test_import_replay_and_immutable_rows(store, monkeypatch):
    first = import_one(store)
    assert first["inserted"] == 1 and first["available_at"] == IMPORTED
    monkeypatch.setattr(f, "iso", lambda: "2026-11-01T06:00:00.000000Z")
    again = import_one(store)
    assert again["unchanged"] == 1 and again["available_at"] == IMPORTED
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM fundamental_releases").fetchone()[0] == 1
        for sql in ("UPDATE fundamental_releases SET available_at='2000-01-01'", "DELETE FROM fundamental_releases"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable_record"):
                con.execute(sql)
    assert len(store.rows("SELECT * FROM audit_events WHERE event_type='fundamentals_imported'")) == 1


def test_failure_after_insert_rolls_back_ddl_rows_and_audit(store):
    with store.connect() as con:
        con.execute("CREATE TRIGGER fail_fundamentals_audit BEFORE INSERT ON audit_events WHEN NEW.event_type='fundamentals_imported' BEGIN SELECT RAISE(ABORT,'fixture_failure'); END")
        con.commit()
        before = list(con.iterdump())
        with pytest.raises(sqlite3.IntegrityError, match="fixture_failure"):
            f.import_fundamentals(con, document(), PDF)
        assert list(con.iterdump()) == before
        assert not con.in_transaction
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'fundamental_releases%'").fetchall()


def test_real_profile_refused_without_any_mutation(tmp_path):
    isolated = Store(tmp_path / "isolated-real-profile.sqlite3", "real")
    with isolated.connect() as con:
        before = list(con.iterdump())
        with pytest.raises(ValueError, match="profile_mismatch"):
            f.import_fundamentals(con, document(), PDF)
        assert list(con.iterdump()) == before


@pytest.mark.parametrize("profile", ["demo", "REAL", "unknown", "", None, True])
def test_profile_is_explicit_and_must_match_real_fixture(tmp_path, profile):
    isolated = Store(tmp_path / "profile-test.sqlite3", "real")
    with isolated.connect() as con:
        before = list(con.iterdump())
        with pytest.raises(ValueError, match="fundamentals_(invalid_profile|profile_mismatch)"):
            f.import_fundamentals(con, document(), PDF, expected_profile=profile)
        assert list(con.iterdump()) == before


def test_explicit_real_import_replay_preserves_preexisting_tables(tmp_path):
    isolated = Store(tmp_path / "explicit-real-fixture.sqlite3", "real")
    upsert_catalog(isolated, [{"symbol": "VALE3", "name": "Vale S.A.", "assetType": "stock"}])
    cutoff = iso()
    baseline = build_bundle(isolated, ASSET, cutoff, SCHEMAS)
    with isolated.connect() as con:
        tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        before = {name: list(con.execute(f'SELECT * FROM "{name}"')) for name in tables}
        result = f.import_fundamentals(con, document(), PDF, expected_profile="real")
        again = f.import_fundamentals(con, document(), PDF, expected_profile="real")
        assert result["inserted"] == 1 and again["unchanged"] == 1
        assert result["available_at"] == again["available_at"]
        for name in tables:
            after = list(con.execute(f'SELECT * FROM "{name}"'))
            if name == "audit_events":
                assert after[:-1] == before[name]
                assert after[-1][1] == "fundamentals_imported"
            else:
                assert after == before[name]
    assert build_bundle(isolated, ASSET, cutoff, SCHEMAS) == baseline


def test_explicit_real_failure_rolls_back_ddl(tmp_path):
    isolated = Store(tmp_path / "real-rollback-fixture.sqlite3", "real")
    upsert_catalog(isolated, [{"symbol": "VALE3", "assetType": "stock"}])
    with isolated.connect() as con:
        con.execute("CREATE TRIGGER fail_real_audit BEFORE INSERT ON audit_events WHEN NEW.event_type='fundamentals_imported' BEGIN SELECT RAISE(ABORT,'fixture_failure'); END")
        con.commit(); before = list(con.iterdump())
        with pytest.raises(sqlite3.IntegrityError, match="fixture_failure"):
            f.import_fundamentals(con, document(), PDF, expected_profile="real")
        assert list(con.iterdump()) == before


def test_real_profile_option_cannot_override_demo_database(store):
    with store.connect() as con:
        before = list(con.iterdump())
        with pytest.raises(ValueError, match="profile_mismatch"):
            f.import_fundamentals(con, document(), PDF, expected_profile="real")
        assert list(con.iterdump()) == before


@pytest.mark.parametrize("busy", ["worker", "job"])
def test_explicit_real_still_requires_inactivity(tmp_path, busy):
    isolated = Store(tmp_path / "real-busy-fixture.sqlite3", "real")
    upsert_catalog(isolated, [{"symbol": "VALE3", "assetType": "stock"}])
    if busy == "worker":
        isolated.acquire_worker("fixture", ttl=90, execution_mode="collection-only")
    else:
        job = isolated.enqueue("analysis", {"asset_id": ASSET})
        with isolated.transaction() as con:
            con.execute("UPDATE jobs SET status='running' WHERE job_id=?", (job["job_id"],))
    with isolated.connect() as con:
        before = list(con.iterdump())
        with pytest.raises(ValueError, match="fundamentals_(worker|job)_active"):
            f.import_fundamentals(con, document(), PDF, expected_profile="real")
        assert list(con.iterdump()) == before


def test_interruption_rolls_back_ddl_too(store):
    with store.connect() as con:
        class InterruptedConnection:
            def __getattr__(self, name):
                return getattr(con, name)

            def execute(self, statement, *args):
                if statement.startswith("INSERT INTO audit_events"):
                    raise KeyboardInterrupt
                return con.execute(statement, *args)

        before = list(con.iterdump())
        with pytest.raises(KeyboardInterrupt):
            f.import_fundamentals(InterruptedConnection(), document(), PDF)
        assert list(con.iterdump()) == before and not con.in_transaction


@pytest.mark.parametrize("busy", ["worker", "job"])
def test_inactive_database_required(store, busy):
    if busy == "worker":
        store.acquire_worker("test-owner", now=IMPORTED, execution_mode="collection-only")
    else:
        job = store.enqueue("analysis", {"asset_id": ASSET})
        with store.transaction() as con:
            con.execute("UPDATE jobs SET status='running' WHERE job_id=?", (job["job_id"],))
    with store.connect() as con:
        before = list(con.iterdump())
        with pytest.raises(ValueError, match="fundamentals_(worker|job)_active"):
            f.import_fundamentals(con, document(), PDF)
        assert list(con.iterdump()) == before


def test_existing_transaction_remains_owned_by_caller(store):
    with store.connect() as con:
        con.execute("BEGIN")
        con.execute("INSERT INTO settings VALUES('pending_test','true',1,?)", (IMPORTED,))
        with pytest.raises(ValueError, match="existing_transaction"):
            f.import_fundamentals(con, document(), PDF)
        assert con.in_transaction
        assert con.execute("SELECT value_json FROM settings WHERE key='pending_test'").fetchone()[0] == "true"
        con.rollback()


def test_fetch_clock_cannot_be_used_before_actual_availability(store, monkeypatch):
    monkeypatch.setattr(f, "iso", lambda: "2026-10-30T06:00:00.000000Z")
    with store.connect() as con, pytest.raises(ValueError, match="future_fetch"):
        f.import_fundamentals(con, document(), PDF)
    assert not store.rows("SELECT name FROM sqlite_master WHERE name='fundamental_releases'")


def test_absent_table_and_ineligible_rows_preserve_materials_and_cache(store):
    baseline = build_bundle(store, ASSET, BEFORE, SCHEMAS)
    assert baseline["coverage"]["known_gaps"][0] == "Fundamentos e exposição econômica ainda não verificados"
    assert baseline["quality_flags"] == ["link_by_explicit_ticker_only", "not_a_complete_news_archive", "no_automatic_causal_validation"]
    assert not store.rows("SELECT name FROM sqlite_master WHERE name='fundamental_releases'")
    prior_snapshot = store.rows("SELECT * FROM evidence_bundles")
    import_one(store)
    assert build_bundle(store, ASSET, BEFORE, SCHEMAS) == baseline
    assert store.rows("SELECT * FROM evidence_bundles") == prior_snapshot
    after = build_bundle(store, ASSET, IMPORTED, SCHEMAS)
    assert after["bundle_id"] != baseline["bundle_id"]
    assert store.rows("SELECT payload_json FROM evidence_bundles WHERE bundle_id=?", (baseline["bundle_id"],))[0]["payload_json"] == dumps(baseline)
    assert after["coverage"]["editorial_sources_reached"] == 0
    assert after["coverage"]["full_text_count"] == 0 and after["coverage"]["partial_text_count"] == 0
    assert "limited_reviewed_fundamentals" in after["quality_flags"]
    assert after["evidence"][0]["first_seen_at"] == IMPORTED
    assert after["evidence"][0]["fetched_at"] == document()["source"]["fetched_at"]


def test_window_publication_is_separate_from_accounting_period(store):
    import_one(store)
    assert len(f.filings_for_bundle(store, ASSET, IMPORTED)) == 1  # YTD starts January: valid.
    assert f.filings_for_bundle(store, ASSET, BEFORE) == []
    assert f.filings_for_bundle(store, "brapi:PETR4", IMPORTED) == []
    assert len(f.filings_for_bundle(store, ASSET, "2027-01-29T15:00:00.000000Z")) == 1
    assert f.filings_for_bundle(store, ASSET, "2027-01-30T15:00:00.000000Z") == []


@pytest.mark.parametrize("text,allowed", [
    ("Receita de US$ 10599 milhões.", True), ("CFO de USD 2519 mi.", True),
    ("Caixa de US$ 4841 milhões.", True), ("Receita de US$ 10.599 milhões.", False),
    ("Receita de US$ 10599000000.", False), ("Receita de US$ 10,599 bilhões.", False),
    ("Receita de R$ 10599 milhões.", False), ("Receita de US$ -10599 milhões.", False),
    ("Receita de 10599.", False), ("Receita de US$ 10599 milhões por dia.", False),
    ("Resultado de 2026.", False), ("Resultado de 24.", False), ("Resultado de 4170.", False),
    ("Resultado de 1000000.", False), ("Resultado em 2026-09-30.", False),
])
def test_numeric_support_only_from_typed_financial_coefficients(store, text, allowed):
    import_one(store)
    filing = f.filings_for_bundle(store, ASSET, IMPORTED)[0]
    assert numbers_supported(text, [filing]) is allowed
    assert len(evidence_quantities(filing)) == 7


@pytest.mark.parametrize("field,value", [("record_type", "article"), ("source_id", "fake"), ("url", None), ("entity_ids", ["brapi:PETR4"]), ("available_at", BEFORE), ("fetched_at", BEFORE)])
def test_bundle_envelope_cannot_mislabel_reviewed_content(store, field, value):
    import_one(store)
    bundle = build_bundle(store, ASSET, IMPORTED, SCHEMAS)
    bundle["evidence"][0][field] = value
    with pytest.raises(EvidenceError, match="fundamentals_evidence_invalid"):
        validate_bundle(bundle, SCHEMAS)


def test_budget_keeps_filing_and_fails_before_persisting_if_it_cannot_fit(store):
    source = {"id": "fixture", "allowed_hostnames": ["example.com"], "publisher_group": "fixture"}
    ingest_article(store, {"url": "https://example.com/fixture", "title": "VALE3 fixture", "content": "Contexto. " * 2000, "access_level": "excerpt", "published_at": "2026-10-30T12:00:00Z", "fetched_at": "2026-10-30T13:00:00Z", "timestamp_precision": "instant"}, source, BEFORE)
    import_one(store)
    bundle = build_bundle(store, ASSET, IMPORTED, SCHEMAS, max_characters=15000)
    assert [e["evidence_id"] for e in bundle["evidence"]] == [f.RELEASE_ID]
    assert bundle["coverage"]["editorial_sources_reached"] == 0
    before = store.rows("SELECT * FROM evidence_bundles")
    with pytest.raises(EvidenceError, match="bundle_size_limit"):
        build_bundle(store, ASSET, IMPORTED, SCHEMAS, max_characters=1000)
    assert store.rows("SELECT * FROM evidence_bundles") == before


@pytest.mark.parametrize("action,classification", [("buy", "investigate"), ("sell", "avoid_for_now")])
def test_reviewed_fundamentals_do_not_enable_direction(store, action, classification):
    import_one(store)
    bundle = build_bundle(store, ASSET, IMPORTED, SCHEMAS)
    value = final_analysis({"schema_version": "1.1", "analysis_id": "fixture", "evidence_bundle": bundle})
    value["status"] = "supported_hypothesis"
    value["opportunity"].update(classification=classification, evidence_ids=[f.RELEASE_ID])
    value["signal"].update(action=action, evidence_ids=[f.RELEASE_ID])
    value["evidence_ids"] = [f.RELEASE_ID]
    with pytest.raises(EvidenceError, match="signal_directional_data_unverified"):
        validate_analysis(value, bundle, SCHEMAS)


def test_fact_can_cite_filing_but_new_numbers_remain_rejected(store):
    import_one(store)
    bundle = build_bundle(store, ASSET, IMPORTED, SCHEMAS)
    value = final_analysis({"schema_version": "1.1", "analysis_id": "fixture", "evidence_bundle": bundle})
    value["claims"] = [{"kind": "fact", "statement": "Receita de US$ 10599 milhões.", "evidence_ids": [f.RELEASE_ID]}]
    value["evidence_ids"] = [f.RELEASE_ID]
    assert validate_analysis(value, bundle, SCHEMAS)
    value["claims"][0]["statement"] = "Receita de US$ 10599 bilhões."
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(value, bundle, SCHEMAS)


@pytest.mark.parametrize("extra", ["milhões", "de milhões", "de bilhões", "de milhão", "de bilhão", "DE MILHÕES"])
def test_compound_scale_is_not_one_scale_in_integrated_analysis(store, extra):
    import_one(store)
    bundle = build_bundle(store, ASSET, IMPORTED, SCHEMAS)
    value = final_analysis({"schema_version": "1.1", "analysis_id": "fixture", "evidence_bundle": bundle})
    value["claims"] = [{"kind": "fact", "statement": f"Receita de US$ 10599 milhões {extra}.", "evidence_ids": [f.RELEASE_ID]}]
    value["evidence_ids"] = [f.RELEASE_ID]
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(value, bundle, SCHEMAS)


@pytest.mark.parametrize("first,second", [("milhão", "milhão"), ("bilhão", "milhão"), ("mil", "milhões"), ("bi", "mi"), ("milhões", "bilhões")])
@pytest.mark.parametrize("connector", [" ", " de "])
def test_compound_scale_is_refused_in_legacy_articles(first, second, connector):
    source = {"record_type": "article", "content": f"Valor de US$ 5 {first}."}
    assert not numbers_supported(f"Valor de US$ 5 {first}{connector}{second}.", [source])


@pytest.mark.parametrize("suffix", [" em receita.", " de investimentos.", " de milho.", ". Milhões de consumidores podem ser afetados.", ", segundo o relatório."])
def test_single_scale_with_ordinary_prose_remains_supported(suffix):
    source = {"record_type": "article", "content": "Valor de US$ 5 milhões."}
    assert numbers_supported("Valor de US$ 5 milhões" + suffix, [source])


def test_cli_existing_demo_only_and_missing_destination(store, tmp_path):
    before = iso()
    args = [sys.executable, "-B", str(ROOT / "scripts/import_fundamentals.py"), "--db", str(store.path), "--manifest", str(MANIFEST), "--pdf", str(PDF)]
    completed = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert before <= result["available_at"] <= iso()
    missing = tmp_path / "missing.sqlite3"
    args[args.index("--db") + 1] = str(missing)
    completed = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 2 and not missing.exists()
    assert str(missing) not in completed.stderr


def test_cli_real_requires_matching_explicit_profile(tmp_path):
    isolated = Store(tmp_path / "cli-real-fixture.sqlite3", "real")
    upsert_catalog(isolated, [{"symbol": "VALE3", "assetType": "stock"}])
    args = [sys.executable, "-B", str(ROOT / "scripts/import_fundamentals.py"), "--db", str(isolated.path), "--manifest", str(MANIFEST), "--pdf", str(PDF)]
    refused = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert refused.returncode == 2 and "profile_mismatch" in refused.stderr
    assert not isolated.rows("SELECT name FROM sqlite_master WHERE name='fundamental_releases'")
    accepted = subprocess.run([*args, "--profile", "real"], capture_output=True, text=True, timeout=30)
    assert accepted.returncode == 0, accepted.stderr
    assert json.loads(accepted.stdout)["inserted"] == 1
