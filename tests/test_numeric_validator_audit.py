"""Synthetic audit of numeric support, with no DB, network, worker or AI.

The fixture exercises the real bundle and analysis schemas without monkeypatches.
Metadata/title facts and fraction-to-percent conversion remain explicit refusals:
the conservative parser does not claim to support those transformations.
"""
import hashlib
import json
from pathlib import Path

import pytest

from test_cli_runner import encode_events, final_analysis, setup_runner

from backend.evidence import EvidenceError, metrics_for_bars, validate_analysis, validate_bundle
from backend.timeutil import news_window
from backend.validation_diagnostics import diagnostic_for


SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
AS_OF = "2026-10-31T15:00:00.000000Z"


def documents(content, statement, *, record_type="article", title="Synthetic title"):
    start, end = news_window(AS_OF)
    evidence = {
        "evidence_id": "synthetic_cited", "record_type": record_type,
        "source_id": "synthetic", "url": None, "title": title,
        "content": content, "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "access_level": "excerpt" if record_type == "article" else "structured",
        "published_at": "2026-10-31T12:00:00Z" if record_type == "article" else None,
        "modified_at": None, "first_seen_at": "2026-10-31T13:00:00Z",
        "fetched_at": "2026-10-31T13:00:00Z", "market_timestamp": None,
        "timestamp_precision": "instant", "publisher_group": None,
        "original_publisher": None, "entity_ids": ["synthetic_asset"],
        "quality_flags": [], "available_at": "2026-10-31T13:00:00Z",
    }
    bundle = {
        "schema_version": "1.0", "bundle_id": "synthetic_bundle", "as_of": AS_OF,
        "asset": {"asset_id": "synthetic_asset", "ticker": "TEST3",
                  "name": "Synthetic", "asset_type": "stock", "currency": "BRL", "sector": None},
        "window": {"start": start, "end": end, "lookback_calendar_months": 3,
                   "timezone": "America/Sao_Paulo"},
        "forecast_horizons_days": [30, 90], "evidence": [evidence], "computed_metrics": [],
        "coverage": {"eligible_assets": 1, "assets_with_recent_prices": 0,
                     "editorial_sources_selected": 8, "editorial_sources_reached": 1,
                     "full_text_count": 0, "partial_text_count": 1,
                     "truncated_queries_count": 0, "known_gaps": []},
        "quality_flags": [],
    }
    analysis = {
        "schema_version": "1.0", "analysis_id": "synthetic_analysis", "as_of": AS_OF,
        "asset_id": "synthetic_asset", "status": "insufficient_data", "synthesis": "Dados insuficientes.",
        "claims": [{"kind": "fact", "statement": statement, "evidence_ids": ["synthetic_cited"]}],
        "scenarios": [], "opportunity": {"classification": "insufficient_data",
        "rationale": "Dados insuficientes.", "evidence_ids": []}, "risks": [],
        "missing_evidence": [], "data_quality_flags": [], "evidence_ids": ["synthetic_cited"],
    }
    assert validate_bundle(bundle, SCHEMAS)
    return analysis, bundle


@pytest.mark.parametrize("spelling", ["41,70", "041.700", "41.7000"])
def test_exact_decimal_equivalence_is_already_supported(spelling):
    analysis, bundle = documents("Fechamento 41.7", f"Fechamento {spelling}")
    assert validate_analysis(analysis, bundle, SCHEMAS)


def test_an_uncited_evidence_cannot_supply_a_number():
    analysis, bundle = documents("Fechamento 41.7", "Fechamento 41.71")
    extra = dict(bundle["evidence"][0], evidence_id="synthetic_uncited", content="41.71",
                 content_sha256=hashlib.sha256(b"41.71").hexdigest())
    bundle["evidence"].append(extra)
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(analysis, bundle, SCHEMAS)


