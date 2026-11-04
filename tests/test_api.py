import json
import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.timeutil import iso


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Unit tests must not depend on the operator's live CLI attestation.
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': False, 'reason': 'isolated test'})
    app=create_app(tmp_path/'real.sqlite3')
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        client.headers['x-radar-token']=client.get('/api/v1/session').json()['token']
        yield client


def seed(client):
    with client.app.state.store.transaction() as c:
        c.execute("INSERT INTO assets(asset_id,ticker,name,asset_type,eligible,provider,updated_at) VALUES('a','TEST3','Teste isolado','stock',1,'test',?)",(iso(),))


def test_empty_real_state_and_boundary(client):
    r=client.get('/api/v1/overview')
    assert r.status_code==200
    assert r.json()['counts']['assets']==0
    assert r.json()['mode']=='real'
    assert r.json()['ai']['enabled'] is False
    assert client.get('/api/v1/session',headers={'Host':'attacker.example'}).status_code==400
    assert client.get('/api/v1/session',headers={'Origin':'https://attacker.example'}).status_code==403
    assert client.post('/api/v1/jobs',json={'kind':'refresh'},headers={'x-radar-token':''}).status_code==403
    assert client.post('/api/v1/jobs',json={'kind':'refresh'},headers={'Sec-Fetch-Site':'cross-site'}).status_code==403


def test_job_idempotency_transitions(client):
    client.app.state.store.acquire_worker('qa-general', consumer_scope='general')
    h={'Idempotency-Key':'same-request'}
    a=client.post('/api/v1/jobs',json={'kind':'refresh'},headers=h)
    b=client.post('/api/v1/jobs',json={'kind':'refresh'},headers=h)
    assert a.status_code==202
    assert a.json()['id']==b.json()['id']
    assert client.post('/api/v1/jobs',json={'kind':'probe'},headers=h).status_code==409
    j=a.json()['id']
    assert client.post(f'/api/v1/jobs/{j}/cancel').json()['status']=='cancelled'
    assert client.post(f'/api/v1/jobs/{j}/retry').json()['status']=='queued'
    assert client.post('/api/v1/jobs',json={'kind':'shell','command':'echo no'}).status_code==422
    assert client.post('/api/v1/jobs',json={'kind':'refresh','scope':{'url':'http://localhost'}}).status_code==422


def test_watchlist_filters_and_missing_values(client):
    seed(client)
    a=client.get('/api/v1/assets?search=TEST').json()['items'][0]
    assert a['price'] is None and a['return_21'] is None
    assert a['missing_reason']
    assert client.post('/api/v1/watchlist',json={'asset_id':'a'}).status_code==200
    assert client.post('/api/v1/watchlist',json={'asset_id':'a'}).status_code==200
    assert client.get('/api/v1/assets?watchlist=true').json()['total']==1
    assert client.get('/api/v1/assets?page_size=1000').status_code==422
    assert client.get('/api/v1/assets?sort=DROP').status_code==422
    assert client.delete('/api/v1/watchlist/a').status_code==200
    assert client.get('/api/v1/watchlist').json()['items']==[]
    assert client.get('/api/v1/assets/a').json()['analysis'] is None
    assert client.get('/api/v1/assets/missing').status_code==404


