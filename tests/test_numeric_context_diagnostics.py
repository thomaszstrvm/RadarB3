"""Post-rejection filing diagnostics; archived public inputs and isolated DBs."""
import ast
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from backend import numeric_support as numeric
# Import API/asyncio during collection, before the autouse subprocess guard
# replaces Popen. Windows asyncio defines a subclass of the original class.
from backend.api import create_app
from backend.core import digest, dumps
from backend.evidence import EvidenceError, validate_analysis
from backend.validation_diagnostics import NUMERIC_REASONS, diagnostic_for, sanitize_issue
from test_cli_runner import encode_events, setup_runner
from test_vintage_bundle import analysis_for


ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'docs/qa-20261101/numeric-context-diagnostics'
PUBLIC = ROOT / 'docs/qa-20261101/manual-ai-v1.2'
NEW_REASONS = {
    'filing_account_unrecognized', 'filing_account_ambiguous',
    'filing_period_unrecognized', 'filing_period_ambiguous',
    'filing_period_incompatible',
}
CASES = [
    ('Resultado trimestral de US$ 1255 milhões.', 'filing_account_unrecognized'),
    ('Receita e lucro trimestral de US$ 1255 milhões.', 'filing_account_ambiguous'),
    ('Lucro de US$ 1255 milhões.', 'filing_period_unrecognized'),
    ('Lucro trimestral acumulado de US$ 1255 milhões.', 'filing_period_ambiguous'),
    ('Lucro na posição de US$ 1255 milhões.', 'filing_period_incompatible'),
    ('Lucro trimestral de US$ 4523 milhões.', 'filing_period_incompatible'),
    ('Receita acumulada de US$ 10599 milhões.', 'filing_period_incompatible'),
    ('Caixa operacional trimestral de US$ 6810 milhões.', 'filing_period_incompatible'),
    ('Saldo de caixa trimestral de US$ 4841 milhões.', 'filing_period_ambiguous'),
    ('Lucro trimestral de R$ 1255 milhões.', 'unit_mismatch'),
    ('Lucro trimestral de US$ 1255 bilhões.', 'unit_mismatch'),
    ('Lucro trimestral de US$ 1255 milhões%.', 'unit_mismatch'),
    ('Lucro trimestral de 1255 milhões.', 'unit_mismatch'),
    ('Lucro trimestral de US$ 999999 milhões.', 'quantity_not_found'),
    ('Lucro trimestral de US$ -1255 milhões.', 'quantity_not_found'),
    ('Receita trimestral de US$ 1255 milhões.', 'quantity_not_found'),
    ('Lucro trimestral de US$ 6810 milhões.', 'quantity_not_found'),
    # Period/currency conflict: only a complete quantity in the same account
    # proves the other-period case. A coefficient alone does not.
    ('Lucro trimestral de EUR 4523 milhões.', 'unit_mismatch'),
    ('Lucro na posição de EUR 4523 milhões.', 'filing_period_incompatible'),
    ('Lucro trimestral de US$ 4523 bilhões.', 'unit_mismatch'),
    ('Lucro trimestral de EUR 999999 milhões.', 'unit_mismatch'),
]


@pytest.fixture
def filing():
    return json.loads((PUBLIC / 'filing-evidence.json').read_text(encoding='utf-8'))


