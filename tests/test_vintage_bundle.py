"""Whole-vintage contract, deterministic eligibility and numeric citation gates."""
import copy
import json
import math

import pytest

from backend import fundamentals
from backend.core import digest,dumps
from backend.evidence import build_bundle,validate_bundle,validate_analysis,EvidenceError
from backend.numeric_support import numbers_supported
from backend.technical_policy import classify_alignment,evaluate_policy,signal_method
from test_price_vintages import store,body,capture,prices,ASSET,NOW,ROOT,PRIOR,iso
from test_cli_runner import final_analysis


def filing(store,monkeypatch):
    monkeypatch.setattr(fundamentals,'iso',lambda value=None:iso(value or '2026-11-01T03:19:00Z'))
    path=ROOT/'docs/qa-20261031/data-readiness/fundamentals'
    with store.connect() as con:
        fundamentals.import_fundamentals(con,json.loads((path/'vale3-3t26-reviewed-v1.json').read_text(encoding='utf-8')),
                                    path/'vale-3t26-earnings-release.pdf',expected_profile='demo')


def built(store,monkeypatch,direction=None):
    filing(store,monkeypatch)
    data=json.loads(body())
    if direction:
        for i,bar in enumerate(data['results'][0]['historicalDataPrice']):
            bar['adjustedClose']=100*math.exp(direction*.01*i+.002*math.sin(i))
    capture(store,dumps(data).encode())
    return build_bundle(store,ASSET['asset_id'],NOW,ROOT/'schemas')


def analysis_for(bundle,action='insufficient_data'):
    result=final_analysis({'analysis_id':'synthetic_vintage_analysis','schema_version':'1.1','evidence_bundle':bundle})
    result['schema_version']='1.2'
    result['signal']['method']=signal_method(bundle['technical_policy'])
    result['signal']['rationale']='Hipótese técnica experimental; contexto limitado e sujeito a revisão.'
    result['signal']['action']=action
    if action!='insufficient_data':
        refs=bundle['technical_policy']['metric_evidence_ids']+bundle['technical_policy']['filing_evidence_ids']
        refs=list(dict.fromkeys(refs))
        result['status']='supported_hypothesis' if action in {'buy','sell'} else 'inconclusive'
        result['opportunity'].update(classification={'buy':'investigate','sell':'avoid_for_now','wait':'watch'}[action],evidence_ids=refs)
        result['signal']['evidence_ids']=refs
        result['evidence_ids']=refs
        result['risks']=['A tendência pode se inverter.']
        result['scenarios']=[{'case':'conditional_case','horizon_days':30,'description':'Hipótese de manutenção da tendência.',
                             'conditions':['Se o alinhamento persistir.'],'invalidation_conditions':['Reversão do alinhamento.'],'evidence_ids':refs}]
    return result


def test_bundle_one_vintage_real_fixture_wait_and_snapshot_cache(store,monkeypatch):
    bundle=built(store,monkeypatch)
    assert bundle['schema_version']=='1.2' and len(dumps(bundle))<60000
    assert len([e for e in bundle['evidence'] if e['record_type']=='price'])==1
    assert bundle['technical_policy']['technical_candidate']=='wait'
    assert bundle['technical_policy']['eligible_action']=='wait'
    assert bundle['technical_policy']['context_origin_count']==1
    assert bundle['technical_policy']['filing_evidence_ids']==bundle['technical_policy']['context_evidence_ids']
    assert validate_analysis(analysis_for(bundle,'wait'),bundle,ROOT/'schemas')
    repeated=build_bundle(store,ASSET['asset_id'],'2026-11-01T04:00:00Z',ROOT/'schemas')
    assert repeated==bundle and len(store.rows('SELECT * FROM evidence_bundles'))==1


@pytest.mark.parametrize('direction,action',[(1,'buy'),(-1,'sell')])
def test_direction_requires_calculated_candidate_and_permits_only_downgrade(store,monkeypatch,direction,action):
    bundle=built(store,monkeypatch,direction)
    assert bundle['technical_policy']['eligible_action']==action
    for allowed in (action,'wait','insufficient_data'):
        assert validate_analysis(analysis_for(bundle,allowed),bundle,ROOT/'schemas')
    inverted=analysis_for(bundle,'sell' if action=='buy' else 'buy')
    with pytest.raises(EvidenceError,match='signal_policy_mismatch'):validate_analysis(inverted,bundle,ROOT/'schemas')