def test_publication_metadata_remains_an_explicit_support_limit():
    analysis, bundle = documents("Empresa divulga balanço.", "A matéria foi publicada em 31/10/2026.")
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(analysis, bundle, SCHEMAS)


def test_title_only_number_remains_an_explicit_support_limit():
    analysis, bundle = documents("Empresa divulga balanço.", "Lucro de 88,4 milhões.",
                                 title="Lucro de 88,4 milhões.")
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(analysis, bundle, SCHEMAS)


@pytest.mark.parametrize("source,claim", [
    ("Preço 1234.56", "Preço 1.234,56"),
    ("Preço 1.234,56", "Preço 1234.56"),
    ("Preço 1234.56", "Preço 1,234.56"),
])
def test_unambiguous_grouping_should_preserve_one_number(source, claim):
    analysis, bundle = documents(source, claim)
    assert validate_analysis(analysis, bundle, SCHEMAS)


def test_fraction_to_percent_conversion_remains_an_explicit_support_limit():
    analysis, bundle = documents(json.dumps({"value": 0.125, "unit": "fraction"}),
                                 "A variação é 12,5%.", record_type="macro")
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(analysis, bundle, SCHEMAS)


@pytest.mark.parametrize("source,claim", [
    ("Variação -5%.", "Variação +5%."),
    ("Receita de R$ 1,5 milhão.", "Receita de R$ 1,5 bilhão."),
    ("Variação 5%.", "Variação 5 pontos percentuais."),
    ("Pagamento em 12/10/2026.", "Pagamento em 10/12/2026."),
    ("Preço R$ 1.234,56.", "Preço R$ 56."),
    ("Volume de 1e6 ações.", "Volume de 1 ação."),
    ("Variação -5%.", "Variação 5%."),
    ("Variação −5%.", "Variação 5%."),
    ("Variação 5%.", "Variação -5%."),
    ("Receita de R$ 1,5 milhão.", "Receita de US$ 1,5 milhão."),
    ("Variação 5 pontos percentuais.", "Variação 5%."),
])
def test_a_different_quantity_must_not_be_supported(source, claim):
    analysis, bundle = documents(source, claim)
    with pytest.raises(EvidenceError):
        validate_analysis(analysis, bundle, SCHEMAS)


def test_session_date_inside_structured_content_cannot_support_a_price():
    analysis, bundle = documents(json.dumps({"close": 41.7, "currency": "BRL", "session_date": "2026-10-30"}),
                                 "Fechamento R$ 2026.", record_type="price")
    with pytest.raises(EvidenceError):
        validate_analysis(analysis, bundle, SCHEMAS)


@pytest.mark.parametrize("statement, accepted", [
    ("Fechamento R$ 41,70.", True),
    ("Fechamento BRL 41.7.", True),
    ("Fechamento US$ 41.7.", False),
    ("Fechamento USD 41.7.", False),
    ("Fechamento EUR 41.7.", False),
    ("Fechamento R$ -41.7.", False),
    ("Fechamento 41,7%.", False),
])
def test_structured_price_preserves_currency_and_unit(statement, accepted):
    analysis, bundle = documents(json.dumps({"close": 41.7, "currency": "BRL", "session_date": "2026-10-30"}),
                                 statement, record_type="price")
    if accepted:
        assert validate_analysis(analysis, bundle, SCHEMAS)
    else:
        with pytest.raises(EvidenceError):
            validate_analysis(analysis, bundle, SCHEMAS)


@pytest.mark.parametrize("content, statement", [
    ("Variação -5%.", "Variação −5,00%."),
    ("Variação +5%.", "Variação 5%."),
    ("Receita de R$ 1,5 milhão.", "Receita de BRL 1.50 milhões."),
    ("Pagamento em 12/10/2026.", "Pagamento em 12/10/2026."),
])
def test_supported_sign_units_and_dates_are_not_blanket_rejected(content, statement):
    analysis, bundle = documents(content, statement)
    assert validate_analysis(analysis, bundle, SCHEMAS)


