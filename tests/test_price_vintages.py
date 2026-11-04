"""Immutable captures and descriptive A metrics; archived fixture, no HTTP/AI."""
import copy
import json
from pathlib import Path

import pytest

from backend import market_calendar as cal
from backend import price_vintages as prices
from backend.core import Store,dumps
from backend.ingest import upsert_catalog
from backend.timeutil import iso

ROOT=Path(__file__).resolve().parents[1]
PRIOR=ROOT/'docs/qa-20261031/vale3-gates'
ASSET={'asset_id':'brapi:VALE3','ticker':'VALE3','currency':'BRL'}
FETCHED='2026-11-01T02:17:43.178290Z'
NOW='2026-11-01T03:20:00.000000Z'


def body(): return (PRIOR/'brapi-public-response.json').read_bytes()


@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(cal,'iso',lambda value=None:iso(value or '2026-11-01T02:52:58Z'))
    monkeypatch.setattr(prices,'iso',lambda value=None:iso(value or NOW))
    store=Store(tmp_path/'temporary.sqlite3','demo')
    upsert_catalog(store,[{'symbol':'VALE3','name':'Synthetic Vale','assetType':'stock'}],fetched_at='2026-07-01T00:00:00Z')
    with store.connect() as con:
        cal.import_calendar_package(con,json.loads((PRIOR/'calendar-package-v1.json').read_text(encoding='utf-8')),ROOT/'docs/qa-20261031/data-readiness/calendar/sources')
    return store


def capture(store,data=None,fetched=FETCHED):
    return prices.persist_vintage(store,ASSET['asset_id'],body() if data is None else data,fetched_at=fetched)


def record(store): return store.rows('SELECT * FROM price_vintages')[0]


def test_full_public_capture_and_exact_retry_are_immutable(store):
    old_prices=store.rows('SELECT * FROM price_revisions')
    first=capture(store);same=capture(store)
    assert first['inserted'] and not same['inserted'] and first['available_at']==same['available_at']==NOW
    row=record(store)
    assert row['response_body']==body() and row['fetched_at']==iso(FETCHED)
    assert row['source_url']=='https://brapi.dev/api/quote/VALE3?range=3mo&interval=1d'
    assert not any(key in row for key in ('headers','cookies','authorization','token'))
    normalized=prices.validate_vintage(row,ASSET,NOW)
    assert len(normalized['bars'])==63
    assert all(bar['market_timestamp'] is None and bar['timestamp_precision']=='date' for bar in normalized['bars'])
    assert all(bar['complete_session'] for bar in normalized['bars'])
    assert normalized['bars'][0]['close']!=normalized['bars'][0]['adjustedClose']
    assert store.rows('SELECT * FROM price_revisions')==old_prices
    assert len(store.rows("SELECT * FROM audit_events WHERE event_type='price_vintage_captured'"))==1
    for sql in ('DELETE FROM price_vintages','UPDATE price_vintages SET material_sha256=material_sha256'):
        with store.connect() as con,pytest.raises(Exception,match='immutable_record'):con.execute(sql)


def test_repeat_acquisition_is_retained_without_changing_material_representative(store,monkeypatch):
    first=capture(store)
    second_body=json.loads(body());second_body['requestedAt']='2026-11-01T04:00:00Z'
    monkeypatch.setattr(prices,'iso',lambda value=None:iso(value or '2026-11-01T04:00:01Z'))
    second=capture(store,dumps(second_body).encode(),fetched='2026-11-01T04:00:00Z')
    assert second['vintage_id']!=first['vintage_id']
    assert len(store.rows('SELECT * FROM price_vintages'))==2
    selected=prices.selected_vintage(store,ASSET,'2026-11-01T04:01:00Z')
    assert selected['vintage_id']==first['vintage_id']
    assert selected['latest_capture_at']=='2026-11-01T04:00:00.000000Z'


def test_A_B_A_never_borrows_early_A_availability(store,monkeypatch):
    a1=capture(store)
    changed=json.loads(body());changed['results'][0]['historicalDataPrice'][-1]['adjustedClose']=61
    monkeypatch.setattr(prices,'iso',lambda value=None:iso(value or '2026-11-01T04:00:01Z'))
    b=capture(store,dumps(changed).encode(),fetched='2026-11-01T04:00:00Z')
    monkeypatch.setattr(prices,'iso',lambda value=None:iso(value or '2026-11-01T05:00:01Z'))
    a2=capture(store,fetched='2026-11-01T05:00:00Z')
    assert a1['vintage_id']!=a2['vintage_id']
    assert prices.selected_vintage(store,ASSET,'2026-11-01T03:30:00Z')['vintage_id']==a1['vintage_id']
    assert prices.selected_vintage(store,ASSET,'2026-11-01T04:30:00Z')['vintage_id']==b['vintage_id']
    final=prices.selected_vintage(store,ASSET,'2026-11-01T05:30:00Z')
    assert final['vintage_id']==a2['vintage_id'] and final['available_at']==iso('2026-11-01T05:00:01Z')
    assert len(store.rows('SELECT * FROM price_vintages'))==3


def test_before_availability_never_uses_snapshot(store):
    capture(store)
    assert prices.selected_vintage(store,ASSET,'2026-11-01T03:19:59Z') is None
    with pytest.raises(ValueError,match='unavailable'):
        prices.validate_vintage(record(store),ASSET,'2026-11-01T03:19:59Z')


