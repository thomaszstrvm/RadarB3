"""Inactive privacy candidate; synthetic text and pinned public fields only."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from backend import numeric_support
from test_counterpoint_numeric_contract import CASES, build_case, evaluate_counterpoint, load_bundle


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'docs/qa-20261101/bounded-rejection-qa/counterpoint-contract/capture_candidate.py'
SPEC = importlib.util.spec_from_file_location('counterpoint_capture_candidate', PATH)
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)

# The remaining enumerated cases must be retained. The explicit count prevents
# an added contract case from silently extending this reviewed privacy matrix.
BLOCK_REASONS = {
    ('metric_alias', 'decimal_comma'): 'candidate_unknown_number_blocked',
    ('metric_alias', 'explicit_plus'): 'candidate_unknown_number_blocked',
    ('metric_alias', 'unicode_minus'): 'candidate_character_blocked',
    ('metric_invalid', 'percent_conversion'): 'candidate_character_blocked',
    ('metric_invalid', 'rounded'): 'candidate_unknown_number_blocked',
    ('metric_invalid', 'sign_reversed'): 'candidate_unknown_number_blocked',
    ('metric_invalid', 'missing_unit'): 'candidate_context_blocked',
    ('metric_invalid', 'currency_added'): 'candidate_context_blocked',
    ('metric_invalid', 'scale_added'): 'candidate_context_blocked',
    ('metric_invalid', 'numeric_horizon'): 'candidate_context_blocked',
    ('price_alias', 'decimal_comma'): 'candidate_unknown_number_blocked',
    ('price_alias', 'explicit_plus'): 'candidate_unknown_number_blocked',
    ('price_invalid', 'sign_reversed'): 'candidate_unknown_number_blocked',
    ('price_invalid', 'denominator'): 'candidate_vocabulary_blocked',
    ('price_invalid', 'suffix_continuation'): 'candidate_context_blocked',
    ('price_invalid', 'leading_decimal'): 'candidate_number_uncovered',
    ('price_invalid', 'conflicting_prefixes'): 'candidate_context_blocked',
    ('filing_alias', 'grouping_pt'): 'candidate_number_uncovered',
    ('filing_alias', 'grouping_en'): 'candidate_number_uncovered',
    ('missing', 'return_words'): 'candidate_empty_numeric_scope',
    ('missing', 'qualitative_hypothesis'): 'candidate_empty_numeric_scope',
    ('missing', 'invented_zero'): 'candidate_unknown_number_blocked',
    ('metadata', 'session_date'): 'candidate_context_blocked',
    ('metadata', 'publication_year'): 'candidate_unknown_number_blocked',
    ('metadata', 'horizon'): 'candidate_unknown_number_blocked',
    ('metadata', 'numeric_identifier'): 'candidate_number_uncovered',
}
SAFE_FIELDS = {'grammar', 'origin', 'value_validated', 'lexical_exact',
               'literal_count', 'unknown_literal_count', 'fixed_label_count'}


@pytest.fixture
def bundle():
    return load_bundle()


@pytest.mark.parametrize('family,variant,financial_acceptance', CASES,
                         ids=[family + '-' + variant for family, variant, _ in CASES])
def test_enumerated_capture_matrix_preserves_financial_decision(bundle, family, variant, financial_acceptance):
    assert len(CASES) == 80
    statement, refs = build_case(bundle, family, variant)
    before = evaluate_counterpoint(bundle, statement, refs)
    assert before['accepted'] is financial_acceptance
    reason = BLOCK_REASONS.get((family, variant))
    if reason:
        with pytest.raises(candidate.CandidateError) as caught:
            candidate.sanitize_candidate(statement, bundle)
        assert caught.value.code == reason
        return
    retained, metadata = candidate.sanitize_candidate(statement, bundle)
    assert retained is statement and retained.encode('utf-8') == statement.encode('utf-8')
    assert set(metadata) == SAFE_FIELDS
    assert metadata['origin'] == 'rejected_model_statement'
    assert metadata['value_validated'] is False and metadata['lexical_exact'] is True
    assert 0 <= metadata['literal_count'] <= 3
    assert 0 <= metadata['unknown_literal_count'] <= 1
    assert evaluate_counterpoint(bundle, retained, refs) == before


@pytest.mark.parametrize('label', ['R63', 'M5', 'M21', 's20'])
def test_fixed_labels_are_retained_without_generic_numeric_exemption(bundle, label):
    text = label + ' ausente.'
    retained, metadata = candidate.sanitize_candidate(text, bundle)
    assert retained is text and metadata['fixed_label_count'] == 1
    assert metadata['literal_count'] == metadata['unknown_literal_count'] == 0


@pytest.mark.parametrize('spelling', ['123456.7', '123.4567', '-123.4567', '+123.4567', '123,4567', '000012.3'])
def test_only_legacy_unknown_money_with_original_digit_limits_is_allowed(bundle, spelling):
    text = 'Lucro trimestral de USD ' + spelling + ' milhões.'
    retained, metadata = candidate.sanitize_candidate(text, bundle)
    assert retained is text
    assert metadata['unknown_literal_count'] == 1 and metadata['value_validated'] is False


PRIVACY_BLOCKS = [
    ('person_name', 'R63 para Alice.'),
    ('email', 'R63 para caixa@example.com.'),
    ('phone', 'R63 telefone 11987654321.'),
    ('cpf', 'R63 CPF 12345678901.'),
    ('monetary_phone_shape', 'Lucro trimestral de USD 119876.5432 milhões.'),
    ('unknown_integer_seven', 'Lucro trimestral de USD 1234567 milhões.'),
    ('unknown_decimal_five', 'Lucro trimestral de USD 1.23456 milhões.'),
    ('leading_zeros_count', 'Lucro trimestral de USD 0000012.3 milhões.'),
    ('url', 'R63 https://caixa.com.'),
    ('domain_allowlisted_words', 'R63 caixa.com.'),
    ('spaced_domain_allowlisted_words', 'R63 caixa . com.'),
    ('windows_path', 'R63 C:\\Users\\caixa.'),
    ('unix_path', 'R63 /tmp/caixa.'),
    ('api_secret', 'R63 sk-proj-SYNTHETIC_SECRET.'),
    ('token_assignment', 'R63 token=SYNTHETIC_SECRET.'),
    ('uuid', 'R63 a1234567-1234-1234-1234-123456789012.'),
    ('newline', 'R63\nausente.'),
    ('tab', 'R63\tausente.'),
    ('invisible', 'R63\u200bausente.'),
    ('rtl', 'R63\u202eausente.'),
    ('nbsp', 'R63\u00a0ausente.'),
    ('surrogate', 'R63\ud800ausente.'),
    ('unicode_digits', 'Lucro de USD １２３ milhões.'),
    ('unknown_short_fraction', 'Retorno de 0.1234 fraction.'),
    ('unknown_short_annualized', 'Volatilidade de 0.1234 annualized_fraction.'),
    ('unknown_short_dimensionless', 'Alinhamento de 0.1234 dimensionless.'),
    ('unknown_exponent', 'Lucro trimestral de USD 1e2 milhões.'),
    ('leading_decimal', 'Lucro trimestral de USD .1234 milhões.'),
    ('sign_before_currency', 'Lucro trimestral de -USD 123.4 milhões.'),
    ('unknown_parentheses', 'Lucro trimestral de (USD 123.4567 milhões).'),
    ('unknown_currency_suffix', 'Lucro trimestral de 123.4567 milhões BRL.'),
    ('free_number', 'R63 valor 1234.'),
    ('new_label', 'R64 ausente.'),
    ('new_alignment_label', 'M6 ausente.'),
    ('label_fragment', 'M_5 ausente.'),
    ('free_metric_identifier', 'provider_adjusted_change_63 ausente.'),
    ('unit_without_coefficient', 'R63 annualized_fraction ausente.'),
    ('arbitrary_underscore', 'R63 caixa_operacional ausente.'),
    ('bare_unknown_unit', 'R63 NaN fraction.'),
    ('numeric_date', 'R63 em 2026-10-30.'),
]


@pytest.mark.parametrize('name,text', PRIVACY_BLOCKS, ids=[name for name, _ in PRIVACY_BLOCKS])
def test_privacy_denies_whole_input_and_returns_only_enum(bundle, name, text):
    with pytest.raises(candidate.CandidateError) as caught:
        candidate.sanitize_candidate(text, bundle)
    assert caught.value.code in candidate.ERROR_CODES
    assert caught.value.args == (caught.value.code,)
    assert vars(caught.value) == {'code': caught.value.code}


def test_maximum_three_literals_and_only_one_unknown(bundle):
    first, _ = build_case(bundle, 'metric', 'daily_return')
    second, _ = build_case(bundle, 'metric', 'positive_alignment')
    unknown = 'Lucro trimestral de USD 123.4567 milhões.'
    text = ' '.join([first, second, unknown])
    retained, metadata = candidate.sanitize_candidate(text, bundle)
    assert retained is text and metadata['literal_count'] == 3
    assert metadata['unknown_literal_count'] == 1
    with pytest.raises(candidate.CandidateError, match='candidate_literal_limit'):
        candidate.sanitize_candidate(' '.join([first] * 4), bundle)
    with pytest.raises(candidate.CandidateError, match='candidate_unknown_limit'):
        candidate.sanitize_candidate(unknown + ' ' + unknown, bundle)
    with pytest.raises(candidate.CandidateError, match='candidate_label_limit'):
        candidate.sanitize_candidate('R63 R63 R63 R63 R63 ausente.', bundle)


def test_known_parenthesized_money_reproduces_original_format_rejection(bundle):
    statement, refs = build_case(bundle, 'price', 'close')
    statement = statement.replace('de BRL ', 'de (BRL ').removesuffix('.') + ').'
    retained, metadata = candidate.sanitize_candidate(statement, bundle)
    result = evaluate_counterpoint(bundle, retained, refs)
    assert retained is statement and metadata['unknown_literal_count'] == 0
    assert result['accepted'] is False and result['reason'] == 'unsupported_format'


def test_privacy_does_not_call_financial_parser_or_io(bundle, monkeypatch):
    text, _ = build_case(bundle, 'formula_label', 's20')
    def forbidden(*args, **kwargs):
        pytest.fail('Privacy prototype must be independent and in-memory')
    monkeypatch.setattr(numeric_support, 'quantities', forbidden)
    monkeypatch.setattr('builtins.open', forbidden)
    retained, metadata = candidate.sanitize_candidate(text, bundle)
    assert retained is text and metadata['value_validated'] is False


def test_underscores_only_inside_consumed_fixed_unit(bundle):
    text, _ = build_case(bundle, 'metric', 'annualized_volatility')
    assert 'annualized_fraction' in text
    retained, _ = candidate.sanitize_candidate(text, bundle)
    assert retained is text
    with pytest.raises(candidate.CandidateError, match='candidate_context_blocked'):
        candidate.sanitize_candidate(text + ' caixa_operacional.', bundle)


@pytest.mark.parametrize('location', ['metric', 'filing', 'price', 'title', 'metadata'])
def test_caller_cannot_poison_public_allowlist_by_changing_bundle(bundle, location):
    altered = copy.deepcopy(bundle)
    if location == 'metric': altered['computed_metrics'][0]['value'] = 11987654321
    elif location == 'filing':
        item = next(item for item in altered['evidence'] if item['record_type'] == 'filing')
        document = json.loads(item['content']); document['observations'][0]['value'] = '11987654321'
        item['content'] = json.dumps(document)
    elif location == 'price':
        item = next(item for item in altered['evidence'] if item['record_type'] == 'price')
        document = json.loads(item['content']); document['financial_series'][0]['volume'] = 11987654321
        item['content'] = json.dumps(document)
    elif location == 'title': altered['evidence'][0]['title'] = '11987654321'
    elif location == 'metadata': altered['asset']['name'] = '11987654321'
    with pytest.raises(candidate.CandidateError, match='candidate_bundle_untrusted'):
        candidate.sanitize_candidate('R63 ausente.', altered)


def test_typed_source_extraction_does_not_import_metadata(bundle):
    known, volumes = candidate._financial_lexemes(bundle)
    assert known and volumes <= known
    assert all(value not in known for value in [bundle['as_of'], bundle['bundle_id'], bundle['asset']['asset_id']])
    assert '11987654321' not in known
    long_value = next(value for value in known if len(value) > 10 and '.' in value)
    with pytest.raises(candidate.CandidateError, match='candidate_context_blocked'):
        candidate.sanitize_candidate('Valor de ' + long_value + '.', bundle)


def test_allowlist_is_extracted_from_the_snapshot_that_was_hashed(bundle, monkeypatch):
    original_hash = candidate.hashlib.sha256
    def mutate_caller_after_hash(encoded):
        result = original_hash(encoded)
        bundle['computed_metrics'][0]['value'] = 11987654321
        return result
    monkeypatch.setattr(candidate.hashlib, 'sha256', mutate_caller_after_hash)
    with pytest.raises(candidate.CandidateError, match='candidate_unknown_number_blocked'):
        candidate.sanitize_candidate('Retorno de 11987654321 fraction.', bundle)


def test_original_text_is_not_trimmed_truncated_or_normalized(bundle):
    text = ' R63 ausente.' + ' ' * (512 - len(' R63 ausente.'))
    retained, _ = candidate.sanitize_candidate(text, bundle)
    assert retained is text and len(retained) == 512
    for invalid in [text + ' ', '', None, 123]:
        with pytest.raises(candidate.CandidateError, match='candidate_size_blocked'):
            candidate.sanitize_candidate(invalid, bundle)
    with pytest.raises(candidate.CandidateError, match='candidate_character_blocked'):
        candidate.sanitize_candidate('R63 ausente por observac\u0327o\u0303es.', bundle)


def test_unrecognized_exception_code_cannot_embed_rejected_text():
    for supplied in ['SYNTHETIC_SECRET', {}, None]:
        error = candidate.CandidateError(supplied)
        assert error.code == str(error) == 'candidate_context_blocked'
