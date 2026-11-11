"""Operational source clocks are read-only projections, never collectors."""
import json
import pytest
from backend.operations import collection_state
from backend.timeutil import iso
from test_price_read_boundaries import handlers, no_network_or_processes
from test_price_vintages import store, capture

NOW = '2026-11-01T04:01:00Z'

def state(store, provider='brapi_prices', **values):
    row = dict(provider_id=provider, status='available', last_attempt_at=iso('2026-11-01T04:00:00Z'),
               last_success_at=iso('2026-11-01T04:00:00Z'), error_code=None,
               next_attempt_at=None, details_json='{}')
    row.update(values)
    with store.transaction() as con:
        con.execute('INSERT INTO provider_state VALUES(?,?,?,?,?,?,?)', tuple(row.values()))

def source(store, monkeypatch, provider='brapi_prices'):
    return next(x for x in handlers(store, monkeypatch, NOW)['/api/v1/sources']()['items'] if x['id']==provider)

@pytest.mark.parametrize('clock', ['2026-11-01T04:01:00.000001Z','invalid','2026-11-01T04:00:00',''])
@pytest.mark.parametrize('with_capture', [False,True])
def test_invalid_attempt_never_exposes_unconfirmed_diagnostics(store, monkeypatch, clock, with_capture):
    if with_capture: capture(store)
    state(store,last_attempt_at=clock,last_success_at=iso('2026-11-01T03:30:00Z'),
          error_code='unconfirmed_error',status='unavailable',details_json='{"unconfirmed":true}',next_attempt_at='2026-11-02T05:00:00Z')
    before=store.rows('SELECT * FROM provider_state')
    item=source(store,monkeypatch)
    assert item['status']=='unavailable'
    assert item['last_attempt_at'] is None
    assert item['last_success_at']==iso('2026-11-01T03:30:00Z')
    assert item['error']=='provider_state_clock_invalid'
    assert 'unconfirmed' not in item['details']
    assert item['next_attempt_at'] is None
    assert store.rows('SELECT * FROM provider_state')==before

@pytest.mark.parametrize('clock', ['2026-11-01T04:01:00.000001Z','invalid','2026-11-01T04:00:00',''])
def test_invalid_success_does_not_hide_known_failure(store,monkeypatch,clock):
    capture(store)
    state(store,last_success_at=clock,status='unavailable',error_code='rate_limited',details_json='{"known_failure":true}')
    item=source(store,monkeypatch)
    assert item['status']=='unavailable'
    assert item['last_success_at']==iso('2026-11-01T02:17:43.178290Z')
    assert item['error']=='rate_limited'
    assert item['reason']=='provider_state_clock_invalid'
    assert item['details']['known_failure'] is True

@pytest.mark.parametrize('at', ['2026-11-01T04:01:00Z','2026-11-01T01:01:00-03:00'])
def test_cutoff_inclusive_and_scheduled_future_is_valid(store,monkeypatch,at):
    state(store,provider='bcb_sgs',last_attempt_at=at,last_success_at=at,next_attempt_at='2026-11-02T01:00:00-03:00')
    item=source(store,monkeypatch,'bcb_sgs')
    assert item['status']=='available' and item['error'] is None
    assert item['last_success_at']==iso(NOW)
    assert item['next_attempt_at']==iso('2026-11-02T04:00:00Z')

def test_offset_success_compares_instants_not_text(store,monkeypatch):
    capture(store)
    state(store,last_attempt_at='2026-11-01T00:30:00-03:00',last_success_at='2026-11-01T00:30:00-03:00')
    assert source(store,monkeypatch)['last_success_at']==iso('2026-11-01T03:30:00Z')

def test_sources_freeze_cutoff_once(store,monkeypatch):
    routes=handlers(store,monkeypatch,NOW)
    calls=[]
    def advancing(value=None):
        if value is not None: return iso(value)
        calls.append(1)
        return iso(NOW if len(calls)==1 else '2026-11-02T04:01:00Z')
    monkeypatch.setattr('backend.api.iso',advancing)
    response=routes['/api/v1/sources']()
    assert response['as_of']==iso(NOW)
    assert len(calls)==1

def test_operations_and_sources_filter_same_provider_clocks(store,monkeypatch):
    state(store,last_attempt_at='2026-11-02T04:00:00Z',last_success_at='2026-11-02T04:00:00Z',details_json='{"future":true}')
    item=source(store,monkeypatch)
    before=store.rows('SELECT * FROM provider_state')
    operations=collection_state(store,[],now=NOW)
    projected=next(x for x in operations['sources'] if x['provider_id']=='brapi_prices')
    assert operations['latest_price_fetch'] is None
    assert projected['last_attempt_at']==item['last_attempt_at']==None
    assert projected['last_success_at']==item['last_success_at']==None
    assert projected['error_code']==item['error']=='provider_state_clock_invalid'
    assert store.rows('SELECT * FROM provider_state')==before

