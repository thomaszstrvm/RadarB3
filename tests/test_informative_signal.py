import copy
import json
import shutil

import pytest

from backend.cli_process import CLIError
from backend.core import dumps
from backend.evidence import EvidenceError, validate_analysis
from backend.validation_diagnostics import diagnostic_for
from test_cli_runner import ROOT, encode_events, final_analysis, setup_runner
from test_numeric_validator_audit import documents


def signal_document():
    _, bundle = documents("Fechamento 41.7. A empresa divulgou resultado.", "Fechamento 41,70.")
    value = final_analysis({"analysis_id": "synthetic_analysis", "schema_version": "1.1", "evidence_bundle": bundle})
    return value, bundle


def test_legacy_analysis_stays_valid_and_unmodified():
    value, bundle = documents("Fechamento 41.7.", "Fechamento 41,70.")
    before = copy.deepcopy(value)
    assert validate_analysis(value, bundle, ROOT / "schemas")
    assert value == before and "signal" not in value


def test_insufficient_data_is_a_valid_informative_result():
    value, bundle = signal_document()
    assert validate_analysis(value, bundle, ROOT / "schemas", expected_schema_version="1.1")


def test_wait_requires_substantive_cited_content_and_exact_union():
    value, bundle = signal_document()
    value["status"] = "inconclusive"
    value["opportunity"].update(classification="watch", evidence_ids=["synthetic_cited"])
    value["signal"].update(action="wait", evidence_ids=["synthetic_cited"], rationale="Acompanhar a divulgação enquanto faltam fundamentos verificados.")
    value["evidence_ids"] = ["synthetic_cited"]
    assert validate_analysis(value, bundle, ROOT / "schemas")
    value["signal"]["evidence_ids"] = []
    with pytest.raises(EvidenceError, match="signal_support_insufficient"):
        validate_analysis(value, bundle, ROOT / "schemas")


@pytest.mark.parametrize("field,replacement,code", [
    ("as_of", "2026-10-30T15:00:00Z", "signal_identity_or_time_mismatch"),
    ("horizon_days", 7, "signal_horizon_invalid"),
    ("evidence_ids", ["invented"], "signal_evidence_missing"),
    ("action", "wait", "signal_classification_mismatch"),
])
def test_signal_metadata_and_classification_must_match(field, replacement, code):
    value, bundle = signal_document()
    value["signal"][field] = replacement
    with pytest.raises(EvidenceError, match=code):
        validate_analysis(value, bundle, ROOT / "schemas")


@pytest.mark.parametrize("action,classification", [("buy", "investigate"), ("sell", "avoid_for_now")])
def test_directional_actions_remain_blocked_without_verified_fundamentals_contract(action, classification):
    value, bundle = signal_document()
    value["status"] = "supported_hypothesis"
    value["opportunity"].update(classification=classification, evidence_ids=["synthetic_cited"])
    value["signal"].update(action=action, evidence_ids=["synthetic_cited"])
    value["evidence_ids"] = ["synthetic_cited"]
    bundle["quality_flags"] = ["fundamentals_verified"]  # A free-form flag is not a contract.
    with pytest.raises(EvidenceError, match="signal_directional_data_unverified"):
        validate_analysis(value, bundle, ROOT / "schemas")


@pytest.mark.parametrize("field", ["risks", "counterpoints", "invalidation_conditions"])
def test_signal_explanation_lists_are_required(field):
    value, bundle = signal_document()
    value["signal"][field] = []
    with pytest.raises(EvidenceError, match="schema_invalid"):
        validate_analysis(value, bundle, ROOT / "schemas")


@pytest.mark.parametrize("field", ["rationale", "risks", "counterpoints", "invalidation_conditions"])
def test_signal_cannot_introduce_unsupported_numbers(field):
    value, bundle = signal_document()
    value["signal"]["evidence_ids"] = ["synthetic_cited"]
    value["evidence_ids"] = ["synthetic_cited"]
    value["signal"][field] = "PRIVATE_SENTINEL 999" if field == "rationale" else ["PRIVATE_SENTINEL 999"]
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence") as failure:
        validate_analysis(value, bundle, ROOT / "schemas")
    diagnostic = diagnostic_for(failure.value, bundle)
    issue = diagnostic["issues"][0]
    assert issue["path"].startswith("/signal/" + field)
    assert issue["evidence_ids"] == ["synthetic_cited"]
    assert "PRIVATE_SENTINEL" not in dumps(diagnostic) and "999" not in dumps(diagnostic)


def test_signal_citations_participate_in_root_union():
    value, bundle = signal_document()
    value["signal"]["evidence_ids"] = ["synthetic_cited"]
    with pytest.raises(EvidenceError, match="root_evidence_not_exact_union"):
        validate_analysis(value, bundle, ROOT / "schemas")
    value["evidence_ids"] = ["synthetic_cited"]
    assert validate_analysis(value, bundle, ROOT / "schemas")


