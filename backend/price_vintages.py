"""Public daily captures: immutable observations, one vintage per calculation.

Provider adjustedClose includes distributions; it is neither split-only price
nor a verified investor total-return methodology. No network or AI lives here.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
import statistics

from .core import digest, dumps
from .market_calendar import validate_package_envelope
from .session_continuity import calendar_context, calendar_from_rows, continuity_reason
from .timeutil import instant, iso, news_window, SAO_PAULO, UTC

MAX_RESPONSE_BYTES = 2_000_000
PRICE_BASIS = 'provider_adjusted_close_with_proventos'
POLICY = {
    'policy_id':'brapi_v1_daily_ended_revisable_v1',
    'endpoint_family':'brapi_quote_v1','interval':'1d',
    'dictionary_url':'https://brapi.dev/api/v2/dictionary?category=historical',
    'dictionary_sha256':'5de4099f6f5d824c4462e0223ccd65d90977d4aab529f94816dda322bbcb3c1b',
    'dictionary_available_at':'2026-11-01T02:27:21.320149Z',
    'session_label_policy':'utc_midnight_civil_label_or_sao_paulo_midnight_v1',
    'period_ended_policy':'fetched_strictly_after_scheduled_correction_end',
    'subject_to_revision':True,
    'price_basis':PRICE_BASIS,
    'limitations':['not_split_only','not_verified_investor_total_return','not_trade_timestamp'],
}
POLICY_HASH = digest(POLICY)


def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError('price_duplicate_json_key')
        result[key]=value
    return result


def _invalid_constant(value):
    raise ValueError('price_nonfinite_json')


def _financial(value, field, *, positive=False):
    if value is None:
        return None, 'missing_'+field
    try: finite=type(value) in (int,float) and math.isfinite(value)
    except OverflowError: finite=False
    if not finite:
        raise ValueError('price_invalid_numeric_type')
    if value<0 or (positive and value==0):
        return None, 'nonpositive_'+field if positive else 'negative_'+field
    return value,None


def normalize_vintage(body, ticker, currency, fetched_at, calendar_envelope, available_at):
    """Reconstruct financial fields and completion from preserved public bytes."""
    if not isinstance(body,bytes) or not 0<len(body)<=MAX_RESPONSE_BYTES:
        raise ValueError('price_response_size_invalid')
    if instant(fetched_at)>instant(available_at) or instant(POLICY['dictionary_available_at'])>instant(available_at):
        raise ValueError('price_knowledge_clock_invalid')
    rows=validate_package_envelope(calendar_envelope,available_at)
    calendar={row['session_date']:row for row in rows}
    payload=json.loads(body.decode('utf-8'),object_pairs_hook=_object,parse_constant=_invalid_constant)
    results=payload.get('results') if isinstance(payload,dict) else None
    if not isinstance(results,list) or len(results)!=1 or not isinstance(results[0],dict):
        raise ValueError('price_response_shape_invalid')
    quote=results[0]
    if quote.get('symbol')!=ticker or quote.get('historicalSymbol') not in (None,ticker) or quote.get('currency')!=currency:
        raise ValueError('price_identity_mismatch')
    if quote.get('usedInterval')!='1d':
        raise ValueError('price_daily_interval_required')
    if quote.get('usedRange')!='3mo':
        raise ValueError('price_requested_range_mismatch')
    raw_bars=quote.get('historicalDataPrice')
    if not isinstance(raw_bars,list) or not 1<=len(raw_bars)<=1000:
        raise ValueError('price_history_shape_invalid')
    bars=[];seen=set()
    for original in raw_bars:
        if not isinstance(original,dict): raise ValueError('price_bar_shape_invalid')
        stamp=original.get('date')
        try: finite_stamp=type(stamp) in (int,float) and math.isfinite(stamp)
        except OverflowError: finite_stamp=False
        if not finite_stamp:
            raise ValueError('price_session_label_invalid')
        try: clock=datetime.fromtimestamp(stamp,UTC)
        except (ValueError,OverflowError,OSError): raise ValueError('price_session_label_invalid') from None
        local=clock.astimezone(SAO_PAULO)
        if clock.hour==clock.minute==clock.second==clock.microsecond==0:
            session=clock.date().isoformat()
        elif local.hour==local.minute==local.second==local.microsecond==0:
            session=local.date().isoformat()
        else:
            raise ValueError('price_session_label_unsupported')
        if session in seen: raise ValueError('price_duplicate_session')
        seen.add(session)
        if session>instant(fetched_at).astimezone(SAO_PAULO).date().isoformat():
            raise ValueError('price_future_session')
        scheduled=calendar.get(session)
        if scheduled is not None and not scheduled['is_open']:
            raise ValueError('price_closed_session')
        values={};missing={}
        for field in ('open','high','low','close','adjustedClose','volume'):
            values[field],reason=_financial(original.get(field),field,positive=field!='volume')
            if reason: missing[field]=reason
        complete=bool(scheduled and scheduled['is_open'] and instant(fetched_at)>instant(scheduled['closes_at']))
        bars.append({**values,'session_date':session,'source_session_timestamp':stamp,
                     'market_timestamp':None,'timestamp_precision':'date','currency':currency,
                     'complete_session':complete,'missing':missing})
    if bars!=sorted(bars,key=lambda bar:bar['session_date']):
        raise ValueError('price_session_order_invalid')
    return {'ticker':ticker,'currency':currency,'provider':'brapi','interval':'1d',
            'historical_symbol':quote.get('historicalSymbol'),'bars':bars}


def _material(normalized, calendar_envelope):
    return digest({'series':normalized,'policy_sha256':POLICY_HASH,
                   'calendar_id':calendar_envelope['package_id'],
                   'calendar_sha256':calendar_envelope['document_sha256']})


def persist_vintage(store, asset_id, body, *, fetched_at):
    """Atomically preserve a capture; exact reprocessing retains availability.

