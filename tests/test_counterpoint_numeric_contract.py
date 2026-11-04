"""Finite v1.2 counterpoint contract audit; synthetic prose, archived public data.

No retained/rejected model statement, authorization, live store or executor is read.
The named cases enumerate a grammar sample, not all Portuguese financial prose.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from backend.evidence import EvidenceError, validate_analysis
from backend.numeric_support import quantities
from backend.validation_diagnostics import diagnostic_for
from test_vintage_bundle import analysis_for


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'docs/qa-20261101/manual-ai-v1.2/actual-bundle.json'
QA = ROOT / 'docs/qa-20261101/bounded-rejection-qa/counterpoint-contract'
LABELS = {
    'net_sales_revenue': 'receita',
    'net_income_attributable_to_vale_shareholders': 'lucro',
    'net_cash_from_operating_activities': 'caixa operacional',
    'cash_and_cash_equivalents': 'saldo de caixa',
}
METRICS = {
    'daily_return': ('provider_adjusted_change_1', 'Variação'),
    'weekly_return': ('provider_adjusted_change_5', 'Retorno'),
    'negative_return': ('provider_adjusted_change_21', 'Retorno'),
    'daily_volatility': ('provider_adjusted_daily_volatility_20', 'Volatilidade diária'),
    'annualized_volatility': ('provider_adjusted_annualized_volatility_20', 'Volatilidade anualizada'),
    'positive_alignment': ('technical_alignment_5', 'Alinhamento'),
    'negative_alignment': ('technical_alignment_21', 'Alinhamento'),
}
PRICE_FIELDS = {'open': 'abertura', 'high': 'máxima', 'low': 'mínima',
                'close': 'close informado', 'adjustedClose': 'adjustedClose', 'volume': 'volume'}

# Each row is (family, variant, expected acceptance under the prompt contract).
CASES = [('metric', name, True) for name in METRICS]
CASES += [('metric_alias', name, True) for name in ('fraction_pt', 'decimal_comma', 'explicit_plus', 'unicode_minus')]
CASES += [('metric_invalid', name, False) for name in (
    'percent_conversion', 'rounded', 'sign_reversed', 'missing_unit', 'wrong_unit',
    'annualized_as_fraction', 'dimensionless_as_fraction',
    'currency_added', 'scale_added', 'wrong_metric_family', 'uncited_metric', 'numeric_horizon')]
CASES += [('price', field, True) for field in PRICE_FIELDS]
CASES += [('price_alias', name, True) for name in ('symbol_prefix', 'currency_suffix', 'reais_suffix', 'decimal_comma', 'explicit_plus')]
CASES += [('price_invalid', name, False) for name in (
    'wrong_currency', 'scale_added', 'two_fields', 'missing_field', 'sign_reversed',
    'denominator', 'suffix_continuation', 'leading_decimal', 'wrong_field_value', 'conflicting_prefixes')]
CASES += [('filing', account + ':' + period, True) for account in LABELS
          for period in (['instant'] if account == 'cash_and_cash_equivalents' else ['quarter', 'year_to_date'])]
CASES += [('filing_alias', name, True) for name in ('symbol_prefix', 'mi', 'decimal_comma', 'grouping_pt', 'grouping_en')]
CASES += [('filing_invalid', name, False) for name in (
    'wrong_currency', 'wrong_scale', 'wrong_scale_alias', 'wrong_account', 'wrong_period', 'no_period',
    'two_accounts', 'two_periods', 'wrong_value', 'growth_comparison')]
CASES += [('missing', name, accepted) for name, accepted in (
    ('return_words', True), ('qualitative_hypothesis', True), ('invented_zero', False), ('legacy_r63_label', False))]
CASES += [('metadata', name, False) for name in ('session_date', 'publication_year', 'horizon', 'numeric_identifier')]
CASES += [('formula_label', name, accepted) for name, accepted in (
    ('M5', False), ('M21', False), ('s20', False),
    ('alignment_five_words', True), ('alignment_twenty_one_words', True), ('deviation_twenty_words', True))]


def load_bundle():
    return json.loads(PUBLIC.read_text(encoding='utf-8'))


def build_case(bundle, family, variant):
    evidence = bundle['evidence']
    price = next(item for item in evidence if item['record_type'] == 'price')
    filing = next(item for item in evidence if item['record_type'] == 'filing')
    metrics = {json.loads(item['content'])['metric_id']: item for item in evidence if item['record_type'] == 'computed_metric'}
    series = json.loads(price['content'], parse_float=Decimal)['financial_series']
    observations = json.loads(filing['content'])['observations']

    if family.startswith('metric'):
        key = variant if family == 'metric' else 'daily_return'
        if variant == 'unicode_minus':
            key = 'negative_return'
        elif variant == 'annualized_as_fraction':
            key = 'annualized_volatility'
        elif variant == 'dimensionless_as_fraction':
            key = 'positive_alignment'
        item_id, label = METRICS[key]
        item = metrics[item_id]
        value = json.loads(item['content'], parse_float=Decimal)['value']
        unit = json.loads(item['content'])['unit']
        text = f'{label} de {value} {unit}.'
        refs = [item['evidence_id']]
        if family == 'metric_alias':
            if variant == 'fraction_pt': text = text.replace('fraction', 'fração')
            elif variant == 'decimal_comma': text = text.replace(str(value), str(value).replace('.', ','))
            elif variant == 'explicit_plus': text = text.replace(str(value), '+' + str(value))
            elif variant == 'unicode_minus': text = text.replace('-', '−')
        if family == 'metric_invalid':
            if variant == 'percent_conversion': text = f'{label} de {value * 100}%.'
            elif variant == 'rounded': text = f'{label} de {value:.4f} {unit}.'
            elif variant == 'sign_reversed': text = f'{label} de {-value} {unit}.'
            elif variant == 'missing_unit': text = f'{label} de {value}.'
            elif variant == 'wrong_unit': text = text.replace(unit, 'annualized_fraction')
            elif variant in {'annualized_as_fraction', 'dimensionless_as_fraction'}: text = text.replace(unit, 'fraction')
            elif variant == 'currency_added': text = f'{label} de BRL {value} {unit}.'
            elif variant == 'scale_added': text = f'{label} de {value} milhões {unit}.'
            elif variant == 'wrong_metric_family': text = text.replace(label, 'Alinhamento')
            elif variant == 'uncited_metric': refs = [price['evidence_id']]
            elif variant == 'numeric_horizon': text = text.replace(' de ', ' de 30 dias: ', 1)
        return text, refs

    if family.startswith('price'):
        field = variant if family == 'price' else 'close'
        row = next(row for row in series if row[field] is not None)
        value = row[field]
        label = PRICE_FIELDS[field]
        amount = str(value) if field == 'volume' else 'BRL ' + str(value)
        text = f'{label} de {amount}.'
        if family == 'price_alias':
            if variant == 'symbol_prefix': text = text.replace('BRL', 'R$')
            elif variant == 'currency_suffix': text = f'{label} de {value} BRL.'
            elif variant == 'reais_suffix': text = f'{label} de {value} reais.'
            elif variant == 'decimal_comma': text = text.replace(str(value), str(value).replace('.', ','))
            elif variant == 'explicit_plus': text = text.replace('BRL ', 'BRL +')
        if family == 'price_invalid':
            if variant == 'wrong_currency': text = text.replace('BRL', 'USD')
            elif variant == 'scale_added': text = f'{label} de BRL {value} milhões.'
            elif variant == 'two_fields': text = text.replace(label, label + ' e adjustedClose')
            elif variant == 'missing_field': text = text.replace(label, 'cotação')
            elif variant == 'sign_reversed': text = f'{label} de BRL {-value}.'
            elif variant == 'denominator': text = f'{label} de BRL {value} por dia.'
            elif variant == 'suffix_continuation': text = f'{label} de {value} reais na sessão.'
            elif variant == 'leading_decimal': text = f'{label} de BRL .{value}.'
            elif variant == 'conflicting_prefixes': text = f'{label} de R$ USD {value}.'
            elif variant == 'wrong_field_value':
                adjusted_values = {row['adjustedClose'] for row in series}
                value = next(row['close'] for row in series if row['close'] not in adjusted_values)
                text = f'adjustedClose de BRL {value}.'
        return text, [price['evidence_id']]

    if family.startswith('filing'):
        account, period = variant.split(':') if family == 'filing' else ('net_sales_revenue', 'quarter')
        observation = next(row for row in observations if row['account'] == account and row['period']['kind'] == period)
        value = Decimal(observation['value'])
        context = LABELS[account] + {'quarter': ' trimestral', 'year_to_date': ' acumulado', 'instant': ''}[period]
        text = f'{context} de USD {value} milhões.'
        if family == 'filing_alias':
            if variant == 'symbol_prefix': text = text.replace('USD', 'US$')
            elif variant == 'mi': text = text.replace('milhões', 'mi')
            elif variant == 'decimal_comma': text = text.replace(str(value), str(value) + ',0')
            elif variant == 'grouping_pt': text = text.replace(str(value), f'{value:,.1f}'.replace(',', '_').replace('.', ',').replace('_', '.'))
            elif variant == 'grouping_en': text = text.replace(str(value), f'{value:,.1f}')
        if family == 'filing_invalid':
            if variant == 'wrong_currency': text = text.replace('USD', 'BRL')
            elif variant == 'wrong_scale': text = text.replace('milhões', 'bilhões')
            elif variant == 'wrong_scale_alias': text = text.replace('milhões', 'bi')
            elif variant == 'wrong_account': text = text.replace('receita', 'lucro')
            elif variant == 'wrong_period': text = text.replace('trimestral', 'acumulado')
            elif variant == 'no_period': text = text.replace(' trimestral', '')
            elif variant == 'two_accounts': text = text.replace('receita', 'receita e lucro')
            elif variant == 'two_periods': text = text.replace('trimestral', 'trimestral acumulado')
            elif variant == 'wrong_value': text = text.replace(str(value), str(value + 17))
            elif variant == 'growth_comparison': text = text.replace('receita', 'receita cresceu')
        return text, [filing['evidence_id']]

    if family == 'missing':
        missing = next(metric for metric in bundle['computed_metrics'] if metric['metric_id'] == 'provider_adjusted_change_63')
        # Null metrics have no computed-metric evidence record to cite. Use the
        # underlying price evidence for the qualitative coverage limitation.
        assert missing['value'] is None and missing['evidence_id'] is None
        text = {
            'return_words': 'Retorno da janela de sessenta e três sessões ausente por amostra insuficiente.',
            'qualitative_hypothesis': 'Hipótese: indisponibilidade de volume limitaria a leitura da atividade de negociação.',
            'invented_zero': 'Retorno da janela longa de 0 fraction.',
            'legacy_r63_label': 'R63 ausente por falta de sessenta e quatro observações.',
        }[variant]
        return text, [price['evidence_id']]

    if family == 'metadata':
        text = {
            'session_date': f"Sessão em {series[0]['session_date']}.",
            'publication_year': 'close informado de BRL ' + filing['published_at'][:4] + '.',
            'horizon': 'Alinhamento de 30 dimensionless.',
            'numeric_identifier': 'provider_adjusted_change_63 ausente.',
        }[variant]
        return text, [price['evidence_id']] if variant != 'horizon' else [metrics['technical_alignment_5']['evidence_id']]
    if family == 'formula_label':
        item_id, label = {
            'M5': ('technical_alignment_5', 'M5'),
            'M21': ('technical_alignment_21', 'M21'),
            's20': ('provider_adjusted_daily_volatility_20', 's20'),
            'alignment_five_words': ('technical_alignment_5', 'Alinhamento da janela de cinco sessões'),
            'alignment_twenty_one_words': ('technical_alignment_21', 'Alinhamento da janela de vinte e uma sessões'),
            'deviation_twenty_words': ('provider_adjusted_daily_volatility_20', 'Desvio diário da janela de vinte sessões'),
        }[variant]
        item = metrics[item_id]
        content = json.loads(item['content'], parse_float=Decimal)
        return f"{label} de {content['value']} {content['unit']}.", [item['evidence_id']]
    raise AssertionError('Unknown enumerated case family')


def evaluate_counterpoint(bundle, text, references):
    analysis = analysis_for(bundle)
    analysis['signal']['counterpoints'] = [text]
    analysis['signal']['evidence_ids'] = references
    analysis['evidence_ids'] = references
    try:
        validate_analysis(analysis, bundle, ROOT / 'schemas')
    except EvidenceError as error:
        issue = diagnostic_for(error, bundle)['issues'][0]
        return {'accepted': False, 'code': issue['code'], 'path': issue['path'],
                'reason': issue.get('numeric_reason')}
    return {'accepted': True, 'code': None, 'path': None, 'reason': None}


PARAMETERS = [pytest.param(family, variant, expected, id=family + '-' + variant)
              for family, variant, expected in CASES]


@pytest.mark.parametrize('family,variant,expected', PARAMETERS)
def test_finite_counterpoint_family_matches_prompt(family, variant, expected):
    bundle = load_bundle()
    text, references = build_case(bundle, family, variant)
    result = evaluate_counterpoint(bundle, text, references)
    assert result['accepted'] is expected
    if not expected:
        assert result['path'] == '/signal/counterpoints/0'
        assert result['code'] in {'factual_number_not_in_cited_evidence', 'fundamental_comparison_unsupported'}


def test_named_missing_metric_conflict_is_reproduced_at_counterpoint_path():
    prompt = (ROOT / 'prompts/analysis-signal-v1.2.txt').read_text(encoding='utf-8-sig')
    previous = (QA / 'source-before/prompts/analysis-signal-v1.2.txt').read_text(encoding='utf-8-sig')
    assert 'R63 ausente por falta de sessenta e quatro observações' in previous
    assert 'R63' not in prompt
    assert 'Retorno da janela de sessenta e três sessões ausente' in prompt
    assert 'Em textos livres, inclusive contrapontos' in prompt
    assert 'sem rótulos ou símbolos com algarismos' in prompt
    assert 'IDs permanecem apenas nos campos estruturados próprios' in prompt
    assert 'Preserve as unidades literais exigidas acima' in prompt
    bundle = load_bundle()
    text, references = build_case(bundle, 'missing', 'legacy_r63_label')
    assert evaluate_counterpoint(bundle, text, references) == {
        'accepted': False, 'code': 'factual_number_not_in_cited_evidence',
        'path': '/signal/counterpoints/0', 'reason': 'unsupported_format'}
    words, references = build_case(bundle, 'missing', 'return_words')
    result = evaluate_counterpoint(bundle, words, references)
    assert result['accepted'] is True


@pytest.mark.parametrize('left,right', [
    ('R$ 3.875', 'BRL 3.875'),
    ('US$ 3.875', 'USD 3.875'),
    ('de 3.875 BRL.', 'de 3.875 reais.'),
    ('USD 3.875 milhões', 'USD 3.875 mi'),
    ('USD 3.875 milhão', 'USD 3.875 milhões'),
    ('USD 3.875 bilhões', 'USD 3.875 bi'),
    ('USD 3.875 bilhão', 'USD 3.875 bilhões'),
    ('3.875 fraction', '3.875 fração'),
], ids=['brl_prefix', 'usd_prefix', 'brl_suffix', 'million_mi', 'million_singular',
        'billion_bi', 'billion_singular', 'fraction_pt'])
def test_finite_literal_aliases_normalize_without_currency_or_scale_conversion(left, right):
    # Lexical equivalence only. The public filing contains no billion-denominated
    # observation, so this does not claim that a billion fact passes that bundle.
    assert quantities(left) == quantities(right)
    assert len(quantities(left)) == 1 and quantities(left)[0][0] == 'quantity'
    assert quantities('USD 3.875 milhões') != quantities('USD 3.875 bilhões')
    assert quantities('USD 3.875 milhões') != quantities('BRL 3.875 milhões')