@pytest.mark.parametrize('a,b,expected',[(1,2,'wait'),(-1,-2,'wait'),(1.00000000000001,2,'buy'),(-1.00000000000001,-2,'sell'),(2,-2,'wait'),(0,0,'wait'),(None,2,'insufficient_data'),(float('inf'),2,'insufficient_data'),(float('nan'),-2,'insufficient_data')])
def test_strict_unrounded_thresholds(a,b,expected):assert classify_alignment(a,b)==expected


@pytest.mark.parametrize('field,value', [('response_sha256','0'*64),('material_sha256','0'*64),('policy_sha256','0'*64),('calendar_ref','fake'),('response_body','{}'),('financial_series',[])])
def test_tampered_content_rehashed_still_rejected(store,monkeypatch,field,value):
    bundle=built(store,monkeypatch);item=next(e for e in bundle['evidence'] if e['record_type']=='price')
    data=json.loads(item['content']);data[field]=value;item['content']=dumps(data);item['content_sha256']=digest(item['content'])
    with pytest.raises(EvidenceError):validate_bundle(bundle,ROOT/'schemas')


@pytest.mark.parametrize('field,value',[('record_type','article'),('source_id','other'),('market_timestamp',NOW),('timestamp_precision','instant'),('url','https://brapi.dev/api/quote/VALE3?token=secret'),('available_at','2026-10-31T01:00:00Z')])
def test_tampered_envelope_cannot_hide_behind_material_hash(store,monkeypatch,field,value):
    bundle=built(store,monkeypatch);item=next(e for e in bundle['evidence'] if e['record_type']=='price');item[field]=value
    with pytest.raises(EvidenceError):validate_bundle(bundle,ROOT/'schemas')


@pytest.mark.parametrize('field,value',[('value',.8),('adjustment','split_adjusted'),('start_at','2026-01-01'),('missing_reason','invented')])
def test_metric_and_policy_are_recomputed(store,monkeypatch,field,value):
    bundle=built(store,monkeypatch);bundle['computed_metrics'][0][field]=value
    with pytest.raises(EvidenceError):validate_bundle(bundle,ROOT/'schemas')


def test_policy_adulteration_cannot_promote_direction(store,monkeypatch):
    bundle=built(store,monkeypatch);bundle['technical_policy']['eligible_action']='buy'
    with pytest.raises(EvidenceError):validate_bundle(bundle,ROOT/'schemas')


def test_numeric_fields_do_not_accept_metadata_or_other_price_basis(store,monkeypatch):
    bundle=built(store,monkeypatch);series=next(e for e in bundle['evidence'] if e['record_type']=='price')
    data=json.loads(series['content']);a=data['financial_series'][0]
    assert numbers_supported(f"adjustedClose R${a['adjustedClose']}.",[series])
    assert numbers_supported(f"close informado R${a['close']}.",[series])
    assert not numbers_supported(f"close informado R${a['adjustedClose']}.",[series])
    assert not numbers_supported('close informado R$2026.',[series])
    assert not numbers_supported('Retorno 0.03280599500416326 fraction.',[series])
    metric=next(e for e in bundle['evidence'] if e['record_type']=='computed_metric' and json.loads(e['content'])['metric_id']=='provider_adjusted_change_1')
    assert numbers_supported('Variação 0.03280599500416326 fraction.',[metric])
    assert not numbers_supported('Volatilidade 0.03280599500416326 fraction.',[metric])


def test_missing_filing_is_explicit_even_for_wait(store,monkeypatch):
    bundle=built(store,monkeypatch)
    result=evaluate_policy(bundle['computed_metrics'],[],ASSET['asset_id'],NOW)
    assert 'typed_issuer_filing_missing' in result['missing_reasons']
    assert 'substantive_context_missing' in result['missing_reasons']


@pytest.mark.parametrize('values',[[1e-300,1e300]*32,[1e300,1e-300]*32,[100]*64])
def test_extreme_positive_values_and_zero_volatility_never_crash(store,values):
    data=json.loads(body())
    for bar,value in zip(data['results'][0]['historicalDataPrice'],values):bar['adjustedClose']=value
    capture(store,dumps(data).encode());selected=prices.selected_vintage(store,ASSET,NOW)
    metrics=prices.adjusted_metrics(selected['normalized'],json.loads(selected['calendar_json']),NOW,'series')
    assert all(m['missing_reason'] if m['value'] is None else math.isfinite(m['value']) for m in metrics)
    if len(set(values))==1:
        assert metrics[4]['value']==0
        assert all(m['value'] is None and m['missing_reason']=='zero_daily_volatility' for m in metrics[-2:])