def test_runner_rejects_legacy_output_in_new_call(setup_runner):
    runner, bundle, calls, config, *_ = setup_runner
    runtime = json.loads(config.read_text())
    runtime["max_repair_attempts"] = 0
    config.write_text(dumps(runtime))
    def legacy(*args, **kwargs):
        envelope = json.loads(kwargs["stdin"].decode().rsplit("\n\n", 1)[1])
        envelope["schema_version"] = "1.0"
        calls.append(1)
        return encode_events(final_analysis(envelope))
    runner.executor = legacy
    with pytest.raises(CLIError, match="cli_analysis_validation_rejected"):
        runner.run(bundle)
    assert len(calls) == 1
    assert not runner.store.rows("SELECT * FROM analyses")


def test_prompt_supplement_change_invalidates_cache(setup_runner, tmp_path):
    runner, bundle, calls, *_ = setup_runner
    prompt_dir = tmp_path / "prompts"
    shutil.copytree(ROOT / "prompts", prompt_dir)
    runner.prompt_dir = prompt_dir
    first = runner.run(bundle)
    supplement = prompt_dir / "analysis-signal-v1.1.txt"
    supplement.write_text(supplement.read_text(encoding="utf-8") + "\nVersão de teste isolado.\n", encoding="utf-8")
    second = runner.run(bundle)
    assert first["analysis"]["analysis_id"] != second["analysis"]["analysis_id"]
    assert len(calls) == 2 and not second["cache_reused"]
    assert second["analysis"]["schema_version"] == "1.1"


def test_diagnostics_resanitize_unknown_values_and_bound_resolved_ids():
    known = ["public_" + str(i) for i in range(12)]
    issue = {"code": "factual_number_not_in_cited_evidence", "path": "/claims/0/statement", "rule": None,
             "evidence_ids": known + ["PRIVATE_SENTINEL", "private" * 50], "claim_kind": "fact",
             "rejected_value": "SECRET_999", "statement": "SECRET_999"}
    error = EvidenceError("factual_number_not_in_cited_evidence", [issue])
    diagnostic = diagnostic_for(error, {"evidence": [{"evidence_id": item} for item in known]})
    result = diagnostic["issues"][0]
    assert len(result["evidence_ids"]) == 8 and set(result["evidence_ids"]) <= set(known)
    assert "PRIVATE" not in dumps(diagnostic) and "SECRET" not in dumps(diagnostic)


@pytest.mark.parametrize("source,statement", [
    ("Variação 5%.", "Variação .5%."),
    ("Variação +5%.", "Variação - 5%."),
    ("Receita 1.5 million.", "Receita 1.5 billion."),
])
def test_external_validator_numeric_regressions(source, statement):
    value, bundle = documents(source, statement)
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(value, bundle, ROOT / "schemas")


@pytest.mark.parametrize("text,code", [("Lucro garantido.", "unsupported_financial_certainty"), ("   ", "explanation_empty")])
@pytest.mark.parametrize("field", ["rationale", "risks", "counterpoints", "invalidation_conditions"])
def test_signal_rejects_empty_explanations_and_promises(text, code, field):
    value, bundle = signal_document()
    value["signal"][field] = text if field == "rationale" else [text]
    with pytest.raises(EvidenceError, match=code):
        validate_analysis(value, bundle, ROOT / "schemas")


@pytest.mark.parametrize("disclaimer", ["O sinal não garante retorno.", "O sinal nao garante retorno."])
def test_immediate_explicit_negation_of_return_guarantee_is_allowed(disclaimer):
    value, bundle = signal_document()
    value["signal"]["rationale"] = disclaimer
    assert validate_analysis(value, bundle, ROOT / "schemas")


def test_disclaimer_cannot_hide_a_second_affirmative_promise():
    value, bundle = signal_document()
    value["signal"]["rationale"] = "O sinal não garante retorno, mas esta oportunidade garante retorno."
    with pytest.raises(EvidenceError, match="unsupported_financial_certainty"):
        validate_analysis(value, bundle, ROOT / "schemas")


@pytest.mark.parametrize("source,statement,accepted", [
    ("Resultado de 88,4 mi.", "Resultado de 88,4 milhões.", True),
    ("Resultado de US$ 1,25 bi.", "Resultado de US$ 1,25 bilhão.", True),
    ("Resultado de US$ 1,25 bi.", "Resultado de US$ 1,25 mi.", False),
    ("Resultado de 88,4 mi.", "Resultado de 88,4 bi.", False),
    ("Resultado de US$ 1,25 bi.", "Resultado de US$ 1,25.", False),
    ("Resultado de 88,4 milhões.", "Resultado de 88,4 mi.", True),
])
def test_scale_abbreviations_observed_in_public_bundle(source, statement, accepted):
    value, bundle = documents(source, statement)
    if accepted:
        assert validate_analysis(value, bundle, ROOT / "schemas")
    else:
        with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
            validate_analysis(value, bundle, ROOT / "schemas")
