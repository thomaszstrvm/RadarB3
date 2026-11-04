"""Calendar provenance copied into bundles; no network or persistence."""
from datetime import date, timedelta
from .market_calendar import PACKAGE_ID, REVIEWED_PACKAGES, stored_package, storage_projection, validate_package_envelope
from .timeutil import instant, iso, SAO_PAULO


def calendar_from_rows(rows, bars, as_of):
    """Pure schedule calculation, shared by construction and bundle replay."""
    cutoff = instant(as_of)
    today = cutoff.astimezone(SAO_PAULO).date().isoformat()
    rows = [row for row in rows if row['session_date'] <= today and instant(row['verified_at']) <= cutoff]
    by_date = {r['session_date']: r for r in rows}
    if bars:
        first = date.fromisoformat(min(b['session_date'] for b in bars))
        end = date.fromisoformat(today)
        if not 0 <= (end-first).days < 3660:
            return [dict(bar, calendar=None) for bar in bars]
        required = [(first + timedelta(days=i)).isoformat() for i in range((end-first).days+1)]
        if any(day not in by_date for day in required):
            return [dict(bar, calendar=None) for bar in bars]
    knowledge_at = max((r['verified_at'] for r in rows), default=None)
    expected = next((r['session_date'] for r in reversed(rows) if r['is_open'] and instant(r['closes_at']) <= cutoff), None)
    previous = None
    span = []
    metadata = {}
    for row in rows:
        if span and date.fromisoformat(row['session_date']) != date.fromisoformat(span[-1]['session_date']) + timedelta(days=1):
            previous, span = None, []
        span.append(row)
        if row['is_open']:
            coherent = len({(r['source'], r['version']) for r in span}) == 1
            if coherent and today in by_date and instant(row['closes_at']) <= cutoff:
                metadata[row['session_date']] = {
                    'session_date': row['session_date'], 'closes_at': row['closes_at'],
                    'previous_open_session': previous,
                    'closed_dates': [r['session_date'] for r in span if not r['is_open']],
                    'source': row['source'], 'version': row['version'],
                    'verified_at': knowledge_at,
                    'expected_last_session': expected,
                }
            previous, span = row['session_date'], [row]
    return [dict(bar, calendar=metadata.get(bar['session_date'])) for bar in bars]


def calendar_context(store, bars, as_of):
    """Select the cumulative package known at the cutoff, preserving earlier replay."""
    cutoff = instant(as_of)
    rows = store.rows('SELECT * FROM market_sessions WHERE session_date<=? AND verified_at<=? ORDER BY session_date',
                      (cutoff.astimezone(SAO_PAULO).date().isoformat(), iso(cutoff)))
    # A package's documentary verification is not its operational availability.
    legacy = [row for row in rows if row['version'] not in REVIEWED_PACKAGES]
    if store.rows("SELECT 1 FROM sqlite_master WHERE type='table' AND name='calendar_packages'"):
        records = store.rows('SELECT package_id,document_sha256,verified_at,available_at,payload_json FROM calendar_packages WHERE available_at<=? ORDER BY available_at,package_id',(iso(cutoff),))
        if records:
            packages = [stored_package(tuple(record.values()), as_of) for record in records]
            envelope, scheduled = packages[-1]
            if packages[0][0]['package_id'] != PACKAGE_ID:
                raise ValueError('calendar_predecessor_required')
            projection = storage_projection(scheduled, packages[0][1]) if len(packages) > 1 else scheduled
            ids = [item[0]['package_id'] for item in packages]
            placeholders = ','.join('?' for _ in ids)
            stored = store.rows(f'SELECT * FROM market_sessions WHERE version IN ({placeholders}) ORDER BY session_date', tuple(ids))
            if stored != projection:
                raise ValueError('calendar_package_sessions_mismatch')
            annotated = calendar_from_rows(scheduled,bars,as_of)
            for bar in annotated:
                if bar['calendar']:
                    bar['calendar']['available_at'] = envelope['available_at']
            return annotated,envelope
    return calendar_from_rows(legacy,bars,as_of),None


def calendar_for_bars(store, bars, as_of):
    return calendar_context(store,bars,as_of)[0]


def price_session_state(envelope, as_of):
    """Decide the price gate from one already validated calendar and a cutoff."""
    cutoff = instant(as_of)
    day = cutoff.astimezone(SAO_PAULO).date().isoformat()
    result = {'automatic_prices': 'blocked', 'reason_code': 'calendar_date_unverified',
              'reason': 'Data fora do calendário verificado; coleta automática de preços suspensa',
              'session_date': day, 'closes_at': None, 'next_attempt_at': None}
    sessions = envelope['document']['sessions'] if envelope else []
    session = next((entry for entry in sessions if entry[0] == day), None)
    if session is None:
        return result
    if not session[1]:
        return {**result, 'automatic_prices': 'closed_session', 'reason_code': 'closed_session',
                'reason': 'Dia sem pregão no calendário verificado'}
    close = instant(session[2])
    result['closes_at'] = iso(close)
    if cutoff <= close:
        return {**result, 'automatic_prices': 'waiting_for_close',
                'reason_code': 'correction_period_not_ended',
                'reason': 'Aguardando o fim do período de fechamento e correção dos preços',
                'next_attempt_at': iso(close + timedelta(seconds=1))}
    return {**result, 'automatic_prices': 'ready', 'reason_code': None,
            'reason': 'Sessão encerrada; coleta automática de preços liberada pela agenda'}


