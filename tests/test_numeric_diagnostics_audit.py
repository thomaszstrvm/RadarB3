"""Independent narrow tests for currency aliases and diagnostic-only metadata."""
import pytest

from backend.evidence import EvidenceError, validate_analysis
from backend.validation_diagnostics import diagnostic_for, sanitize_issue
from test_numeric_diagnostics import price_documents
from test_numeric_validator_audit import SCHEMAS


@pytest.mark.parametrize("statement", [
    "Fechamento de £60,05 reais.",
    "Fechamento de ¥60,05 reais.",
    "Fechamento de GBP 60,05 reais.",
    "Fechamento de gbp 60,05 reais.",
    "Fechamento de Gbp 60,05 reais.",
    "Fechamento de gBp 60,05 BRL.",
])
def test_brl_suffix_cannot_discard_an_explicit_foreign_currency_prefix(statement):
    analysis, bundle = price_documents(statement)
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(analysis, bundle, SCHEMAS)


@pytest.mark.parametrize("statement", [
    "Rendimento de 60,05 reais ao dia.",
    "Fechamento de 60,05 reais US$.",
])
def test_new_currency_alias_does_not_hide_a_continuing_unit_or_currency(statement):
    analysis, bundle = price_documents(statement)
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence") as failure:
        validate_analysis(analysis, bundle, SCHEMAS)
    assert diagnostic_for(failure.value, bundle)["issues"][0]["numeric_reason"] == "unsupported_format"


def test_existing_currency_prefix_can_still_precede_ordinary_prose():
    analysis, bundle = price_documents("Fechamento de R$ 60,05 observado.")
    assert validate_analysis(analysis, bundle, SCHEMAS)


@pytest.mark.parametrize("statement", ["Fechamento foi R$ 60,05.", "Fechamento FOI R$ 60,05."])
def test_new_suffix_guard_does_not_reclassify_prose_before_the_existing_prefix(statement):
    analysis, bundle = price_documents(statement)
    assert validate_analysis(analysis, bundle, SCHEMAS)


@pytest.mark.parametrize("returned", ["PRIVATE_SENTINEL", {"private": "PRIVATE_SENTINEL"}, None])
def test_arbitrary_classifier_result_cannot_change_rejection_or_leak(monkeypatch, returned):
    analysis, bundle = price_documents("Fechamento de R$ 998877.")
    monkeypatch.setattr("backend.evidence.numeric_rejection_reason", lambda *_: returned)
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence") as failure:
        validate_analysis(analysis, bundle, SCHEMAS)
    issue = diagnostic_for(failure.value, bundle)["issues"][0]
    assert "numeric_reason" not in issue
    assert "PRIVATE" not in str(issue)


def test_accepted_quantity_does_not_run_the_diagnostic_classifier(monkeypatch):
    analysis, bundle = price_documents("Fechamento de 60,05 reais.")
    def forbidden(*_):
        pytest.fail("A rejection classifier ran for an accepted quantity")
    monkeypatch.setattr("backend.evidence.numeric_rejection_reason", forbidden)
    assert validate_analysis(analysis, bundle, SCHEMAS)


def test_numeric_reason_sanitizer_is_idempotent_and_never_backfills_history():
    historical = {"code": "factual_number_not_in_cited_evidence", "path": "/claims/3/statement", "rule": None}
    assert sanitize_issue(sanitize_issue(historical)) == historical
    approved = dict(historical, numeric_reason="unit_mismatch")
    assert sanitize_issue(sanitize_issue(approved)) == approved
