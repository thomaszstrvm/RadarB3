"""Finite VALE3 gates: temporary stores, archived PDFs, no network or AI."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import market_calendar as calendar
from backend.adapters import BrapiAdapter
from backend.core import Store, digest, dumps
from backend.evidence import EvidenceError, build_bundle, metrics_for_bars, validate_bundle
from backend.ingest import ingest_price, upsert_catalog
from backend.session_continuity import calendar_context, calendar_from_rows
from backend.timeutil import iso, instant

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'docs/qa-20261031/vale3-gates'
SOURCES = ROOT / 'docs/qa-20261031/data-readiness/calendar/sources'
SCHEMAS = ROOT / 'schemas'
ASSET = 'brapi:VALE3'
IMPORTED = '2026-10-31T06:00:00.000000Z'
CUTOFF = '2026-11-03T21:59:59.000000Z'


def document():
    return json.loads((QA/'calendar-package-v1.json').read_text(encoding='utf-8'))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar,'iso',lambda value=None: iso(value or IMPORTED))
    result = Store(tmp_path/'isolated.sqlite3','demo')
    upsert_catalog(result,[{'symbol':'VALE3','name':'Synthetic Vale fixture','assetType':'stock'}],fetched_at='2026-07-01T12:00:00Z')
    return result


def import_one(store, **kwargs):
    with store.connect() as con:
        return calendar.import_calendar_package(con,document(),SOURCES,**kwargs)


def synthetic_bars(count=63, adjustment='split_adjusted'):
    days = [row for row in calendar.package_rows(document()) if row['is_open'] and '2026-08-03'<=row['session_date']<='2026-10-30'][-count:]
    return [{'session_date':row['session_date'],'close':100+i,'volume':1000+i,'provider':'fixture',
             'currency':'BRL','adjustment':adjustment,'complete_session':True,'revision_id':f'p{i}',
             'market_timestamp':row['closes_at'],'quality_flags':[]} for i,row in enumerate(days)]


def seed_prices(store,count=63,adjustment='split_adjusted'):
    for bar in synthetic_bars(count,adjustment):
        ingest_price(store,ASSET,{**bar,'fetched_at':'2026-10-31T05:00:00Z'},provider='fixture')


def test_package_import_is_atomic_idempotent_and_immutable(store):
    before = store.rows('SELECT * FROM audit_events')
    first = import_one(store)
    assert first['inserted']==96 and first['available_at']==IMPORTED
    assert import_one(store)=={**first,'inserted':0,'unchanged':96}
    assert len(store.rows('SELECT * FROM calendar_packages'))==1
    assert len(store.rows('SELECT * FROM audit_events'))==len(before)+1
    rows=store.rows('SELECT * FROM market_sessions')
    assert len(rows)==96 and sum(row['is_open'] for row in rows)==65
    for table in ('calendar_packages','market_sessions'):
        with store.connect() as con, pytest.raises(Exception,match='immutable_record'):
            con.execute(f'DELETE FROM {table}')


@pytest.mark.parametrize('profile,expected', [('demo','real'),('real','demo')])
def test_explicit_profile_mismatch_has_no_partial_table(store,profile,expected):
    with store.transaction() as con:
        con.execute("UPDATE settings SET value_json=? WHERE key='profile'",(dumps(profile),))
    with pytest.raises(ValueError,match='calendar_profile_mismatch'):
        import_one(store,expected_profile=expected)
    assert not store.rows("SELECT name FROM sqlite_master WHERE name='calendar_packages'")


def test_explicit_real_profile_supported_only_in_temporary_store(store):
    with store.transaction() as con:
        con.execute("UPDATE settings SET value_json='\"real\"' WHERE key='profile'")
    assert import_one(store,expected_profile='real')['inserted']==96


def test_existing_identical_rows_are_counted_as_reused_not_inserted(store):
    with store.transaction() as con:
        for row in calendar.package_rows(document()):
            con.execute('INSERT INTO market_sessions VALUES(?,?,?,?,?,?)',tuple(row.values()))
    result=import_one(store)
    assert result['inserted']==0 and result['unchanged']==96
    assert len(store.rows('SELECT * FROM calendar_packages'))==1
    assert len(store.rows("SELECT * FROM audit_events WHERE event_type='calendar_package_imported'"))==1


def test_conflict_rolls_back_package_rows_and_ddl(store):
    row=calendar.package_rows(document())[50]
    with store.transaction() as con:
        con.execute('INSERT INTO market_sessions VALUES(?,?,?,?,?,?)',
                    (row['session_date'],row['is_open'],row['closes_at'],'https://other.invalid','other',row['verified_at']))
    with pytest.raises(ValueError,match='calendar_conflicting_session'):
        import_one(store)
    assert len(store.rows('SELECT * FROM market_sessions'))==1
    assert not store.rows("SELECT name FROM sqlite_master WHERE name LIKE 'calendar_package%'")
    assert not store.rows("SELECT * FROM audit_events WHERE event_type='calendar_package_imported'")


def test_keyboard_interrupt_rolls_back_everything(store):
    class InterruptingConnection:
        def __init__(self, con): self.con=con; self.insertions=0
        def __getattr__(self,key): return getattr(self.con,key)
        def execute(self,sql,args=()):
            if sql.startswith('INSERT INTO market_sessions'):
                self.insertions+=1
                if self.insertions==20: raise KeyboardInterrupt()
            return self.con.execute(sql,args)
    with store.connect() as con, pytest.raises(KeyboardInterrupt):
        calendar.import_calendar_package(InterruptingConnection(con),document(),SOURCES)
    assert not store.rows('SELECT * FROM market_sessions')
    assert not store.rows("SELECT name FROM sqlite_master WHERE name LIKE 'calendar_package%'")


def test_active_job_refuses_import_without_mutation(store):
    store.enqueue('analyse',{'asset_id':ASSET})
    with pytest.raises(ValueError,match='calendar_job_active'): import_one(store)
    assert not store.rows('SELECT * FROM market_sessions')


def test_even_expired_worker_lease_requires_operational_reconciliation(store):
    store.acquire_worker('fixture',now=instant('2026-10-01T12:00:00Z'))
    with pytest.raises(ValueError,match='calendar_worker_present'): import_one(store)
    assert not store.rows('SELECT * FROM market_sessions')


def test_calendar_status_is_derived_without_rewriting_provider_history(store,monkeypatch):
    from backend.session_continuity import calendar_status
    from backend.operations import collection_state
    monkeypatch.setattr('backend.core.iso',lambda value=None: iso(value or CUTOFF))
    store.provider_status('market_calendar','unavailable','verified_session_calendar_required')
    baseline=store.rows('SELECT * FROM provider_state')
    import_one(store)
    unavailable=calendar_status(store,'2026-10-31T05:30:00Z')
    assert unavailable['status']=='unavailable'
    assert unavailable['error_code']=='calendar_date_unverified'
    assert unavailable['available_at'] is None
    status=calendar_status(store,CUTOFF)
    assert status['status']=='partial' and status['available_end']=='2026-11-03'
    assert status['details']['provider_finality_certified'] is False
    assert calendar_status(store,'2026-11-04T12:00:00Z')['status']=='unavailable'
    monkeypatch.setattr('backend.operations.iso',lambda: CUTOFF)
    source=next(row for row in collection_state(store,[])['sources'] if row['provider_id']=='market_calendar')
    assert source['error_code'] is None
    assert source['reason']==status['reason']
    assert source['next_attempt_at']==status['next_attempt_at']
    assert source['last_success_at']==IMPORTED
    assert store.rows('SELECT * FROM provider_state')==baseline


def test_api_calendar_status_and_coverage_are_truthful_without_schedule(store,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.api import create_app
    monkeypatch.setattr('backend.api.ai_status',lambda:{'enabled':False,'reason':'isolated'})
    monkeypatch.setattr('backend.api.iso',lambda value=None: iso(value or CUTOFF))
    monkeypatch.setattr('backend.core.iso',lambda value=None: iso(value or CUTOFF))
    store.provider_status('market_calendar','unavailable','verified_session_calendar_required')
    baseline=store.rows('SELECT * FROM provider_state')
    import_one(store)
    app=create_app(store.path,profile='demo',execution_mode='manual-analysis')
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        source=next(row for row in client.get('/api/v1/sources').json()['items'] if row['id']=='market_calendar')
        assert source['status']=='partial' and source['available_end']=='2026-11-03'
        assert source['count']==96 and source['details']['scheduled_open_days']==65
        assert source['capability'].startswith('Dias documentais')
        assert source['details']['provider_finality_certified'] is False
        gaps=client.get('/api/v1/coverage').json()['known_gaps']
        assert any('limitado a 2026-07-31' in gap for gap in gaps)
        assert not any('calendário de pregões ainda precisam' in gap for gap in gaps)
    assert store.rows('SELECT * FROM provider_state')==baseline


def test_pinned_document_and_sources_cannot_self_attest(store,tmp_path):
    doc=document();doc['sessions'][-2][1]=True
    with pytest.raises(ValueError,match='calendar_package_not_reviewed'):
        calendar.package_rows(doc)
    for source in document()['sources']:
        (tmp_path/source['filename']).write_bytes(b'%PDF-wrong')
    with store.connect() as con, pytest.raises(ValueError,match='calendar_source_hash_mismatch'):
        calendar.import_calendar_package(con,document(),tmp_path)
    assert not store.rows('SELECT * FROM market_sessions')


def test_available_at_blocks_backdated_calendar_and_preserves_old_snapshot(store):
    seed_prices(store,22)
    before='2026-10-31T05:30:00Z'
    old=build_bundle(store,ASSET,before,SCHEMAS)
    old_text=dumps(old)
    import_one(store)
    assert calendar_context(store,synthetic_bars(),before)[1] is None
    assert dumps(build_bundle(store,ASSET,before,SCHEMAS))==old_text
    new=build_bundle(store,ASSET,'2026-10-31T06:30:00Z',SCHEMAS)
    assert new['schema_version']=='1.1' and new['calendar_package']['available_at']==IMPORTED
    assert 'limitado a 2026-07-31' in new['coverage']['known_gaps'][1]
    assert store.rows('SELECT payload_json FROM evidence_bundles WHERE bundle_id=?',(old['bundle_id'],))[0]['payload_json']==old_text
    for item in new['evidence']:
        if item['record_type']=='price':
            data=json.loads(item['content'])
            assert 'calendar' not in data and data['calendar_ref']==calendar.PACKAGE_ID
            assert item['available_at']>=IMPORTED


def test_closed_tail_and_market_close_determine_expected_last():
    rows=calendar.package_rows(document())
    prior=calendar_from_rows(rows,synthetic_bars(2),CUTOFF)
    assert prior[-1]['calendar']['expected_last_session']=='2026-10-30'
    exact=calendar_from_rows(rows,synthetic_bars(2),'2026-11-03T22:00:00Z')
    assert exact[-1]['calendar']['expected_last_session']=='2026-10-30'
    after=calendar_from_rows(rows,synthetic_bars(2),'2026-11-03T22:00:00.000001Z')
    assert after[-1]['calendar']['expected_last_session']=='2026-11-03'
    assert all(bar['calendar'] is None for bar in calendar_from_rows(rows,synthetic_bars(2),'2026-11-04T12:00:00Z'))
    doc=document();doc['sessions'][-2][1]=True  # Nov 2 closure is integral provenance.
    with pytest.raises(ValueError,match='calendar_package_not_reviewed'): calendar.package_rows(doc)


def test_63_observations_keep_short_metrics_independently_valid():
    bars=calendar_from_rows(calendar.package_rows(document()),synthetic_bars(),CUTOFF)
    metrics={m['metric_id']:m for m in metrics_for_bars(bars)}
    assert metrics['price_return_63']['value'] is None
    assert metrics['price_return_63']['missing_reason']=='insufficient_observations'
    for key in ('price_return_1','price_return_5','price_return_21','realized_volatility_20','relative_volume_20'):
        assert metrics[key]['value'] is not None and metrics[key]['missing_reason'] is None


def test_total_return_never_gets_price_return_label_value():
    bars=calendar_from_rows(calendar.package_rows(document()),synthetic_bars(adjustment='total_return'),CUTOFF)
    metrics=metrics_for_bars(bars)
    assert all(metric['value'] is None for metric in metrics)
    assert metrics[0]['missing_reason']=='metric_requires_split_adjusted_series'


def test_legacy_total_return_mislabel_is_intentionally_rejected_without_rewrite(store):
    import importlib.util
    # Reproduce the prior acceptance from the captured implementation, not a mock.
    spec=importlib.util.spec_from_file_location('backend._previous_evidence',QA/'source-before/backend/evidence.py')
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    rows=calendar.package_rows(document())
    with store.transaction() as con:
        for row in rows:
            row={**row,'version':'synthetic-legacy-v1'}
            con.execute('INSERT INTO market_sessions VALUES(?,?,?,?,?,?)',tuple(row.values()))
    seed_prices(store,22,adjustment='total_return')
    bundle=old.build_bundle(store,ASSET,CUTOFF,SCHEMAS)
    assert bundle['schema_version']=='1.0'
    assert any(metric['value'] is not None for metric in bundle['computed_metrics'])
    assert old.validate_bundle(bundle,SCHEMAS)
    baseline=store.rows('SELECT * FROM evidence_bundles')
    with pytest.raises(EvidenceError,match='metric_recalculation_mismatch'):
        validate_bundle(bundle,SCHEMAS)
    assert store.rows('SELECT * FROM evidence_bundles')==baseline


def test_lower_window_inclusive_before_family_and_limit(store):
    for day in ('2026-07-30','2026-07-31','2026-08-03'):
        ingest_price(store,ASSET,{'session_date':day,'close':10,'fetched_at':'2026-10-31T05:00:00Z'})
    bundle=build_bundle(store,ASSET,'2026-10-31T06:30:00Z',SCHEMAS)
    assert [json.loads(e['content'])['session_date'] for e in bundle['evidence']]==['2026-07-31','2026-08-03']
    assert len(store.rows('SELECT * FROM price_bars'))==3


def test_compact_bundle_budget_keeps_all_valid_metric_inputs(store):
    import_one(store);seed_prices(store)
    bundle=build_bundle(store,ASSET,CUTOFF,SCHEMAS)
    assert len(dumps(bundle))<=60000 and bundle['schema_version']=='1.1'
    assert validate_bundle(bundle,SCHEMAS)
    ids={item['evidence_id'] for item in bundle['evidence']}
    nonnull=[metric for metric in bundle['computed_metrics'] if metric['value'] is not None]
    assert len(nonnull)==5
    assert all(set(metric['evidence_ids'])<=ids for metric in nonnull)
    with pytest.raises(EvidenceError,match='bundle_size_limit'):
        build_bundle(store,ASSET,CUTOFF,SCHEMAS,max_characters=10000)


@pytest.mark.parametrize('field,value',[('adjustment','total_return'),('start_at','2026-08-01T00:00:00.000000Z'),('end_at','2026-10-29T22:00:00.000000Z')])
def test_metric_metadata_must_equal_inputs(store,field,value):
    import_one(store);seed_prices(store,22)
    bundle=build_bundle(store,ASSET,CUTOFF,SCHEMAS)
    bundle['computed_metrics'][0][field]=value
    with pytest.raises(EvidenceError,match='metric_input_metadata_mismatch'):
        validate_bundle(bundle,SCHEMAS)


@pytest.mark.parametrize('change',['ref','available','tail'])
def test_calendar_reference_and_provenance_tampering_refused(store,change):
    import_one(store);seed_prices(store,2)
    bundle=build_bundle(store,ASSET,CUTOFF,SCHEMAS)
    price=next(e for e in bundle['evidence'] if e['record_type']=='price')
    if change=='tail': bundle['calendar_package']['document']['sessions'][-2][1]=True
    elif change=='available': price['available_at']=price['fetched_at']
    else:
        content=json.loads(price['content']);content['calendar_ref']='invented'
        price['content']=dumps(content);price['content_sha256']=digest(price['content'])
    with pytest.raises(EvidenceError,match='calendar_'):
        validate_bundle(bundle,SCHEMAS)


class FakeHTTP:
    def __init__(self,quote): self.quote=quote
    def get(self,*args,**kwargs):
        from backend.safe_http import Response
        return Response(200,dumps({'results':[self.quote]}).encode(),{})


def quote():
    return {'symbol':'VALE3','currency':'BRL','usedInterval':'1d','historicalDataPrice':[
        {'date':int(instant('2026-10-30T00:00:00Z').timestamp()),'close':60,'volume':100}]}


def test_calendar_never_promotes_unproven_adapter_snapshot(store):
    import_one(store)
    # The whole-vintage path now requires a matching daily response contract.
    # A calendar alone cannot bless this incomplete response (usedRange absent).
    with pytest.raises(ValueError,match='price_requested_range_mismatch'):
        BrapiAdapter(store,FakeHTTP(quote())).prices({'ticker':'VALE3','asset_id':ASSET})
    assert not store.rows('SELECT * FROM price_revisions')
    assert not store.rows("SELECT 1 FROM sqlite_master WHERE name='price_vintages'")


@pytest.mark.parametrize('field,value',[('symbol','OTHER3'),('symbol',None),('historicalSymbol','OTHER3'),('historicalSymbol',[]),('usedInterval','1wk')])
def test_adapter_identity_and_interval_mismatch_refused_without_ingestion(store,field,value):
    data=quote();data[field]=value
    with pytest.raises(Exception,match='mismatch'):
        BrapiAdapter(store,FakeHTTP(data)).prices({'ticker':'VALE3','asset_id':ASSET})
    assert not store.rows('SELECT * FROM price_revisions')