fetched_at comes from the trusted transport/archival receipt, never the remote
requestedAt/Date. This function constructs the only stored origin URL itself.
"""
    assets=store.rows('SELECT ticker,currency FROM assets WHERE asset_id=? AND eligible=1',(asset_id,))
    if len(assets)!=1: raise ValueError('price_asset_invalid')
    asset=assets[0];fetched_at=iso(fetched_at)
    if not re.fullmatch(r'[A-Z0-9]{4}\d{1,2}',asset['ticker']): raise ValueError('price_symbol_invalid')
    available=iso()
    source_url=f"https://brapi.dev/api/quote/{asset['ticker']}?range=3mo&interval=1d"
    if not isinstance(body,bytes) or not 0<len(body)<=MAX_RESPONSE_BYTES:raise ValueError('price_response_size_invalid')
    response_hash=hashlib.sha256(body).hexdigest()
    vintage_id='price_vintage_'+digest([asset_id,source_url,response_hash,fetched_at,POLICY_HASH])[:24]
    # An exact archival retry belongs to its original proof/availability even
    # after a newer calendar is installed. Never reinterpret that acquisition.
    if store.rows("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_vintages'"):
        existing=store.rows('SELECT * FROM price_vintages WHERE vintage_id=?',(vintage_id,))
        if existing:
            validate_vintage(existing[0],dict(asset,asset_id=asset_id),available)
            return {'vintage_id':vintage_id,'inserted':False,'available_at':existing[0]['available_at']}
    _,calendar_envelope=calendar_context(store,[],available)
    if calendar_envelope is None: raise ValueError('price_calendar_package_required')
    normalized=normalize_vintage(body,asset['ticker'],asset['currency'],fetched_at,calendar_envelope,available)
    material_hash=_material(normalized,calendar_envelope)
    with store.transaction() as con:
        available=iso()
        if instant(fetched_at)>instant(available): raise ValueError('price_knowledge_clock_invalid')
        con.execute('''CREATE TABLE IF NOT EXISTS price_vintages(
            vintage_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(asset_id),
            provider TEXT NOT NULL, source_url TEXT NOT NULL, response_sha256 TEXT NOT NULL,
            response_body BLOB NOT NULL, fetched_at TEXT NOT NULL, available_at TEXT NOT NULL,
            policy_json TEXT NOT NULL, policy_sha256 TEXT NOT NULL, calendar_json TEXT NOT NULL,
            material_sha256 TEXT NOT NULL)''')
        con.execute('CREATE INDEX IF NOT EXISTS price_vintages_cutoff ON price_vintages(asset_id,available_at)')
        for action in ('UPDATE','DELETE'):
            con.execute(f"CREATE TRIGGER IF NOT EXISTS price_vintages_no_{action.lower()} BEFORE {action} ON price_vintages BEGIN SELECT RAISE(ABORT,'immutable_record'); END")
        existing=con.execute('SELECT * FROM price_vintages WHERE vintage_id=?',(vintage_id,)).fetchone()
        if existing:
            validate_vintage(dict(existing),dict(asset,asset_id=asset_id),available)
            return {'vintage_id':vintage_id,'inserted':False,'available_at':existing['available_at']}
        con.execute('INSERT INTO price_vintages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (vintage_id,asset_id,'brapi',source_url,response_hash,body,fetched_at,available,
             dumps(POLICY),POLICY_HASH,dumps(calendar_envelope),material_hash))
        con.execute('INSERT INTO audit_events(event_type,created_at,payload_json) VALUES(?,?,?)',
            ('price_vintage_captured',available,dumps({'vintage_id':vintage_id,'response_sha256':response_hash,'policy_sha256':POLICY_HASH})))
    return {'vintage_id':vintage_id,'inserted':True,'available_at':available}


def validate_vintage(record, asset, as_of):
    if record['asset_id']!=asset['asset_id'] or record['provider']!='brapi': raise ValueError('price_identity_mismatch')
    expected_url=f"https://brapi.dev/api/quote/{asset['ticker']}?range=3mo&interval=1d"
    if record['source_url']!=expected_url: raise ValueError('price_origin_invalid')
    if instant(record['available_at'])>instant(as_of): raise ValueError('price_vintage_unavailable')
    if record['policy_sha256']!=POLICY_HASH or json.loads(record['policy_json'])!=POLICY:
        raise ValueError('price_policy_not_reviewed')
    body=record['response_body']
    if hashlib.sha256(body).hexdigest()!=record['response_sha256']: raise ValueError('price_response_hash_mismatch')
    expected_id='price_vintage_'+digest([record['asset_id'],expected_url,record['response_sha256'],record['fetched_at'],POLICY_HASH])[:24]
    if record['vintage_id']!=expected_id: raise ValueError('price_vintage_identity_invalid')
    calendar_envelope=json.loads(record['calendar_json'])
    normalized=normalize_vintage(body,asset['ticker'],asset['currency'],record['fetched_at'],calendar_envelope,record['available_at'])
    if _material(normalized,calendar_envelope)!=record['material_sha256']: raise ValueError('price_material_hash_mismatch')
    return normalized


def selected_vintage(store, asset, as_of):
    """Use one whole capture; identical acquisitions keep the first current run.