@pytest.mark.parametrize("content, statement", [
    ("Empresa divulga resultado.", "Receita R$999bi."),
    ("Receita R$1.", "Receita R$1,25bi."),
    ("Saldo R$5.", "Saldo -R$5."),
    ("Saldo 5 BRL.", "Saldo 5 USD."),
    ("Preço R$5.", "Preço R$5/kg."),
])
def test_unsupported_numeric_suffix_or_context_cannot_disappear(content, statement):
    analysis, bundle = documents(content, statement)
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(analysis, bundle, SCHEMAS)


def with_signal(analysis):
    analysis["schema_version"] = "1.1"
    analysis["signal"] = {
        "action": "insufficient_data", "horizon_days": 30, "as_of": analysis["as_of"],
        "rationale": "Dados insuficientes.", "evidence_ids": ["synthetic_cited"],
        "risks": ["Dados parciais."], "counterpoints": ["Faltam fundamentos."],
        "invalidation_conditions": ["Reavaliar quando houver dados verificáveis."],
    }
    return analysis


@pytest.mark.parametrize("field", ["risks", "missing_evidence", "scenario_description", "scenario_conditions", "scenario_invalidation_conditions"])
def test_v11_other_visible_fields_cannot_introduce_unsupported_numbers(field):
    analysis, bundle = documents("Fechamento 41.7.", "Fechamento 41.7.")
    with_signal(analysis)
    if field in {"risks", "missing_evidence"}:
        analysis[field] = ["Receita divulgada de R$999."]
    else:
        scenario = {"case": "conditional_case", "horizon_days": 30,
                    "description": "Cenário condicionado a dados novos.",
                    "conditions": ["Dados verificáveis."], "invalidation_conditions": ["Revisão de dados."],
                    "evidence_ids": ["synthetic_cited"]}
        target = field.removeprefix("scenario_")
        scenario[target] = "Receita divulgada de R$999." if target == "description" else ["Receita divulgada de R$999."]
        analysis["scenarios"] = [scenario]
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(analysis, bundle, SCHEMAS)


def metric_documents(statement):
    analysis, bundle = documents("Temporary synthetic content.", statement)
    template = bundle["evidence"][0]
    bars = []
    for index, (day, close) in enumerate([("2026-10-29", 100), ("2026-10-30", 112.5)]):
        bars.append({"session_date": day, "provider": "synthetic", "currency": "BRL",
                     "adjustment": "split_adjusted", "close": close, "volume": 1000,
                     "complete_session": True, "revision_id": f"synthetic_price_{index}",
                     "market_timestamp": day + "T20:00:00Z", "quality_flags": [],
                     "calendar": {"session_date": day, "closes_at": day + "T20:00:00Z",
                                  "previous_open_session": None if index == 0 else "2026-10-29",
                                  "closed_dates": [], "source": "https://example.org/calendar",
                                  "version": "synthetic", "verified_at": "2026-10-01T12:00:00Z",
                                  "expected_last_session": "2026-10-30"}})
    metric = metrics_for_bars(bars)[0]
    assert metric["value"] == 0.125 and metric["unit"] == "fraction"
    metric["evidence_id"] = "synthetic_cited"
    items = []
    for bar in bars:
        content = json.dumps(bar)
        items.append(dict(template, evidence_id=bar["revision_id"], record_type="price",
                          access_level="structured", published_at=None, content=content,
                          market_timestamp=bar["market_timestamp"],
                          content_sha256=hashlib.sha256(content.encode()).hexdigest()))
    content = json.dumps({key: metric[key] for key in ("value", "unit", "formula_version", "evidence_ids")})
    items.append(dict(template, record_type="computed_metric", access_level="structured", published_at=None,
                      content=content, content_sha256=hashlib.sha256(content.encode()).hexdigest()))
    bundle["evidence"] = items
    bundle["computed_metrics"] = [metric]
    assert validate_bundle(bundle, SCHEMAS)
    return analysis, bundle


