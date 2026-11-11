"""Read-only operational clocks; readiness is not proof of a running worker."""
from .timeutil import iso, instant, SAO_PAULO
from contextlib import nullcontext
import json
from .session_continuity import calendar_status
from .source_status import provider_state_at, latest_clock, known_clock


def manual_quota(store, connection=None, now=None):
    """Effective local daily cap; pending/uncertain reservations still count."""
    period = instant(now or iso()).astimezone(SAO_PAULO).date().isoformat()
    with nullcontext(connection) if connection is not None else store.connect() as con:
        configured = con.execute("SELECT value_json FROM settings WHERE key='max_analyses_per_day'").fetchone()
        limit = json.loads(configured[0]) if configured else 10
        valid = type(limit) is int and 0 <= limit <= 10
        limit = limit if valid else 0
        used = con.execute("SELECT COALESCE(SUM(units),0) FROM quota_ledger WHERE provider='codex_cli' AND period=? AND status<>'released'", (period,)).fetchone()[0]
        return {'used': used, 'limit': limit, 'remaining': max(0, limit-used), 'period': period}, valid


def manual_slot(store, connection=None, now=None):
    """Read readiness and its private owner from the same database snapshot."""
    now = now or iso()
    with nullcontext(connection) if connection is not None else store.connect() as con:
        lease = con.execute("SELECT owner FROM worker_lease WHERE singleton=1 AND execution_mode='manual-analysis' AND expires_at>?", (now,)).fetchone()
        if not lease:
            return 'unavailable', None
        busy = con.execute("""SELECT 1 FROM jobs WHERE kind='analyse'
            AND status IN ('queued','running','retry_wait')
            AND json_extract(payload_json,'$.manual_owner')=? LIMIT 1""", (lease['owner'],)).fetchone()
        return ('busy' if busy else 'ready'), lease['owner']


def worker_state(store, now=None):
    now = now or iso()
    leases = store.rows('SELECT heartbeat_at,expires_at,execution_mode FROM worker_lease WHERE singleton=1')
    if not leases:
        return {'status': 'absent', 'heartbeat_at': None, 'expires_at': None, 'execution_mode': None}
    lease = leases[0]
    return {**lease, 'status': 'active' if instant(lease['expires_at']) > instant(now) else 'stale'}


def job_admission(store, process_mode, connection=None, now=None):
    """Describe which manual jobs this lease explicitly promises to consume."""
    now = now or iso()
    result = {'allowed_kinds': [], 'reason': 'manual_consumer_absent', 'expires_at': None}
    if process_mode == 'manual-analysis':
        return {**result, 'reason': 'manual_operation_not_allowed'}
    with nullcontext(connection) if connection is not None else store.connect() as con:
        lease = con.execute('SELECT * FROM worker_lease WHERE singleton=1').fetchone()
        if not lease:
            return result
        if instant(lease['expires_at']) <= instant(now):
            return {**result, 'reason': 'manual_consumer_expired'}
        if (lease['consumer_scope_owner'] != lease['owner']
                or lease['consumer_scope'] not in {'general', 'restricted'}):
            return {**result, 'reason': 'manual_consumer_unverified'}
        if lease['consumer_scope'] == 'restricted':
            return {**result, 'reason': 'manual_consumer_restricted'}
        if lease['execution_mode'] not in {'collection-only', 'analysis-enabled'}:
            return {**result, 'reason': 'manual_consumer_incompatible'}
        # Retry also serves historical/internal kinds supported by Worker.execute.
        # New requests remain restricted by the API's JobRequest contract.
        kinds = ['refresh', 'backfill', 'probe', 'catalog', 'news', 'prices', 'macro',
                 'diagnostics', 'refresh_news', 'refresh_prices', 'gdelt', 'gdelt_partition',
                 'bcb', 'bundle', 'maintenance', 'demo_task']
        if process_mode == lease['execution_mode'] == 'analysis-enabled':
            kinds.extend(['analyse', 'analysis'])
        return {'allowed_kinds': kinds, 'reason': 'ready', 'expires_at': lease['expires_at']}


def collection_state(store, news_ids, now=None):
    now = now or iso()
    clock_errors = {}
    def latest(table):
        columns = {row['name'] for row in store.rows('PRAGMA table_info(' + table + ')')}
        fields = 'fetched_at,available_at' if 'available_at' in columns else 'fetched_at'
        rows = store.rows('SELECT ' + fields + ' FROM ' + table)
        clocks = []
        for row in rows:
            if any(known_clock(value, now)[1] for value in row.values()):
                clock_errors[table] = 'data_clock_invalid'
                continue
            clocks.append(row['fetched_at'])
        return latest_clock(clocks, now)
    projected = [provider_state_at(row, now) for row in store.rows(
        "SELECT * FROM provider_state WHERE provider_id<>'codex_cli' ORDER BY provider_id")]
    def success(ids):
        return latest_clock((row['last_success_at'] for row in projected if row['provider_id'] in ids), now)
    sources = [{key: row.get(key) for key in ('provider_id','last_attempt_at','last_success_at','error_code','next_attempt_at')}
               | ({'reason': row['clock_error'], 'details': {'invalid_clock_fields':row['invalid_clock_fields']}}
                  if row.get('clock_error') else {}) for row in projected]
    scheduled = calendar_status(store,now)
    if scheduled is not None:
        previous = next((item for item in sources if item['provider_id']=='market_calendar'), {})
        sources = [item for item in sources if item['provider_id']!='market_calendar'] + [
            {'provider_id':'market_calendar','last_attempt_at':previous.get('last_attempt_at'),'last_success_at':scheduled['available_at'],
             'error_code':previous.get('error_code') if previous.get('reason') else scheduled['error_code'],
             'next_attempt_at':scheduled['next_attempt_at'],
             'reason':previous.get('reason') or scheduled['reason'],
             'details':{**scheduled['details'], **previous.get('details',{})}}]
    latest_price=latest('price_revisions')
    if store.rows("SELECT 1 FROM sqlite_master WHERE name='price_vintages' AND type='table'"):
        latest_price=latest_clock((latest_price,latest('price_vintages')), now)
    return {'sources': sources,
            'latest_news_fetch': success(news_ids), 'latest_price_fetch': success(['brapi_prices']),
            'latest_macro_fetch': success(['bcb_sgs']),
            'latest_news_data_at': latest('article_revisions'), 'latest_price_data_at': latest_price,
            'latest_macro_data_at': latest('macro_observations'), 'data_clock_errors': clock_errors}
