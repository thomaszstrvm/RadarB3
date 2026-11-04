import json
from decimal import Decimal

import pytest

from backend.core import dumps
from backend.evidence import EvidenceError, validate_analysis
from backend.validation_diagnostics import diagnostic_for, sanitize_issue
from test_numeric_validator_audit import SCHEMAS, documents
from test_cli_runner import setup_runner, encode_events, final_analysis


def price_documents(statement, *, close=60.05, volume=1200):
    return documents(json.dumps({"close": close, "volume": volume, "currency": "BRL", "session_date": "2026-10-29"}),
                     statement, record_type="price")


@pytest.mark.parametrize("statement", ["Fechamento de 60,05 reais.", "Fechamento de 60.05 BRL.", "Fechamento de R$ 60,05 reais.",
                                     "Fechamento de brl 60,05 reais.", "Fechamento foi R$ 60,05.", "Fechamento FOI R$ 60,05."])
def test_explicit_brl_suffix_is_equivalent_to_price_currency(statement):
    value, bundle = price_documents(statement)
    assert validate_analysis(value, bundle, SCHEMAS)


@pytest.mark.parametrize("statement", [
    "Fechamento de US$ 60,05 reais.", "Fechamento de 60,05 reais/kg.",
    "Fechamento de (60,05 reais).", "Fechamento de -60,05 reais.",
    "Fechamento de 60,05 milhões de reais.", "Fechamento de 1200 reais.",
    "Fechamento de 2026 reais.", "Fechamento de 60,05 dólares.",
    "Fechamento de 60,05 reais em 29/10/2026.",
    "Fechamento de 60,05 reais por tonelada.", "Fechamento de 60,05 reais por dia.",
    "Fechamento de 60,05 BRL/USD.", "Fechamento de 60,05 real.",
    "Fechamento de CAD 60,05 reais.", "Fechamento de XYZ 60,05 reais.",
    "Fechamento de ₹60,05 reais.", "Fechamento de CHF R$ 60,05 reais.",
    "Rendimento de 60,05 reais ao dia.", "Fechamento de 60,05 reais US$.",
    "Fechamento de 60,05 reais na sessão.", "Fechamento de 60,05 reais%.",
    "Fechamento foi 60,05 reais.",
])
def test_brl_suffix_does_not_relax_units_signs_dates_or_volume(statement):
    value, bundle = price_documents(statement)
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(value, bundle, SCHEMAS)


@pytest.mark.parametrize("statement,reason", [
    ("PRIVATE_SENTINEL .60 reais.", "unsupported_format"),
    ("Em 29/10/2026, fechamento de R$ 60,05.", "unsupported_date"),
    ("Fechamento de US$ 60,05.", "unit_mismatch"),
    ("Fechamento de R$ 998877.", "quantity_not_found"),
])
def test_numeric_reason_is_a_safe_category_only_after_rejection(statement, reason):
    value, bundle = price_documents(statement)
    with pytest.raises(EvidenceError) as failure:
        validate_analysis(value, bundle, SCHEMAS)
    diagnostic = diagnostic_for(failure.value, bundle)
    issue = diagnostic["issues"][0]
    assert issue["code"] == "factual_number_not_in_cited_evidence"
    assert issue["numeric_reason"] == reason
    assert issue["evidence_ids"] == ["synthetic_cited"]
    assert issue["path"] == "/claims/0/statement"
    serialized = dumps(diagnostic)
    assert "PRIVATE" not in serialized and "998877" not in serialized and "60.05" not in serialized


def test_unknown_numeric_reason_and_reason_on_other_code_are_dropped():
    original = {"code": "factual_number_not_in_cited_evidence", "path": "/claims/0/statement", "numeric_reason": "PRIVATE_998877"}
    assert "numeric_reason" not in sanitize_issue(original)
    original.update(code="schema_invalid", numeric_reason="quantity_not_found")
    assert "numeric_reason" not in sanitize_issue(original)


