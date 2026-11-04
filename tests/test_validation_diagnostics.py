import json
import pytest
from test_cli_runner import setup_runner, encode_events, final_analysis, ROOT
from backend.core import QuotaExceeded
from backend.evidence import EvidenceError, validate_analysis
from backend.validation_diagnostics import diagnostic_for

def test_rejection_preserved_when_repair_has_no_budget(setup_runner):
    runner, bundle, calls, *_ = setup_runner
    runner.store.set_setting('max_analyses_per_day', 1)
    def invalid(args, **kw):
        calls.append(1)
        return encode_events({'private-unexpected-key': 'SECRET-SENTINEL'})
    runner.executor = invalid
    with pytest.raises(QuotaExceeded):
        runner.run(bundle)
    events = runner.store.rows("SELECT payload_json FROM audit_events WHERE event_type='analysis_validation'")
    assert len(events) == len(calls) == 1
    assert 'SECRET' not in events[0]['payload_json'] and 'private-unexpected' not in events[0]['payload_json']
    data = json.loads(events[0]['payload_json'])
    assert data['diagnostic']['stage'] == 'schema'
    assert data['diagnostic']['issues'][0]['code'] == 'schema_invalid'
    assert runner.store.rows('SELECT status FROM quota_ledger') == [{'status':'consumed'}]
    assert not runner.store.rows('SELECT * FROM analyses')

def test_unknown_exception_is_redacted():
    assert diagnostic_for(EvidenceError('SECRET-SENTINEL'))['issues'][0]['code'] == 'validation_error_unclassified'
    assert 'SECRET' not in json.dumps(diagnostic_for(EvidenceError('SECRET-SENTINEL')))

def test_semantic_rule_is_specific(setup_runner):
    runner, bundle, *_ = setup_runner
    value = final_analysis({'analysis_id':'a','evidence_bundle':bundle})
    value['asset_id'] = 'wrong'
    with pytest.raises(EvidenceError) as error:
        validate_analysis(value,bundle,ROOT/'schemas','a')
    assert diagnostic_for(error.value)['issues'][0]['code'] == 'analysis_identity_or_time_mismatch'

def test_diagnostic_api_is_correlated_and_survives_reopen(setup_runner):
    from backend.api import create_app
    from fastapi.testclient import TestClient
    runner, bundle, calls, *_ = setup_runner
    job = runner.store.enqueue('analyse', {'asset_id':bundle['asset']['asset_id']}, 'synthetic-job')
    runner.store.set_setting('max_analyses_per_day',1)
    runner.executor = lambda *a, **kw: encode_events({'secret-key':'secret-value'})
    with pytest.raises(QuotaExceeded):
        runner.run(bundle, job_id=job['job_id'])
    with TestClient(create_app(runner.store.path,profile='real'),base_url='http://127.0.0.1:8000') as client:
        response = client.get('/api/v1/assets/brapi:TEST3/analysis-job')
        assert response.status_code == 200
        diagnostics = response.json()['validation_diagnostics']
        assert len(diagnostics) == 1
        assert diagnostics[0]['attempt'] == 1
        assert 'secret' not in json.dumps(diagnostics)
    assert len(runner.store.rows('SELECT * FROM quota_ledger')) == 1

@pytest.mark.parametrize('number,accepted', [('41,70',True),('041.700',True),('41.71',False)])
def test_decimal_equivalence_without_rounding(number, accepted, monkeypatch):
    monkeypatch.setattr('backend.evidence.validate_bundle', lambda *args: True)
    bundle = {'asset':{'asset_id':'synthetic'},'as_of':'2026-10-26T12:00:00Z',
              'evidence':[{'evidence_id':'cited','content':'41.7'}, {'evidence_id':'uncited','content':'41.71'}], 'forecast_horizons_days':[30]}
    value = final_analysis({'analysis_id':'a','evidence_bundle':bundle})
    value['claims'] = [{'kind':'fact','statement':f'Preço {number}', 'evidence_ids':['cited']}]
    value['evidence_ids'] = ['cited']
    if accepted:
        assert validate_analysis(value,bundle)
    else:
        with pytest.raises(EvidenceError,match='factual_number_not_in_cited_evidence'):
            validate_analysis(value,bundle)


def test_numeric_diagnostic_identifies_claim_without_rejected_content(monkeypatch):
    monkeypatch.setattr('backend.evidence.validate_bundle', lambda *args: True)
    bundle = {'asset': {'asset_id': 'synthetic'}, 'as_of': '2026-10-26T12:00:00Z',
              'evidence': [{'evidence_id': 'cited', 'content': '41.7'}], 'forecast_horizons_days': [30]}
    value = final_analysis({'analysis_id': 'a', 'evidence_bundle': bundle})
    value['claims'] = [
        {'kind': 'inference', 'statement': 'Conditional interpretation.', 'evidence_ids': ['cited']},
        {'kind': 'fact', 'statement': 'PRIVATE_SENTINEL 99', 'evidence_ids': ['cited']},
    ]
    value['evidence_ids'] = ['cited']
    with pytest.raises(EvidenceError, match='factual_number_not_in_cited_evidence') as error:
        validate_analysis(value, bundle)
    diagnostic = diagnostic_for(error.value)
    assert diagnostic['issues'] == [{'code': 'factual_number_not_in_cited_evidence', 'path': '/claims/1/statement', 'rule': None, 'claim_kind': 'fact', 'numeric_reason': 'quantity_not_found'}]
    assert 'PRIVATE_SENTINEL' not in json.dumps(diagnostic) and '99' not in json.dumps(diagnostic)
