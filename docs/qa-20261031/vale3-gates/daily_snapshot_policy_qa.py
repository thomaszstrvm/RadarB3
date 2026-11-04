"""Pure QA demonstration; never imported by the adapter or written to real DB.

A period-ended daily snapshot may later be revised. This policy does not
certify split-only adjustment, the formula of total return, or all trades.
"""
from datetime import datetime
import hashlib
import json
import math

from backend.market_calendar import package_rows
from backend.timeutil import instant, SAO_PAULO, UTC

POLICY = 'brapi-v1-daily-ended-revisable-qa-v1'
DICTIONARY_SHA256 = '5de4099f6f5d824c4462e0223ccd65d90977d4aab529f94816dda322bbcb3c1b'
DICTIONARY_AVAILABLE_AT = '2026-11-01T02:27:21.320149Z'


def verify_daily_snapshot(body, response_sha256, dictionary, fetched_at, available_at, calendar_document, ticker='VALE3'):
    """Return validated session labels and proof, or refuse the whole vintage."""
    if not isinstance(body, bytes) or len(body)>2_000_000 or hashlib.sha256(body).hexdigest()!=response_sha256:
        raise ValueError('snapshot_integrity_mismatch')
    if hashlib.sha256(dictionary).hexdigest()!=DICTIONARY_SHA256:
        raise ValueError('dictionary_not_reviewed')
    fetched, available = instant(fetched_at), instant(available_at)
    if available < fetched:
        raise ValueError('availability_precedes_fetch')
    if available < instant(DICTIONARY_AVAILABLE_AT):
        raise ValueError('policy_documentation_not_yet_known')
    quotes = json.loads(body).get('results')
    if not isinstance(quotes,list) or len(quotes)!=1 or not isinstance(quotes[0],dict):
        raise ValueError('snapshot_shape_invalid')
    quote = quotes[0]
    if quote.get('symbol')!=ticker or quote.get('historicalSymbol') not in (None,ticker) or quote.get('currency')!='BRL':
        raise ValueError('snapshot_identity_mismatch')
    if quote.get('usedInterval')!='1d':
        raise ValueError('snapshot_daily_interval_required')
    days = {row['session_date']:row for row in package_rows(calendar_document,available_at)}
    bars = quote.get('historicalDataPrice')
    if not isinstance(bars,list) or not bars:
        raise ValueError('snapshot_daily_bars_required')
    sessions=[]
    for bar in bars:
        stamp=bar.get('date')
        if type(stamp) not in (int,float) or not math.isfinite(stamp):
            raise ValueError('snapshot_session_date_required')
        date = datetime.fromtimestamp(stamp,UTC)
        # Local normalization policy, not a claim made by the dictionary.
        # The archived sample uses 03:00 UTC, which is midnight in Sao Paulo.
        day = (date.date() if date.hour==date.minute==date.second==0 else date.astimezone(SAO_PAULO).date()).isoformat()
        row=days.get(day)
        if not row or not row['is_open'] or fetched<=instant(row['closes_at']):
            raise ValueError('snapshot_period_not_ended_or_unknown')
        if day in sessions:
            raise ValueError('snapshot_duplicate_session')
        if type(bar.get('close')) not in (int,float) or not math.isfinite(bar['close']) or bar['close']<=0:
            raise ValueError('snapshot_close_invalid')
        if type(bar.get('volume')) not in (int,float) or not math.isfinite(bar['volume']) or bar['volume']<0:
            raise ValueError('snapshot_volume_invalid')
        sessions.append(day)
    if sessions!=sorted(sessions):
        raise ValueError('snapshot_session_order_invalid')
    return {'policy':POLICY,'response_sha256':response_sha256,'dictionary_sha256':DICTIONARY_SHA256,
            'fetched_at':fetched_at,'available_at':available_at,'sessions':sessions,
            'provider_dates_unix':[bar['date'] for bar in bars],
            'normalization':'local_policy_utc_midnight_as_civil_label_otherwise_sao_paulo',
            'period_ended':True,'subject_to_revision':True,'adjustment_for_price_return':'unverified',
            'production_ingestion':False}
