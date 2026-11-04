"""Archived public response only; negative variants never leave memory."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
QA=ROOT/'docs/qa-20261031/vale3-gates'
spec=importlib.util.spec_from_file_location('daily_snapshot_policy_qa',QA/'daily_snapshot_policy_qa.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)


def inputs():
    return {'body':(QA/'brapi-public-response.json').read_bytes(),
            'response_sha256':'ca776418bafd6f621b6e49d78b7a04fc013c822cb3f2e8336fc8c5c6f3f5921e',
            'dictionary':(QA/'brapi-historical-dictionary-approved.json').read_bytes(),
            'fetched_at':'2026-11-01T02:17:43.178290Z','available_at':'2026-11-01T02:30:00.000000Z',
            'calendar_document':json.loads((QA/'calendar-package-v1.json').read_text(encoding='utf-8'))}


def test_archived_free_daily_snapshot_is_ended_but_revisable():
    proof=policy.verify_daily_snapshot(**inputs())
    assert len(proof['sessions'])==63 and proof['sessions'][0]=='2026-08-03' and proof['sessions'][-1]=='2026-10-30'
    assert proof['period_ended'] and proof['subject_to_revision'] and not proof['production_ingestion']
    assert proof['adjustment_for_price_return']=='unverified'


@pytest.mark.parametrize('change', ['body_hash','dictionary_hash','availability','before_documentation','before_close','symbol','historical_symbol','interval','closed_day','date_missing','duplicate','close','volume'])
def test_negative_snapshot_conditions_refuse_complete_vintage(change):
    args=inputs();body=json.loads(args['body']);quote=body['results'][0]
    if change=='body_hash': args['response_sha256']='0'*64
    elif change=='dictionary_hash': args['dictionary']+=b' '
    elif change=='availability': args['available_at']='2026-10-31T12:00:00Z'
    elif change=='before_documentation': args['available_at']='2026-11-01T02:20:00Z'
    elif change=='before_close': args['fetched_at']='2026-10-30T21:59:59Z'
    elif change=='symbol': quote['symbol']='OTHER3'
    elif change=='historical_symbol': quote['historicalSymbol']='OTHER3'
    elif change=='interval': quote['usedInterval']='1wk'
    elif change=='closed_day': quote['historicalDataPrice'][-1]['date']=1793484000  # Saturday, Oct 31.
    elif change=='date_missing': quote['historicalDataPrice'][-1].pop('date')
    elif change=='duplicate': quote['historicalDataPrice'].append(quote['historicalDataPrice'][-1])
    elif change=='close': quote['historicalDataPrice'][-1]['close']=None
    elif change=='volume': quote['historicalDataPrice'][-1]['volume']=-1
    if change not in ('body_hash','dictionary_hash','availability','before_documentation','before_close'):
        args['body']=json.dumps(body).encode();args['response_sha256']=hashlib.sha256(args['body']).hexdigest()
    with pytest.raises(ValueError): policy.verify_daily_snapshot(**args)