def test_operations_future_data_clock_not_reported_as_collected(store,monkeypatch):
    capture(store)
    original=store.rows
    def rows(sql,args=()):
        result=original(sql,args)
        if sql=='SELECT fetched_at,available_at FROM price_vintages':
            return [dict(row,fetched_at='2026-11-02T04:00:00Z') for row in result]
        return result
    monkeypatch.setattr(store,'rows',rows)
    assert collection_state(store,[],now=NOW)['latest_price_data_at'] is None

def test_invalid_capture_is_explicit_not_a_sources_500(store,monkeypatch):
    capture(store)
    from backend.core import Store
    original=Store.rows
    def rows(self,sql,args=()):
        result=original(self,sql,args)
        if 'SELECT v.*,a.ticker,a.currency FROM price_vintages' in sql:
            return [dict(row,response_sha256='invalid') for row in result]
        return result
    monkeypatch.setattr(Store,'rows',rows)
    item=source(store,monkeypatch)
    assert item['status']=='unavailable'
    assert item['reason']==item['error']=='price_capture_invalid'
    assert item['count'] is None and item['last_success_at'] is None


def test_calendar_clock_error_is_separate_from_valid_documentary_schedule(store,monkeypatch):
    state(store,provider='market_calendar',last_attempt_at='2026-11-02T04:00:00Z',last_success_at='2026-11-02T04:00:00Z')
    before=store.rows('SELECT * FROM provider_state')
    item=source(store,monkeypatch,'market_calendar')
    operation=next(x for x in collection_state(store,[],now=NOW)['sources'] if x['provider_id']=='market_calendar')
    assert item['status']=='partial'
    assert item['error']==operation['error_code']=='provider_state_clock_invalid'
    assert item['reason']==operation['reason']=='provider_state_clock_invalid'
    assert item['last_attempt_at']==operation['last_attempt_at']==None
    assert item['last_success_at']==operation['last_success_at']==item['details']['available_at']
    assert item['details']['automatic_prices']==operation['details']['automatic_prices']
    assert item['next_attempt_at']==operation['next_attempt_at']
    assert store.rows('SELECT * FROM provider_state')==before


def test_unknown_attempt_and_invalid_success_preserve_failure(store,monkeypatch):
    capture(store)
    state(store,last_attempt_at=None,last_success_at='2026-11-02T04:00:00Z',status='unavailable',error_code='known_failure')
    item=source(store,monkeypatch)
    assert item['status']=='unavailable' and item['error']=='known_failure'
    assert item['reason']=='provider_state_clock_invalid'
    assert item['last_success_at']==iso('2026-11-01T02:17:43.178290Z')


def test_invalid_success_without_error_never_certifies_provider(store,monkeypatch):
    capture(store)
    state(store,last_success_at='2026-11-02T04:00:00Z')
    item=source(store,monkeypatch)
    assert item['status']=='unavailable'
    assert item['error']==item['reason']=='provider_state_clock_invalid'
    assert item['last_success_at']==iso('2026-11-01T02:17:43.178290Z')
    assert item['count']==63


def test_future_availability_excludes_previously_fetched_data(store,monkeypatch):
    capture(store)
    original=store.rows
    def rows(sql,args=()):
        result=original(sql,args)
        if sql=='SELECT fetched_at,available_at FROM price_vintages':
            return [dict(row,available_at='2026-11-02T04:00:00Z') for row in result]
        return result
    monkeypatch.setattr(store,'rows',rows)
    projection=collection_state(store,[],now=NOW)
    assert projection['latest_price_data_at'] is None
    assert projection['data_clock_errors']=={'price_vintages':'data_clock_invalid'}


@pytest.mark.parametrize('field,value',[('last_success_at','2026-11-02T04:00:00Z'),('next_attempt_at','invalid')])
def test_calendar_known_failure_survives_other_invalid_clock(store,monkeypatch,field,value):
    state(store,provider='market_calendar',status='unavailable',error_code='known_failure',**{field:value})
    item=source(store,monkeypatch,'market_calendar')
    operation=next(x for x in collection_state(store,[],now=NOW)['sources'] if x['provider_id']=='market_calendar')
    assert item['status']=='partial'
    assert item['error']==operation['error_code']=='known_failure'
    assert item['reason']==operation['reason']=='provider_state_clock_invalid'
    assert item['last_success_at']==operation['last_success_at']==item['details']['available_at']
    assert item['next_attempt_at']==operation['next_attempt_at']
