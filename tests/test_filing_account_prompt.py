"""Canonical filing labels: synthetic prose, public fixture, fake executor only.

This module never opens the temporary rejected-claim capture.
"""
import hashlib
import json
import re
import shutil
from decimal import Decimal

import pytest

from backend.cli_process import CLIError, ProcessResult
from backend.cli_runner import MODEL, POLICY_VERSION, REASONING_EFFORT
from backend.core import digest, dumps
from backend.evidence import validate_analysis
from backend.ingest import upsert_catalog
from backend.numeric_support import FUNDAMENTAL_ACCOUNTS, numbers_supported, numeric_rejection_reason
from backend.timeutil import iso
from test_cli_runner import ROOT, encode_events, setup_runner
from test_vintage_bundle import analysis_for


QA = ROOT / 'docs/qa-20261101/bounded-rejection-qa/account-label-fix'
PUBLIC = ROOT / 'docs/qa-20261101/manual-ai-v1.2'
LABELS = {
    'net_sales_revenue': 'receita',
    'net_income_attributable_to_vale_shareholders': 'lucro',
    'net_cash_from_operating_activities': 'caixa operacional',
    'cash_and_cash_equivalents': 'saldo de caixa',
}
PERIODS = {'quarter': ' trimestral', 'year_to_date': ' acumulado', 'instant': ''}


@pytest.fixture
def public_filing_bundle():
    bundle = json.loads((PUBLIC / 'actual-bundle.json').read_text(encoding='utf-8'))
    filing = next(item for item in bundle['evidence'] if item['record_type'] == 'filing')
    return bundle, filing, json.loads(filing['content'])['observations']


def canonical_statement(observation):
    # Compose new prose from typed public fields, never from rejected output.
    context = LABELS[observation['account']] + PERIODS[observation['period']['kind']]
    return f"{context} de {observation['currency']} {observation['value']} milhões."


def test_prompt_filing_instruction_requires_literal_labels():
    previous = (QA / 'source-before/prompts/analysis-signal-v1.2.txt').read_text(encoding='utf-8-sig')
    revised = (ROOT / 'prompts/analysis-signal-v1.2.txt').read_text(encoding='utf-8-sig')
    # This local contract must allow independent revisions of metric guidance.
    old = next(part for part in previous.split('\n\n') if part.startswith('O manifesto disponível'))
    new = next(part for part in revised.split('\n\n') if part.startswith('O manifesto disponível'))
    assert old != new
    assert 'Use literalmente' in new and 'sem substituí-lo por sinônimos' in new
    assert '<trimestral ou acumulado>' in new
    assert 'saldo de caixa de <moeda> <valor> <escala>.' in new
    assert 'não deve ser combinado com trimestral ou acumulado' in new
    for account, label in LABELS.items():
        assert f'"{label}"' in new
        matches = [key for key, pattern in FUNDAMENTAL_ACCOUNTS.items() if re.search(pattern, label, re.I)]
        assert matches == [account]
    assert hashlib.sha256(previous.encode()).digest() != hashlib.sha256(revised.encode()).digest()


@pytest.mark.parametrize('account,period', [
    (account, period)
    for account in LABELS
    for period in (['instant'] if account == 'cash_and_cash_equivalents' else ['quarter', 'year_to_date'])
])
def test_canonical_label_and_period_support_exact_public_observation(public_filing_bundle, account, period):
    bundle, filing, observations = public_filing_bundle
    observation = next(row for row in observations if row['account'] == account and row['period']['kind'] == period)
    statement = canonical_statement(observation)
    supported = numbers_supported(statement, [filing], modern=True)
    assert supported is True
    analysis = analysis_for(bundle)
    analysis['claims'] = [{'kind': 'fact', 'statement': statement, 'evidence_ids': [filing['evidence_id']]}]
    analysis['evidence_ids'] = [filing['evidence_id']]
    validated = bool(validate_analysis(analysis, bundle, ROOT / 'schemas'))
    assert validated is True


@pytest.mark.parametrize('mutation,reason', [
    ('unknown_label', 'filing_account_unrecognized'),
    ('two_accounts', 'filing_account_ambiguous'),
    ('no_period', 'filing_period_unrecognized'),
    ('two_periods', 'filing_period_ambiguous'),
    ('wrong_period', 'filing_period_incompatible'),
    ('wrong_currency', 'unit_mismatch'),
    ('wrong_scale', 'unit_mismatch'),
    ('missing_currency', 'unit_mismatch'),
    ('wrong_value', 'quantity_not_found'),
    ('wrong_sign', 'quantity_not_found'),
])
def test_prompt_precision_does_not_relax_financial_acceptance(public_filing_bundle, mutation, reason):
    _, filing, observations = public_filing_bundle
    observation = next(row for row in observations if row['account'] == 'net_sales_revenue' and row['period']['kind'] == 'quarter')
    statement = canonical_statement(observation)
    if mutation == 'unknown_label':
        statement = statement.replace('receita', 'fluxo financeiro')
    elif mutation == 'two_accounts':
        statement = statement.replace('receita', 'receita e lucro')
    elif mutation == 'no_period':
        statement = statement.replace(' trimestral', '')
    elif mutation == 'two_periods':
        statement = statement.replace('trimestral', 'trimestral acumulado')
    elif mutation == 'wrong_period':
        statement = statement.replace('trimestral', 'acumulado')
    elif mutation == 'wrong_currency':
        statement = statement.replace(observation['currency'], 'EUR')
    elif mutation == 'wrong_scale':
        statement = statement.replace('milhões', 'bilhões')
    elif mutation == 'missing_currency':
        statement = statement.replace(observation['currency'] + ' ', '')
    else:
        replacement = Decimal(observation['value']) + 17 if mutation == 'wrong_value' else -Decimal(observation['value'])
        statement = statement.replace(str(observation['value']), str(replacement))
    supported = numbers_supported(statement, [filing], modern=True)
    observed_reason = numeric_rejection_reason(statement, [filing], modern=True)
    assert supported is False
    assert observed_reason == reason


