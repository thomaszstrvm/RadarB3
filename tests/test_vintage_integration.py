"""Real local components, temporary databases, fake transport/executor only."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.adapters import BrapiAdapter
from backend.safe_http import Response
from backend.core import dumps
from backend.evidence import build_bundle
from test_price_vintages import store,body,ASSET,NOW,ROOT,iso
from test_vintage_bundle import built,analysis_for
from test_cli_runner import setup_runner,encode_events


def test_adapter_preserves_one_response_and_does_not_ingest_individual_or_current_quote(store,monkeypatch):
    monkeypatch.setattr('backend.adapters.iso',lambda value=None:iso(value or NOW))
    monkeypatch.delenv('BRAPI_TOKEN',raising=False)
    calls=[]
    class Transport:
        def get(self,url,**kwargs):
            calls.append((url,kwargs));return Response(200,body(),{})
    result=BrapiAdapter(store,Transport()).prices(ASSET)
    assert result['bars']==63 and len(calls)==1
    assert not store.rows('SELECT * FROM price_bars')
    assert len(store.rows('SELECT * FROM price_vintages'))==1
    assert not store.rows('SELECT * FROM quota_ledger')
    assert 'token' not in dumps(calls)


def test_gets_show_close_and_A_separately_without_new_bundle_analysis_or_reservation(store,monkeypatch):
    bundle=built(store,monkeypatch)
    monkeypatch.setattr('backend.api.iso',lambda value=None:iso(value or NOW))
    monkeypatch.setattr('backend.api.ai_status',lambda:{'enabled':False,'reason':'isolated'})
    app=create_app(store.path,profile='demo',execution_mode='manual-analysis')
    tables=['evidence_bundles','analyses','jobs','quota_ledger','audit_events','price_vintages','price_revisions']
    before={t:store.rows('SELECT * FROM '+t) for t in tables}
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        listing=client.get('/api/v1/assets').json()['items'][0]
        assert listing['price']==json.loads(body())['results'][0]['historicalDataPrice'][-1]['close']
        assert listing['price_timestamp_precision']=='date' and listing['price_at'] is None
        detail=client.get('/api/v1/assets/brapi:VALE3').json()
        assert detail['prices'][0]['close']!=detail['prices'][0]['adjustedClose']
        assert detail['price_snapshot']['vintage_id']==next(e['evidence_id'] for e in bundle['evidence'] if e['record_type']=='price')
        assert len(detail['current_price_metrics'])==8
        assert detail['current_price_metrics'][3]['value'] is None
        assert client.get('/api/v1/overview').json()['counts']['priced']==1
        source=next(s for s in client.get('/api/v1/sources').json()['items'] if s['id']=='brapi_prices')
        assert source['count']==63 and source['status']=='partial' and 'VALE3' in source['capability']
        assert client.get('/api/v1/operations').json()['collection']['latest_price_data_at']==iso('2026-11-01T02:17:43.178290Z')
    for table,rows in before.items():assert store.rows('SELECT * FROM '+table)==rows


def test_runner_v1_2_uses_material_new_schema_prompt_and_cache_without_altering_legacy(setup_runner,store,monkeypatch):
    runner,legacy,_,*_=setup_runner
    old=runner.run(legacy)  # Existing injected fake executor only.
    old_run=runner.store.rows('SELECT prompt_hash,schema_hash FROM analysis_runs')[0]
    bundle=built(store,monkeypatch)
    runner.store=store
    # The entire fixture is a disposable DB; this changes only the runner's
    # in-memory profile gate, never an operational database/profile/config.
    monkeypatch.setattr(store,'profile','real')
    calls=[]
    def executor(args,**kwargs):
        envelope=json.loads(kwargs['stdin'].decode().rsplit('\n\n',1)[1])
        schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text(encoding='utf-8'))
        assert schema['properties']['schema_version']['const']=='1.2'
        assert 'uniqueItems' not in dumps(schema)
        assert 'model_reasoning_effort="ultra"' in args
        assert 'windows.sandbox="mxc"' in args
        assert envelope['schema_version']=='1.2'
        value=analysis_for(envelope['evidence_bundle']);value['analysis_id']=envelope['analysis_id']
        calls.append(envelope)
        return encode_events(value)
    runner.executor=executor
    first=runner.run(bundle,repair_limit=0);second=runner.run(bundle,repair_limit=0)
    assert first['calls']==1 and second['calls']==0 and second['cache_reused']
    assert len(calls)==1 and len(store.rows('SELECT * FROM quota_ledger'))==1
    new_run=store.rows('SELECT prompt_hash,schema_hash FROM analysis_runs')[0]
    assert new_run['prompt_hash']!=old_run['prompt_hash'] and new_run['schema_hash']!=old_run['schema_hash']
    assert old['analysis']['schema_version']=='1.1'