@pytest.mark.parametrize('change', ['symbol','historical_symbol','interval','currency','duplicate_day','closed_day','future_day','timestamp_hour','bool_value','nan_json','duplicate_key'])
def test_invalid_capture_has_no_partial_persistence(store,change):
    data=json.loads(body());quote=data['results'][0]
    if change=='symbol':quote['symbol']='OTHER3'
    elif change=='historical_symbol':quote['historicalSymbol']='OTHER3'
    elif change=='interval':quote['usedInterval']='1wk'
    elif change=='currency':quote['currency']='USD'
    elif change=='duplicate_day':quote['historicalDataPrice'].append(quote['historicalDataPrice'][-1])
    elif change=='closed_day':quote['historicalDataPrice'][-1]['date']=1793415600  # Oct 31, 03:00 UTC.
    elif change=='future_day':quote['historicalDataPrice'][-1]['date']=1793674800  # Nov 3, 03:00 UTC.
    elif change=='timestamp_hour':quote['historicalDataPrice'][0]['date']+=3600
    elif change=='bool_value':quote['historicalDataPrice'][0]['adjustedClose']=True
    encoded=dumps(data).encode()
    if change=='nan_json':encoded=b'{"results":NaN}'
    elif change=='duplicate_key':encoded=b'{"results":[],"results":[]}'
    with pytest.raises(ValueError):capture(store,encoded)
    assert not store.rows("SELECT name FROM sqlite_master WHERE name='price_vintages'")


def test_interrupt_rolls_back_capture_and_audit_and_table(store,monkeypatch):
    before=store.rows('SELECT * FROM audit_events');checks=0
    def fence(con):
        nonlocal checks
        checks+=1
        if checks==2:raise KeyboardInterrupt()
    monkeypatch.setattr(store,'_check_worker_fence',fence)
    with pytest.raises(KeyboardInterrupt):capture(store)
    assert not store.rows("SELECT name FROM sqlite_master WHERE name='price_vintages'")
    assert store.rows('SELECT * FROM audit_events')==before


def test_adjusted_changes_use_A_and_do_not_require_R63_or_volume(store):
    data=json.loads(body())
    for bar in data['results'][0]['historicalDataPrice']:bar['volume']=None
    capture(store,dumps(data).encode())
    selected=prices.selected_vintage(store,ASSET,NOW)
    metrics=prices.adjusted_metrics(selected['normalized'],json.loads(selected['calendar_json']),NOW,'series-evidence')
    values={metric['metric_id']:metric for metric in metrics}
    expected={1:.03280599500416326,5:.0019386106623586752,21:-.11285939064511508}
    for n,value in expected.items():
        assert values[f'provider_adjusted_change_{n}']['value']==pytest.approx(value,abs=1e-12)
    assert values['provider_adjusted_change_63']['missing_reason']=='insufficient_observations'
    assert values['provider_adjusted_daily_volatility_20']['value']==pytest.approx(.02019151524868287)
    assert values['provider_adjusted_annualized_volatility_20']['value']==pytest.approx(.32053036764950027)
    assert all(metric['adjustment']==prices.PRICE_BASIS for metric in metrics)
    assert not any(metric['metric_id'].startswith('price_return') for metric in metrics)


@pytest.mark.parametrize('missing_index',[-1,-30])
def test_missing_adjusted_close_only_blocks_windows_that_need_it(store,missing_index):
    data=json.loads(body());data['results'][0]['historicalDataPrice'][missing_index]['adjustedClose']=None
    capture(store,dumps(data).encode());selected=prices.selected_vintage(store,ASSET,NOW)
    metrics=prices.adjusted_metrics(selected['normalized'],json.loads(selected['calendar_json']),NOW,'series')
    if missing_index==-1:assert all(metric['value'] is None for metric in metrics)
    else:assert all(metric['value'] is not None for metric in metrics if metric['metric_id']!='provider_adjusted_change_63')


def test_fetch_before_close_remains_incomplete_after_wall_clock_advances(store):
    capture(store,fetched='2026-10-30T21:59:59Z')
    selected=prices.selected_vintage(store,ASSET,NOW)
    assert not selected['normalized']['bars'][-1]['complete_session']
    metrics=prices.adjusted_metrics(selected['normalized'],json.loads(selected['calendar_json']),NOW,'series')
    assert metrics[0]['missing_reason']=='session_completeness_unverified'


@pytest.mark.parametrize('field,value',[('response_sha256','0'*64),('policy_sha256','0'*64),('source_url','https://brapi.dev/api/quote/VALE3?token=secret'),('material_sha256','0'*64),('vintage_id','invented')])
def test_revalidation_cannot_trust_declared_proof(store,field,value):
    capture(store);row=record(store);row[field]=value
    with pytest.raises(ValueError):prices.validate_vintage(row,ASSET,NOW)


def test_exact_acquisition_retry_uses_its_original_calendar_not_current_package(store,monkeypatch):
    first=capture(store);before=record(store)
    def forbid_current(*args):raise AssertionError('Exact retry must not reload/reinterpret current calendar')
    monkeypatch.setattr(prices,'calendar_context',forbid_current)
    second=capture(store)
    assert not second['inserted'] and second['available_at']==first['available_at']
    assert record(store)==before


def test_selection_reads_only_current_and_representative_bodies(store,monkeypatch):
    capture(store)
    for minute in range(1,5):
        stamp=f'2026-11-01T04:0{minute}:00Z'
        monkeypatch.setattr(prices,'iso',lambda value=None,stamp=stamp:iso(value or stamp))
        capture(store,fetched=stamp)
    original=store.rows;body_reads=[]
    def measured(sql,args=()):
        rows=original(sql,args)
        body_reads.extend(row['vintage_id'] for row in rows if 'response_body' in row)
        return rows
    monkeypatch.setattr(store,'rows',measured)
    prices.selected_vintage(store,ASSET,'2026-11-01T05:00:00Z')
    assert len(body_reads)==2