def test_prompt_revision_uses_normal_key_preserves_prior_history_and_retry_guard(setup_runner, public_filing_bundle, tmp_path):
    runner, _, calls, *_ = setup_runner
    bundle, filing, observations = public_filing_bundle
    upsert_catalog(runner.store, [{'symbol': 'VALE3', 'name': 'Isolated public fixture', 'assetType': 'stock'}])
    with runner.store.transaction() as con:
        con.execute('INSERT INTO evidence_bundles VALUES(?,?,?,?,?,?)', (
            bundle['bundle_id'], bundle['asset']['asset_id'], bundle['as_of'], digest(bundle), dumps(bundle), iso()))
    prompt_dir = tmp_path / 'prompts'
    shutil.copytree(ROOT / 'prompts', prompt_dir)
    runner.prompt_dir = prompt_dir
    supplement = prompt_dir / 'analysis-signal-v1.2.txt'
    revised = supplement.read_bytes()
    previous = (QA / 'source-before/prompts/analysis-signal-v1.2.txt').read_bytes()
    supplement.write_bytes(previous)

    def rejected_process(*args, **kwargs):
        calls.append('synthetic failure')
        return ProcessResult(1, b'', b'')

    runner.executor = rejected_process
    with pytest.raises(CLIError, match='cli_execution_failed'):
        runner.run(bundle, repair_limit=0)
    prior_run = runner.store.rows('SELECT * FROM analysis_runs')[0]
    prior_quota = runner.store.rows('SELECT * FROM quota_ledger')[0]
    supplement.write_bytes(revised)

    def canonical_output(*args, **kwargs):
        system, encoded = kwargs['stdin'].decode().rsplit('\n\n', 1)
        assert revised.decode('utf-8-sig') in system
        envelope = json.loads(encoded)
        assert envelope['validation_feedback_codes'] == []
        assert envelope['evidence_bundle'] == bundle
        analysis = analysis_for(bundle)
        analysis['analysis_id'] = envelope['analysis_id']
        analysis['claims'] = [{'kind': 'fact', 'statement': canonical_statement(row),
                               'evidence_ids': [filing['evidence_id']]} for row in observations]
        analysis['evidence_ids'] = [filing['evidence_id']]
        calls.append('synthetic canonical output')
        return encode_events(analysis)

    runner.executor = canonical_output
    result = runner.run(bundle, repair_limit=0)
    assert result['calls'] == 1 and result['cache_reused'] is False
    assert len(calls) == 2
    old = runner.store.rows('SELECT * FROM analysis_runs WHERE run_id=?', (prior_run['run_id'],))[0]
    assert old == prior_run
    current = next(row for row in runner.store.rows('SELECT * FROM analysis_runs') if row['run_id'] != old['run_id'])
    system = (prompt_dir / 'analysis-system.txt').read_text(encoding='utf-8-sig') + '\n\n' + revised.decode('utf-8-sig')
    expected = digest({'system': system, 'request_template': (prompt_dir / 'analysis-request.txt').read_text(encoding='utf-8-sig'),
                       'wrapper': 'json_envelope_v1', 'policy': POLICY_VERSION, 'reasoning': REASONING_EFFORT})
    schema_hash = digest(json.loads((ROOT / 'schemas/analysis-v1.2.schema.json').read_text(encoding='utf-8')))
    assert current['prompt_hash'] == expected != old['prompt_hash']
    assert current['schema_hash'] == old['schema_hash'] == schema_hash
    assert current['cache_key'] == digest([bundle['bundle_id'], MODEL, expected, schema_hash])
    assert current['cache_key'] != old['cache_key'] and current['attempt'] == 1
    assert runner.store.rows('SELECT * FROM quota_ledger WHERE reservation_id=?', (prior_quota['reservation_id'],))[0] == prior_quota
    assert runner.run(bundle, repair_limit=0)['cache_reused'] is True
    supplement.write_bytes(previous)
    with pytest.raises(CLIError, match='cli_previous_attempt_requires_review'):
        runner.run(bundle, repair_limit=0)
    assert len(calls) == len(runner.store.rows('SELECT * FROM quota_ledger')) == 2