def test_no_get_projection_writes_tables_or_fills_from_legacy(store):
    capture(store);before={name:store.rows('SELECT * FROM '+name) for name in ('evidence_bundles','analyses','quota_ledger','audit_events','price_vintages')}
    result=prices.price_projection(store,ASSET,NOW)
    assert len(result['bars'])==63 and result['bars'][0]['close']!=result['bars'][0]['adjustedClose']
    assert result['bars'][0]['market_timestamp'] is None
    for name,rows in before.items():assert store.rows('SELECT * FROM '+name)==rows


def test_raw_audit_body_cannot_donate_financial_values_outside_moving_window(store):
    data=json.loads(body());data['results'][0]['historicalDataPrice'][0]['adjustedClose']=77.123456789
    capture(store,dumps(data).encode())
    before=build_bundle(store,ASSET['asset_id'],'2026-11-03T18:00:00Z',ROOT/'schemas')
    after=build_bundle(store,ASSET['asset_id'],'2026-11-04T18:00:00Z',ROOT/'schemas')
    old=next(e for e in before['evidence'] if e['record_type']=='price')
    current=next(e for e in after['evidence'] if e['record_type']=='price')
    assert numbers_supported('adjustedClose R$77.123456789.',[old])
    assert not numbers_supported('adjustedClose R$77.123456789.',[current])
    content=json.loads(current['content'])
    assert '77.123456789' in content['response_body']  # Audit bytes remain complete.
    assert content['financial_series'][0]['session_date']=='2026-08-04'
    content['financial_series'].insert(0,json.loads(old['content'])['financial_series'][0])
    current['content']=dumps(content);current['content_sha256']=digest(current['content'])
    with pytest.raises(EvidenceError):validate_bundle(after,ROOT/'schemas')


@pytest.mark.parametrize('statement',[
    'Receita trimestral de US$10599 milhões.',
    'Receita acumulada de US$30355 milhões.',
    'Lucro trimestral de US$1255 milhões.',
    'Caixa operacional trimestral de US$2519 milhões.',
    'Saldo de caixa de US$4841 milhões.',
])
def test_typed_filing_observation_supports_its_account_and_period(store,monkeypatch,statement):
    bundle=built(store,monkeypatch);item=next(e for e in bundle['evidence'] if e['record_type']=='filing')
    assert numbers_supported(statement,[item],modern=True)


@pytest.mark.parametrize('statement',[
    'Receita trimestral de US$30355 milhões.',
    'Receita acumulada de US$10599 milhões.',
    'Receita trimestral de US$1255 milhões.',
    'Lucro trimestral de US$10599 milhões.',
    'Receita de US$10599 milhões.',
])
def test_typed_filing_refuses_wrong_account_period_or_unqualified_amount(store,monkeypatch,statement):
    bundle=built(store,monkeypatch);item=next(e for e in bundle['evidence'] if e['record_type']=='filing')
    assert not numbers_supported(statement,[item],modern=True)
    # Version 1.1 lexical corpus is intentionally unchanged; historical replay
    # does not acquire a new claim of full semantic validation.
    assert numbers_supported(statement,[item])


@pytest.mark.parametrize('location',['fact','inference','synthesis','rationale','counterpoints'])
def test_exact_quarter_YTD_growth_probe_rejected_in_every_modern_text_path(store,monkeypatch,location):
    bundle=built(store,monkeypatch);value=analysis_for(bundle,'wait')
    statement='A receita cresceu de US$10599 milhões no trimestre para US$30355 milhões no acumulado do ano.'
    if location in {'fact','inference'}:value['claims']=[{'kind':location,'statement':statement,'evidence_ids':bundle['technical_policy']['filing_evidence_ids']}]
    elif location=='synthesis':value['synthesis']=statement
    elif location=='counterpoints':value['signal']['counterpoints']=[statement]
    else:value['signal']['rationale']='Experimental: '+statement
    with pytest.raises(EvidenceError,match='fundamental_comparison_unsupported'):
        validate_analysis(value,bundle,ROOT/'schemas')