def test_market_scope_event_sort_and_missing_prices_across_pages(client):
    from backend.ingest import ingest_article, ingest_price
    from backend.timeutil import SAO_PAULO, instant
    store = client.app.state.store
    now = iso()
    session = instant(now).astimezone(SAO_PAULO).date().isoformat()
    with store.transaction() as con:
        for ticker, kind, eligible in [('ZTST3','stock',1),('ATST3','stock',1),('WTST11','unit',1),
                                       ('BTST3','stock',1),('UTST11','unknown',0),('FTST11','fund',0)]:
            con.execute('INSERT INTO assets(asset_id,ticker,name,asset_type,eligible,provider,updated_at) VALUES(?,?,?,?,?,?,?)',
                        (ticker,ticker,ticker,kind,eligible,'test',now))
    source = {'id':'fin_infomoney','allowed_hostnames':['www.infomoney.com.br'],'publisher_group':'test'}
    for n,title in enumerate(['ZTST3 e ATST3: relatório anual','ZTST3 anuncia revisão de projeções']):
        ingest_article(store,{'url':f'https://www.infomoney.com.br/mercados/{n}','title':title,
                             'published_at':now,'fetched_at':now},source)
    for ticker in ('ZTST3','ATST3','UTST11','FTST11'):
        ingest_price(store,ticker,{'session_date':session,'close':10,'fetched_at':now})
    def get(**params):
        response = client.get('/api/v1/assets',params=params)
        assert response.status_code==200
        return response.json()
    first = get(type='eligible',sort='-event_count',page_size=2)
    second = get(type='eligible',sort='-event_count',page_size=2,page=2)
    assert [r['ticker'] for r in first['items']+second['items']]==['ZTST3','ATST3','BTST3','WTST11']
    assert [(r['event_count'],r['last_event_seen_date'],r['last_event_disclosed_date']) for r in first['items']]==[(2,session,None),(1,session,None)]
    assert first['items'][1]['last_event']=='ZTST3 e ATST3: relatório anual'
    assert second['items'][0]['last_event'] is None
    assert [r['ticker'] for r in get(type='unknown')['items']]==['UTST11']
    assert get()['total']==5
    assert [r['ticker'] for r in get(type='eligible',sort='-price')['items']]==['ATST3','ZTST3','BTST3','WTST11']
    assert get(type='eligible',quality='low',state='priced')['total']==2
    assert get(type='eligible',quality='unknown',state='analysed')['total']==0
    counts = client.get('/api/v1/overview').json()['market_counts']
    assert counts['priced'] == get(type='all',state='priced')['total'] == 3
    assert counts['analysed'] == get(state='analysed')['total'] == 0


def test_settings_and_rules_persist(client):
    assert client.patch('/api/v1/settings',json={'news_interval_minutes':30}).status_code==422
    assert client.patch('/api/v1/settings',json={'ai':{'enabled':True}}).status_code==422
    assert client.patch('/api/v1/settings',json={'horizons_days':[]}).status_code==422
    assert client.patch('/api/v1/settings',json={'theme':'dark'}).json()['theme']=='dark'
    r=client.post('/api/v1/alert-rules',json={'name':'Notícias','kind':'new_event'}).json()
    assert client.patch('/api/v1/alert-rules/'+r['id'],json={'enabled':False}).json()['enabled'] is False
    assert len(client.get('/api/v1/alert-rules').json()['items'])==1
    assert client.delete('/api/v1/alert-rules/'+r['id']).status_code==200


def test_read_only_routes_have_no_collection_side_effect(client):
    for route in ['health/live','health/ready','overview','assets','events','sources','coverage','jobs','alerts','alert-rules','settings','analyses','exports/analyses']:
        response=client.get('/api/v1/'+route)
        assert response.status_code==200,(route,response.text)
    assert client.app.state.store.list_jobs()==[]


def test_profiles_cannot_share_database(tmp_path):
    create_app(tmp_path/'one.sqlite3',profile='real')
    with pytest.raises(Exception,match='profile'):
        create_app(tmp_path/'one.sqlite3',profile='demo')


def test_demo_never_advertises_external_ai(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.api.ai_status', lambda: {'enabled': True, 'model': 'gpt-6-astra'})
    with TestClient(create_app(tmp_path/'demo.sqlite3', profile='demo'),base_url='http://127.0.0.1:8000') as client:
        result=client.get('/api/v1/settings').json()
        assert result['ai']['enabled'] is False
        assert result['ai']['error_code']=='cli_demo_analysis_disabled'