def test_historical_diagnostic_does_not_gain_an_inferred_reason():
    original = {"code": "factual_number_not_in_cited_evidence", "path": "/claims/3/statement", "rule": None}
    assert "numeric_reason" not in sanitize_issue(original)


def test_diagnostic_failure_does_not_replace_the_original_rejection(monkeypatch):
    value, bundle = price_documents("Fechamento de R$ 998877.")
    def broken(*args):
        raise RuntimeError("PRIVATE_SENTINEL")
    monkeypatch.setattr("backend.evidence.numeric_rejection_reason", broken)
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence") as failure:
        validate_analysis(value, bundle, SCHEMAS)
    diagnostic = diagnostic_for(failure.value, bundle)
    assert "numeric_reason" not in diagnostic["issues"][0]
    assert "PRIVATE" not in dumps(diagnostic)


def test_currency_substrings_are_not_treated_as_explicit_brl():
    from backend.numeric_support import quantities
    assert quantities("60,05 realizado") == [("quantity", Decimal("60.05"), "", "", "")]
    value, bundle = price_documents("Fechamento de 60,05BRLXYZ.")
    with pytest.raises(EvidenceError):
        validate_analysis(value, bundle, SCHEMAS)


def test_numeric_reason_survives_safe_persistence_and_api(setup_runner):
    from backend.api import create_app
    from backend.cli_process import CLIError
    from backend.core import digest
    from backend.timeutil import iso
    from fastapi.testclient import TestClient
    runner, bundle, calls, config, *_ = setup_runner
    runtime = json.loads(config.read_text())
    runtime["max_repair_attempts"] = 0
    config.write_text(dumps(runtime))
    _, source = price_documents("Fechamento de R$ 60,05.")
    item = source["evidence"][0]
    item["entity_ids"] = [bundle["asset"]["asset_id"]]
    for key in ("first_seen_at", "fetched_at", "available_at"):
        item[key] = bundle["as_of"]
    bundle["evidence"] = [item]
    bundle["bundle_id"] = "bundle_" + digest(bundle)[:24]
    with runner.store.transaction() as connection:
        connection.execute("INSERT INTO evidence_bundles VALUES(?,?,?,?,?,?)",
                           (bundle["bundle_id"], bundle["asset"]["asset_id"], bundle["as_of"], digest(bundle), dumps(bundle), iso()))
    job = runner.store.enqueue("analyse", {"asset_id": bundle["asset"]["asset_id"]}, "synthetic-numeric-reason")
    def invalid(*args, **kwargs):
        envelope = json.loads(kwargs["stdin"].decode().rsplit("\n\n", 1)[1])
        value = final_analysis(envelope)
        value["claims"] = [{"kind": "fact", "statement": "PRIVATE_SENTINEL R$ 991122.", "evidence_ids": ["synthetic_cited"]}]
        value["evidence_ids"] = ["synthetic_cited"]
        calls.append(1)
        return encode_events(value)
    runner.executor = invalid
    with pytest.raises(CLIError, match="cli_analysis_validation_rejected"):
        runner.run(bundle, job_id=job["job_id"])
    assert len(calls) == 1
    stored = runner.store.rows("SELECT payload_json FROM audit_events WHERE event_type='analysis_validation'")[0]["payload_json"]
    assert json.loads(stored)["diagnostic"]["issues"][0]["numeric_reason"] == "quantity_not_found"
    assert "PRIVATE" not in stored and "991122" not in stored
    with TestClient(create_app(runner.store.path, profile="real"), base_url="http://127.0.0.1:8000") as client:
        response = client.get("/api/v1/assets/brapi:TEST3/analysis-job").json()
    issue = response["validation_diagnostics"][0]["diagnostic"]["issues"][0]
    assert issue["numeric_reason"] == "quantity_not_found"
    assert issue["evidence_ids"] == ["synthetic_cited"]
    assert "PRIVATE" not in dumps(response) and "991122" not in dumps(response)
