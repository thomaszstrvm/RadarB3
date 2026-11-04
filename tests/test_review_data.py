"""Independent regression checks: no network or model invocation."""
import json
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from backend.api import create_app
from backend.core import dumps
from backend.evidence import build_bundle
from backend.ingest import ingest_article, upsert_catalog
from backend.timeutil import iso

@pytest.fixture
def review_client(tmp_path):
    app=create_app(tmp_path/'review.sqlite3')
    upsert_catalog(app.state.store,[{'symbol':'TEST3','name':'Test','assetType':'stock'}])
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        client.headers['x-radar-token']=client.get('/api/v1/session').json()['token']
        yield client

def test_review_horizon_setting_reaches_bundle(review_client):
    assert review_client.patch('/api/v1/settings',json={'horizons_days':[30]}).status_code==200
    bundle=build_bundle(review_client.app.state.store,'brapi:TEST3')
    assert bundle['forecast_horizons_days']==[30]

def test_review_asset_analysis_uses_its_bundle(review_client):
    store=review_client.app.state.store
    old='2026-10-25T12:00:00+00:00'; new='2026-10-26T12:00:00+00:00'
    with store.transaction() as con:
        for key,cutoff in [('old',old),('new',new)]:
            payload={'computed_metrics':[{'marker':key}],'evidence':[{'marker':key}]}
            con.execute('INSERT INTO evidence_bundles VALUES(?,?,?,?,?,?)',(key,'brapi:TEST3',cutoff,key,dumps(payload),cutoff))
        con.execute('INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?,?,?)',('analysis','brapi:TEST3','old',old,'model','prompt','schema','insufficient_data',dumps({'analysis_id':'analysis','marker':'old'}),old))
    result=review_client.get('/api/v1/assets/brapi:TEST3').json()
    assert result['analysis']['marker']=='old'
    assert result['evidence'][0]['marker']=='old'

def test_review_expired_article_does_not_leave_current_event(review_client):
    now=datetime.now(timezone.utc)
    source={'id':'fin_infomoney','allowed_hostnames':['www.infomoney.com.br'],'publisher_group':'test'}
    ingest_article(review_client.app.state.store,{'url':'https://www.infomoney.com.br/mercados/old','title':'TEST3 old event','published_at':iso(now-timedelta(days=100)),'fetched_at':iso(now-timedelta(days=2))},source)
    assert review_client.get('/api/v1/overview').json()['counts']['articles']==0
    assert review_client.get('/api/v1/events').json()['total']==0

def test_review_provider_states_are_visible(review_client):
    store=review_client.app.state.store
    store.provider_status('brapi_catalog','available',details={'probe':'catalog'})
    store.provider_status('brapi_prices','partial',details={'probe':'prices'})
    store.provider_status('br_g1:gdelt','unavailable','http_429')
    states=review_client.get('/api/v1/sources').json()['items']
    assert any(s.get('details',{}).get('probe')=='catalog' for s in states)
    assert any(s.get('error')=='http_429' for s in states)

def test_review_article_detail_not_lost_after_5000_recent_items(review_client):
    store=review_client.app.state.store
    now=iso()
    with store.transaction() as con:
        for index in range(5001):
            aid=f'article_{index:05d}'; rid=f'revision_{index:05d}'
            con.execute('INSERT INTO articles VALUES(?,?,?,?,?,?)',(aid,f'https://www.infomoney.com.br/mercados/{index}','fin_infomoney',now,rid,aid))
            con.execute('INSERT INTO article_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(rid,aid,rid,'Recent headline','Metadata','headline_only',now,None,now,now,'instant','test',None,'[]'))
    assert review_client.get('/api/v1/articles/article_05000').status_code==200