@pytest.mark.parametrize("statement", ["Retorno de 0,125 vezes.", "Retorno de 0.125 multiple."])
def test_computed_fraction_cannot_support_a_multiple(statement):
    analysis, bundle = metric_documents(statement)
    with pytest.raises(EvidenceError, match="factual_number_not_in_cited_evidence"):
        validate_analysis(analysis, bundle, SCHEMAS)


@pytest.mark.parametrize("statement", ["Retorno de 0,125 fração.", "Retorno de 0.125 fraction."])
def test_computed_fraction_supports_its_explicit_original_unit(statement):
    analysis, bundle = metric_documents(statement)
    assert validate_analysis(analysis, bundle, SCHEMAS)


def test_persisted_numeric_diagnostic_preserves_only_resolved_ids(setup_runner):
    from backend.cli_process import CLIError
    from backend.core import digest, dumps

    runner, bundle, calls, config_path, *_ = setup_runner
    runtime = json.loads(config_path.read_text())
    runtime["max_repair_attempts"] = 0
    config_path.write_text(dumps(runtime))
    _, fixture_bundle = documents("Fechamento 41.7.", "Fechamento 41.7.")
    item = fixture_bundle["evidence"][0]
    for field in ("published_at", "first_seen_at", "fetched_at", "available_at"):
        item[field] = bundle["as_of"]
    item["entity_ids"] = [bundle["asset"]["asset_id"]]
    bundle["evidence"] = [item]
    bundle["bundle_id"] = "synthetic_bundle_with_article"
    assert validate_bundle(bundle, SCHEMAS)
    with runner.store.transaction() as connection:
        connection.execute("INSERT INTO evidence_bundles VALUES(?,?,?,?,?,?)",
                           (bundle["bundle_id"], bundle["asset"]["asset_id"], bundle["as_of"],
                            digest(bundle), dumps(bundle), bundle["as_of"]))

    def rejected_output(*args, **kwargs):
        calls.append(1)
        envelope = json.loads(kwargs["stdin"].decode().rsplit("\n\n", 1)[1])
        value = final_analysis(envelope)
        value["claims"] = [{"kind": "fact", "statement": "PRIVATE_SENTINEL R$98765.4321.",
                            "evidence_ids": ["synthetic_cited"]}]
        value["evidence_ids"] = ["synthetic_cited"]
        return encode_events(value)

    runner.executor = rejected_output
    with pytest.raises(CLIError, match="cli_analysis_validation_rejected"):
        runner.run(bundle)
    events = runner.store.rows("SELECT payload_json FROM audit_events WHERE event_type='analysis_validation'")
    assert len(events) == len(calls) == 1
    serialized = events[0]["payload_json"]
    assert "PRIVATE_SENTINEL" not in serialized and "98765" not in serialized and "41.7" not in serialized
    issue = json.loads(serialized)["diagnostic"]["issues"][0]
    assert issue["path"] == "/claims/0/statement"
    assert issue["evidence_ids"] == ["synthetic_cited"]
    assert issue["claim_kind"] == "fact"
    assert runner.store.rows("SELECT status FROM quota_ledger") == [{"status": "consumed"}]
    assert not runner.store.rows("SELECT * FROM analyses")


def test_numeric_failure_diagnostic_has_only_a_bounded_structural_path():
    analysis, bundle = documents("Fechamento 41.7", "PRIVATE_SENTINEL 98765.4321")
    with pytest.raises(EvidenceError) as failure:
        validate_analysis(analysis, bundle, SCHEMAS)
    diagnostic = diagnostic_for(failure.value)
    assert diagnostic["issues"][0]["path"] == "/claims/0/statement"
    serialized = json.dumps(diagnostic)
    assert "PRIVATE_SENTINEL" not in serialized
    assert "98765" not in serialized
    assert "41.7" not in serialized