def price_schedule_state(store, as_of):
    """Use the same validated, point-in-time gate in both collection launchers."""
    try:
        _, envelope = calendar_context(store, [], as_of)
        return price_session_state(envelope, as_of)
    except (ValueError, TypeError, KeyError):
        return {'automatic_prices': 'blocked', 'reason_code': 'calendar_package_inconsistent',
                'reason': 'Pacote de calendário inconsistente; coleta automática de preços suspensa',
                'session_date': None, 'closes_at': None, 'next_attempt_at': None}


def calendar_status(store, as_of):
    """Derive current limited coverage without rewriting collection history."""
    try:
        _, envelope = calendar_context(store, [], as_of)
        schedule = price_session_state(envelope, as_of)
    except (ValueError, TypeError, KeyError):
        return {'status':'unavailable','reason':'Pacote de calendário inconsistente; uso recusado',
                'available_at':None,'available_start':None,'available_end':None,'count':0,
                'capability':'Cobertura documental não validada',
                'error_code':'calendar_package_inconsistent','next_attempt_at':None,
                'details':{'automatic_prices':'blocked','reason_code':'calendar_package_inconsistent',
                           'session_date':None,'closes_at':None}}
    if envelope is None:
        return {'status':'unavailable','reason':schedule['reason'],
                'available_at':None,'available_start':None,'available_end':None,'count':0,
                'capability':'Cobertura documental não validada',
                'error_code':schedule['reason_code'],'next_attempt_at':None,
                'details':{key:value for key,value in schedule.items() if key not in {'reason','next_attempt_at'}}}
    document = envelope['document']
    covered = document['start'] <= instant(as_of).astimezone(SAO_PAULO).date().isoformat() <= document['end']
    reason = (schedule['reason'] + '. ' +
              f"Calendário programado de ações à vista limitado a {document['start']}–{document['end']}; preços ainda exigem validação independente")
    if not covered:
        reason += '; corte fora da cobertura'
    open_days = sum(row[1] for row in document['sessions'])
    return {'status':'partial' if covered else 'unavailable','reason':reason,
            'available_at':envelope['available_at'],'available_start':document['start'],'available_end':document['end'],
            'count':len(document['sessions']),'capability':f'Dias documentais; {open_days} sessões programadas abertas',
            'error_code':schedule['reason_code'] if schedule['automatic_prices']=='blocked' else None,
            'next_attempt_at':schedule['next_attempt_at'],
            'details':{'package_id':envelope['package_id'],'document_sha256':envelope['document_sha256'],
                       'verified_at':document['verified_at'],'available_at':envelope['available_at'],
                       'documented_days':len(document['sessions']),'scheduled_open_days':open_days,
                       'close_semantics':document['close_semantics'],'provider_finality_certified':False,
                       **{key:value for key,value in schedule.items() if key not in {'reason','next_attempt_at'}}}}


def resolve_calendar_refs(prices, envelope, as_of):
    """A short reference never supplies its own adjacency or last-session facts."""
    rows = validate_package_envelope(envelope,as_of)
    resolved = calendar_from_rows(rows,prices,as_of)
    for original,bar in zip(prices,resolved):
        reference = original.get('calendar_ref')
        if reference is not None:
            if reference != envelope['package_id'] or bar['calendar'] is None or original.get('calendar') is not None:
                raise ValueError('calendar_reference_invalid')
            bar['calendar']['available_at'] = envelope['available_at']
        else:
            if original.get('calendar') is not None:
                raise ValueError('calendar_embedded_context_not_allowed')
            bar['calendar'] = None
    return resolved


def continuity_reason(sample):
    """Require provenance, exact open-session adjacency and the latest close."""
    try:
        for bar in sample:
            item = bar.get('calendar')
            if not isinstance(item, dict):
                return 'calendar_unverified'
            if not item['source'] or not item['version'] or item['session_date'] != bar['session_date']:
                return 'calendar_unverified'
            close = instant(item['closes_at'])
            instant(item['verified_at'])
            if close.astimezone(SAO_PAULO).date().isoformat() != bar['session_date']:
                return 'calendar_unverified'
        for left, right in zip(sample, sample[1:]):
            item = right['calendar']
            if any(left['calendar'][key] != item[key] for key in ('source', 'version', 'expected_last_session')):
                return 'calendar_provenance_mismatch'
            if item['previous_open_session'] != left['session_date']:
                return 'missing_market_session'
            start, end = date.fromisoformat(left['session_date']), date.fromisoformat(right['session_date'])
            if end <= start or (end-start).days > 3660:
                return 'calendar_unverified'
            closed = [(start + timedelta(days=i)).isoformat() for i in range(1, (end-start).days)]
            if item['closed_dates'] != closed:
                return 'calendar_coverage_gap'
        if sample[-1]['calendar']['expected_last_session'] != sample[-1]['session_date']:
            return 'missing_latest_market_session'
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'calendar_unverified'
    return None