A -> B -> A starts a new run of A, and cannot borrow A's earlier availability.
"""
    if not store.rows("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_vintages'"):
        return None
    records=store.rows('SELECT rowid,vintage_id,material_sha256 FROM price_vintages WHERE asset_id=? AND available_at<=? ORDER BY available_at DESC,rowid DESC',(asset['asset_id'],iso(as_of)))
    if not records: return None
    current=store.rows('SELECT * FROM price_vintages WHERE vintage_id=?',(records[0]['vintage_id'],))[0]
    current_normalized=validate_vintage(current,asset,as_of)
    selected=records[0]
    for record in records[1:]:
        if record['material_sha256']!=selected['material_sha256']: break
        selected=record
    selected=current if selected['vintage_id']==current['vintage_id'] else store.rows('SELECT * FROM price_vintages WHERE vintage_id=?',(selected['vintage_id'],))[0]
    normalized=validate_vintage(selected,asset,as_of)
    if normalized!=current_normalized: raise ValueError('price_material_representative_mismatch')
    return {**selected,'normalized':normalized,'latest_capture_at':current['fetched_at']}


def adjusted_metrics(normalized, calendar_envelope, as_of, evidence_id):
    """Descriptive changes of provider A, never investor total return."""
    rows=validate_package_envelope(calendar_envelope,as_of)
    start,_=news_window(as_of)
    first=instant(start).astimezone(SAO_PAULO).date().isoformat()
    today=instant(as_of).astimezone(SAO_PAULO).date().isoformat()
    # An unfinished current period is not part of an ended daily series.
    scheduled={row['session_date']:row for row in rows}
    bars=[bar for bar in normalized['bars'] if first<=bar['session_date']<=today and
          (bar['session_date'] not in scheduled or not scheduled[bar['session_date']]['closes_at'] or instant(scheduled[bar['session_date']]['closes_at'])<=instant(as_of))]
    bars=calendar_from_rows(rows,bars,as_of)
    def metric(key,label,needed,unit,formula,operation):
        reason='insufficient_observations' if len(bars)<needed else None
        sample=bars[-needed:]
        if not reason and any(bar['adjustedClose'] is None or bar['adjustedClose']<=0 for bar in sample): reason='adjusted_close_unavailable'
        if not reason and any(not bar['complete_session'] for bar in sample): reason='session_completeness_unverified'
        if not reason: reason=continuity_reason(sample)
        value=None
        if not reason:
            try: value=operation(sample)
            except (ValueError,OverflowError,ZeroDivisionError): reason='non_finite_or_invalid_calculation'
        if value is not None and (not math.isfinite(value) or ('change_' in key and value<=-1) or ('volatility' in key and value<0)):
            reason='non_finite_or_invalid_calculation';value=None
        return {'metric_id':key,'label':label,'value':value,'unit':unit,'formula_version':formula,
                'adjustment':PRICE_BASIS,'start_at':sample[0]['session_date'] if value is not None else None,
                'end_at':sample[-1]['session_date'] if value is not None else None,
                'evidence_ids':[evidence_id] if value is not None else [],'missing_reason':reason,'evidence_id':None}
    result=[metric(f'provider_adjusted_change_{n}',f'Variação do fechamento ajustado pelo provedor · {n} pregões',n+1,'fraction',
                   'provider_A_end/provider_A_start-1:v1',lambda sample:sample[-1]['adjustedClose']/sample[0]['adjustedClose']-1) for n in (1,5,21,63)]
    std=lambda sample:statistics.stdev(math.log(b['adjustedClose'])-math.log(a['adjustedClose']) for a,b in zip(sample,sample[1:]))
    result.append(metric('provider_adjusted_daily_volatility_20','Desvio diário dos logs da série ajustada · 20 retornos',21,'fraction','sample_std(log_provider_A_returns):v1',std))
    result.append(metric('provider_adjusted_annualized_volatility_20','Volatilidade da série ajustada · anualização 252',21,'annualized_fraction','sample_std(log_provider_A_returns)*sqrt(252):v1',lambda sample:std(sample)*math.sqrt(252)))
    def alignment(sample,n):
        deviation=std(sample[-21:])
        if deviation==0: raise ZeroDivisionError
        return (math.log(sample[-1]['adjustedClose'])-math.log(sample[-n-1]['adjustedClose']))/(deviation*math.sqrt(n))
    for n in (5,21):
        item=metric(f'technical_alignment_{n}',f'Alinhamento técnico experimental · janela {n}',max(21,n+1),'dimensionless',
                    f'log(A_end/A_start)/(sample_std(last20_log_A_returns)*sqrt({n})):v1',lambda sample,n=n:alignment(sample,n))
        if item['missing_reason']=='non_finite_or_invalid_calculation' and result[4]['value']==0:
            item['missing_reason']='zero_daily_volatility'
        result.append(item)
    return result


def price_projection(store,asset,as_of):
    """Read-only daily UI projection; close and A retain distinct meanings."""
    selected=selected_vintage(store,asset,as_of)
    if selected is None:return None
    start,end=news_window(as_of)
    lower=instant(start).astimezone(SAO_PAULO).date().isoformat()
    upper=instant(end).astimezone(SAO_PAULO).date().isoformat()
    bars=[{**bar,'date':bar['session_date'],'provider':'brapi','adjustment':'unknown',
           'adjusted_close_basis':PRICE_BASIS,'vintage_id':selected['vintage_id']}
          for bar in selected['normalized']['bars'] if lower<=bar['session_date']<=upper]
    metrics=adjusted_metrics(selected['normalized'],json.loads(selected['calendar_json']),as_of,selected['vintage_id'])
    return {'bars':bars,'metrics':metrics,'snapshot':{key:selected[key] for key in
            ('vintage_id','fetched_at','latest_capture_at','available_at','response_sha256','policy_sha256')},
            'policy_id':POLICY['policy_id'],'subject_to_revision':True}


def vintage_status(store,as_of):
    if not store.rows("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_vintages'"):return None
    rows=store.rows('SELECT v.*,a.ticker,a.currency FROM price_vintages v JOIN assets a ON a.asset_id=v.asset_id WHERE v.available_at<=? ORDER BY v.available_at DESC,v.rowid DESC LIMIT 1',(iso(as_of),))
    if not rows:return None
    row=rows[0];normalized=validate_vintage(row,row,as_of)
    return {'status':'partial','count':len(normalized['bars']),
            'reason':'Captura diária preservada e revisável; close informado distinto do ajuste com proventos',
            'capability':'Observações da última aquisição: '+row['ticker'],
            'last_success_at':row['fetched_at'],'available_start':normalized['bars'][0]['session_date'],
            'available_end':normalized['bars'][-1]['session_date'],
            'details':{'vintage_id':row['vintage_id'],'available_at':row['available_at'],
                       'policy_id':POLICY['policy_id'],'subject_to_revision':True,'source_url':row['source_url']}}
