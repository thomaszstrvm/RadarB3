"""Compact, replayable whole-capture evidence for bundle v1.2."""
import json

from .core import digest,dumps
from .price_vintages import validate_vintage,adjusted_metrics,POLICY,PRICE_BASIS
from .timeutil import news_window,instant,SAO_PAULO

CONTRACT='brapi_price_vintage_v1'


def financial_series(normalized,as_of):
    start,end=news_window(as_of)
    lower=instant(start).astimezone(SAO_PAULO).date().isoformat()
    upper=instant(end).astimezone(SAO_PAULO).date().isoformat()
    return [{key:bar[key] for key in ('session_date','open','high','low','close','adjustedClose','volume')}
            for bar in normalized['bars'] if lower<=bar['session_date']<=upper]


def make_evidence(record,asset,as_of):
    content=dumps({'contract':CONTRACT,'vintage_id':record['vintage_id'],
        'response_body':record['response_body'].decode('utf-8'),'response_sha256':record['response_sha256'],
        'policy':json.loads(record['policy_json']),'policy_sha256':record['policy_sha256'],
        'material_sha256':record['material_sha256'],
        'calendar_ref':json.loads(record['calendar_json'])['package_id'],
        'financial_series':financial_series(record['normalized'],as_of)})
    return {'evidence_id':record['vintage_id'],'record_type':'price','source_id':'brapi',
        'url':record['source_url'],'title':asset['ticker']+' · captura diária integral revisável',
        'content':content,'content_sha256':digest(content),'access_level':'structured',
        'published_at':None,'modified_at':None,'first_seen_at':record['available_at'],
        'fetched_at':record['fetched_at'],'available_at':record['available_at'],
        'market_timestamp':None,'timestamp_precision':'date','publisher_group':None,'original_publisher':None,
        'entity_ids':[asset['asset_id']],'quality_flags':list(POLICY['limitations'])}


def is_vintage(item):
    if item.get('evidence_id','').startswith('price_vintage_'):return True
    try:return json.loads(item['content']).get('contract')==CONTRACT
    except (ValueError,TypeError,AttributeError,KeyError):return False


def validate_evidence(item,asset,calendar,as_of):
    content=json.loads(item['content'])
    if set(content)!={'contract','vintage_id','response_body','response_sha256','policy','policy_sha256','material_sha256','calendar_ref','financial_series'}:
        raise ValueError('price_vintage_content_contract')
    if (content['contract']!=CONTRACT or content['calendar_ref']!=calendar['package_id']
        or item['evidence_id']!=content['vintage_id'] or item['record_type']!='price'
        or item['source_id']!='brapi' or item['entity_ids']!=[asset['asset_id']]
        or item['access_level']!='structured' or item['market_timestamp'] is not None
        or item['timestamp_precision']!='date' or item['first_seen_at']!=item['available_at']
        or item['published_at'] is not None or item['modified_at'] is not None
        or item['quality_flags']!=POLICY['limitations']):
        raise ValueError('price_vintage_envelope_mismatch')
    record={'asset_id':asset['asset_id'],'provider':item['source_id'],'source_url':item['url'],
            'vintage_id':content['vintage_id'],'response_body':content['response_body'].encode('utf-8'),
            'response_sha256':content['response_sha256'],'policy_json':dumps(content['policy']),
            'policy_sha256':content['policy_sha256'],'calendar_json':dumps(calendar),
            'fetched_at':item['fetched_at'],'available_at':item['available_at'],'material_sha256':content['material_sha256']}
    normalized=validate_vintage(record,asset,as_of)
    if content['financial_series']!=financial_series(normalized,as_of):raise ValueError('price_financial_projection_mismatch')
    return normalized


def validate_metrics(bundle,normalized,item):
    expected=adjusted_metrics(normalized,bundle['calendar_package'],bundle['as_of'],item['evidence_id'])
    if len(bundle['computed_metrics'])!=len(expected):raise ValueError('vintage_metric_set_mismatch')
    for actual,computed in zip(bundle['computed_metrics'],expected):
        if {k:v for k,v in actual.items() if k!='evidence_id'}!={k:v for k,v in computed.items() if k!='evidence_id'}:
            raise ValueError('vintage_metric_recalculation_mismatch')