@pytest.fixture
def public_bundle():
    return json.loads((PUBLIC / 'actual-bundle.json').read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def baseline():
    spec = importlib.util.spec_from_file_location(
        'backend.numeric_context_before', QA / 'source-before/backend/numeric_support.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('statement,reason', CASES)
def test_modern_filing_context_has_specific_bounded_reason(filing, statement, reason):
    assert not numeric.numbers_supported(statement, [filing], modern=True)
    assert numeric.numeric_rejection_reason(statement, [filing], modern=True) == reason


@pytest.mark.parametrize('statement,reason', [
    ('Resultado de US$ .1255 milhões em 29/10/2026.', 'unsupported_format'),
    ('Resultado de US$ 999999 milhões em 29/10/2026.', 'unsupported_date'),
    ('Resultado de EUR 999999 milhões.', 'filing_account_unrecognized'),
    ('Receita e lucro de EUR 999999 milhões.', 'filing_account_ambiguous'),
    ('Lucro de EUR 999999 milhões.', 'filing_period_unrecognized'),
    ('Lucro trimestral acumulado de EUR 999999 milhões.', 'filing_period_ambiguous'),
    ('Lucro na posição de EUR 999999 milhões.', 'filing_period_incompatible'),
    ('Lucro trimestral de US$ 4523 milhões e EUR 999999 milhões.', 'filing_period_incompatible'),
    ('Lucro trimestral de EUR 999999 milhões e US$ 4523 milhões.', 'filing_period_incompatible'),
    ('Lucro trimestral de US$ 999999 milhões e EUR 1255 milhões.', 'unit_mismatch'),
    ('Lucro trimestral de EUR 1255 milhões e US$ 999999 milhões.', 'unit_mismatch'),
])
def test_priority_format_date_account_period_unit_value(filing, statement, reason):
    assert not numeric.numbers_supported(statement, [filing], modern=True)
    assert numeric.numeric_rejection_reason(statement, [filing], modern=True) == reason


@pytest.mark.parametrize('statement', [
    'Receita trimestral de US$ 10599 milhões.',
    'Receita acumulada de USD 30355 mi.',
    'Lucro trimestral de US$ 1.255,0 milhões.',
    'Lucro acumulado de USD 4523 milhões.',
    'Caixa operacional trimestral de US$ 2519 milhões.',
    'Caixa das atividades operacionais acumulado de US$ 6810 milhões.',
    'Saldo de caixa de US$ 4841 milhões.',
    'Evidência qualitativa sem quantidade.',
])
def test_acceptance_never_runs_the_new_diagnostic(filing, statement, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Diagnostic must not decide numeric acceptance')
    monkeypatch.setattr(numeric, '_filing_rejection_reason', forbidden)
    assert numeric.numbers_supported(statement, [filing], modern=True)


def test_acceptance_source_and_corpus_are_unchanged():
    def acceptance_ast(path):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        tree.body = [node for node in tree.body if not (
            isinstance(node, ast.FunctionDef)
            and node.name in {'numeric_rejection_reason', '_filing_rejection_reason'})]
        return ast.dump(tree, include_attributes=False)
    assert acceptance_ast(ROOT / 'backend/numeric_support.py') == acceptance_ast(
        QA / 'source-before/backend/numeric_support.py')


@pytest.mark.parametrize('modern', [False, True])
def test_acceptance_matrix_matches_preserved_source(filing, baseline, modern):
    contexts = ['Resultado', 'Receita', 'Lucro', 'Caixa operacional', 'Saldo de caixa', 'Receita e lucro']
    periods = ['', ' trimestral', ' acumulado', ' na posição', ' trimestral acumulado']
    amounts = ['US$ 1255 milhões', 'USD 4523 mi', 'US$ 10599 milhões', 'US$ 2519 milhões',
               'US$ 6810 milhões', 'US$ 4841 milhões', 'EUR 4523 milhões', 'US$ 1255 bilhões',
               'US$ -1255 milhões', 'US$ .1255 milhões', '29/10/2026', 'US$ 999999 milhões']
    for context in contexts:
        for period in periods:
            for amount in amounts:
                statement = f'{context}{period} de {amount}.'
                assert numeric.numbers_supported(statement, [filing], modern=modern) == baseline.numbers_supported(
                    statement, [filing], modern=modern)
                if not modern and not baseline.numbers_supported(statement, [filing]):
                    assert numeric.numeric_rejection_reason(statement, [filing]) == baseline.numeric_rejection_reason(statement, [filing])


def test_mixed_or_metric_evidence_does_not_invent_filing_intent(filing):
    article = {'record_type': 'article', 'content': 'Preço de R$ 10.'}
    metric = {'record_type': 'computed_metric', 'content': dumps({
        'metric_id': 'technical_alignment_5', 'value': 0.5, 'unit': 'dimensionless'})}
    for statement, items in [
        ('Preço de R$ 11.', [filing, article]),
        ('Alinhamento de 0.7 dimensionless.', [filing, metric]),
        ('Lucro trimestral com alinhamento de 0.7 dimensionless.', [filing, metric]),
    ]:
        assert not numeric.numbers_supported(statement, items, modern=True)
        assert numeric.numeric_rejection_reason(statement, items, modern=True) == 'quantity_not_found'
    # A recognized filing account already selects only filings in the unchanged corpus.
    statement = 'Lucro de US$ 1255 milhões.'
    article['content'] = statement
    assert not numeric.numbers_supported(statement, [filing, article], modern=True)
    assert numeric.numeric_rejection_reason(statement, [filing, article], modern=True) == 'filing_period_unrecognized'


def test_legacy_implicit_modern_corpus_does_not_gain_context_reasons(filing, baseline):
    metric = {'record_type': 'computed_metric', 'content': dumps({
        'metric_id': 'technical_alignment_5', 'value': 0.5, 'unit': 'dimensionless'})}
    items = [filing, metric]
    for statement in ['Lucro de US$ 1255 milhões.', 'Receita e lucro trimestral de US$ 1255 milhões.']:
        assert not numeric.numbers_supported(statement, items)
        assert numeric.numeric_rejection_reason(statement, items) == baseline.numeric_rejection_reason(statement, items)
        assert numeric.numeric_rejection_reason(statement, items) == 'quantity_not_found'


def test_invalid_filing_cannot_supply_a_context_diagnosis(filing):
    invalid = copy.deepcopy(filing)
    document = json.loads(invalid['content'])
    document['observations'][0]['value'] = '1'
    invalid['content'] = dumps(document)
    statement = 'Resultado de US$ 1255 milhões.'
    assert not numeric.numbers_supported(statement, [invalid], modern=True)
    assert numeric.numeric_rejection_reason(statement, [invalid], modern=True) == 'quantity_not_found'


def analysis_with_claim(bundle, statement):
    result = analysis_for(bundle)
    reference = next(e['evidence_id'] for e in bundle['evidence'] if e['record_type'] == 'filing')
    result['claims'] = [{'kind': 'fact', 'statement': statement, 'evidence_ids': [reference]}]
    result['evidence_ids'] = [reference]
    return result


def expected_context_diagnostic(bundle, reason):
    reference = next(e['evidence_id'] for e in bundle['evidence'] if e['record_type'] == 'filing')
    return {'version': 1, 'stage': 'semantic', 'issues': [{
        'code': 'factual_number_not_in_cited_evidence', 'path': '/claims/0/statement',
        'rule': None, 'evidence_ids': [reference], 'claim_kind': 'fact', 'numeric_reason': reason,
    }]}


@pytest.mark.parametrize('statement,reason', CASES[:5])
def test_real_schema_v1_2_rejection_is_sanitized(public_bundle, statement, reason):
    value = analysis_with_claim(public_bundle, 'PRIVATE_SENTINEL ' + statement)
    with pytest.raises(EvidenceError, match='factual_number_not_in_cited_evidence') as caught:
        validate_analysis(value, public_bundle, ROOT / 'schemas')
    diagnostic = diagnostic_for(caught.value, public_bundle)
    assert diagnostic == expected_context_diagnostic(public_bundle, reason)
    assert 'PRIVATE_SENTINEL' not in dumps(diagnostic)


def test_v1_1_filing_keeps_legacy_acceptance_and_diagnostics(public_bundle):
    legacy_bundle = copy.deepcopy(public_bundle)
    legacy_bundle['schema_version'] = '1.1'
    legacy_bundle.pop('technical_policy')
    legacy_bundle['evidence'] = [e for e in legacy_bundle['evidence'] if e['record_type'] == 'filing']
    legacy_bundle['computed_metrics'] = []
    for statement, accepted in [('Resultado de US$ 1255 milhões.', True),
                                ('Lucro trimestral de US$ 4523 milhões.', True),
                                ('Resultado de US$ 999999 milhões.', False)]:
        value = analysis_with_claim(public_bundle, statement)
        value['schema_version'] = '1.1'
        value['signal'].pop('method')
        if accepted:
            assert validate_analysis(value, legacy_bundle, ROOT / 'schemas')
        else:
            with pytest.raises(EvidenceError) as caught:
                validate_analysis(value, legacy_bundle, ROOT / 'schemas')
            assert diagnostic_for(caught.value, legacy_bundle)['issues'][0]['numeric_reason'] == 'quantity_not_found'


def test_modern_valid_analysis_never_invokes_rejection_diagnostics(public_bundle, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Accepted analysis must not run diagnostic classification')
    monkeypatch.setattr('backend.evidence.numeric_rejection_reason', forbidden)
    assert validate_analysis(analysis_with_claim(public_bundle, 'Lucro trimestral de US$ 1255 milhões.'),
                             public_bundle, ROOT / 'schemas')


def test_modern_classifier_failure_does_not_change_rejection(public_bundle, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError('PRIVATE_SENTINEL 998877')
    monkeypatch.setattr(numeric, '_filing_rejection_reason', broken)
    with pytest.raises(EvidenceError, match='factual_number_not_in_cited_evidence') as caught:
        validate_analysis(analysis_with_claim(public_bundle, 'Lucro de US$ 1255 milhões.'),
                          public_bundle, ROOT / 'schemas')
    diagnostic = diagnostic_for(caught.value, public_bundle)
    assert 'numeric_reason' not in diagnostic['issues'][0]
    assert 'PRIVATE' not in dumps(diagnostic) and '998877' not in dumps(diagnostic)


@pytest.mark.parametrize('reason', sorted(NEW_REASONS))
def test_new_reason_allowlist_is_bounded_idempotent_and_code_specific(reason):
    assert reason in NUMERIC_REASONS
    raw = {'code': 'factual_number_not_in_cited_evidence', 'path': '/claims/0/statement',
           'numeric_reason': reason, 'statement': 'PRIVATE_SENTINEL', 'rejected_value': 998877}
    clean = sanitize_issue(raw)
    assert clean['numeric_reason'] == reason and sanitize_issue(clean) == clean
    assert 'PRIVATE' not in dumps(clean) and '998877' not in dumps(clean)
    assert 'numeric_reason' not in sanitize_issue(dict(raw, code='schema_invalid'))
    assert 'numeric_reason' not in sanitize_issue(dict(raw, numeric_reason=reason + ':PRIVATE'))


@pytest.mark.parametrize('statement,reason', CASES[:5])
def test_context_reason_persists_and_api_sanitizes_without_raw_output(setup_runner, public_bundle, statement, reason, monkeypatch):
    from backend.cli_process import CLIError
    from backend.ingest import upsert_catalog
    from backend.timeutil import iso
    runner, _, calls, *_ = setup_runner
    # Reproduce the original false privacy alarm deterministically: a legitimate
    # audit clock contains the same digits as the rejected public coefficient.
    audit_clock = '2026-11-01T04:26:36.312559Z'
    monkeypatch.setattr('backend.cli_runner.iso', lambda value=None: audit_clock if value is None else iso(value))
    upsert_catalog(runner.store, [{'symbol': 'VALE3', 'name': 'Isolated public fixture', 'assetType': 'stock'}])
    with runner.store.transaction() as con:
        con.execute('INSERT INTO evidence_bundles VALUES(?,?,?,?,?,?)', (
            public_bundle['bundle_id'], public_bundle['asset']['asset_id'], public_bundle['as_of'],
            digest(public_bundle), dumps(public_bundle), iso()))
    job = runner.store.enqueue('analyse', {'asset_id': public_bundle['asset']['asset_id']}, 'isolated-context')
    def rejected_output(*args, **kwargs):
        envelope = json.loads(kwargs['stdin'].decode().rsplit('\n\n', 1)[1])
        value = analysis_with_claim(envelope['evidence_bundle'], 'PRIVATE_SENTINEL ' + statement)
        value['analysis_id'] = envelope['analysis_id']
        calls.append(1)
        return encode_events(value)
    runner.executor = rejected_output
    with pytest.raises(CLIError, match='cli_analysis_validation_rejected'):
        runner.run(public_bundle, job_id=job['job_id'], repair_limit=0)
    assert calls == [1]  # Injected local executor; no model process.
    stored = runner.store.rows("SELECT payload_json FROM audit_events WHERE event_type='analysis_validation'")[0]['payload_json']
    payload = json.loads(stored)
    assert set(payload) == {'run_id', 'job_id', 'diagnostic'}
    assert payload['job_id'] == job['job_id']
    assert payload['diagnostic'] == expected_context_diagnostic(public_bundle, reason)
    assert 'PRIVATE_SENTINEL' not in stored
    # Exercise the real GET handler and its second sanitization without opening
    # sockets, starting lifespan/worker, or posting to any application.
    app = create_app(runner.store.path, profile='real')
    endpoint = next(route.endpoint for route in app.routes if route.path == '/api/v1/assets/{asset_id}/analysis-job')
    response = endpoint(public_bundle['asset']['asset_id'])
    record = response['validation_diagnostics'][0]
    assert record['recorded_at'] == audit_clock
    assert '1255' in record['recorded_at']  # The former whole-response assertion would fail.
    assert record['diagnostic'] == expected_context_diagnostic(public_bundle, reason)
    assert 'PRIVATE_SENTINEL' not in dumps(response)
    assert not runner.store.rows('SELECT * FROM analyses')


@pytest.mark.parametrize('field,value', [('statement', 'PRIVATE_SENTINEL'), ('rejected_value', 1255),
                                        ('evidence_ids', ['1255'])])
def test_structural_privacy_assertion_still_detects_values_or_unresolved_ids(public_bundle, field, value):
    expected = expected_context_diagnostic(public_bundle, 'filing_period_ambiguous')
    changed = copy.deepcopy(expected)
    changed['issues'][0][field] = value
    with pytest.raises(AssertionError):
        assert changed == expected
