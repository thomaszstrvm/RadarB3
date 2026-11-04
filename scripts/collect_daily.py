"""One bounded, collection-only Windows task. Receipts link to durable job details."""
from __future__ import annotations

import argparse
from datetime import datetime, time as civil_time, timedelta
import json
import math
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import Conflict, Store, wal_supported
from backend.session_continuity import calendar_context
from backend.timeutil import SAO_PAULO, instant, iso
from backend.worker import Worker
from scripts.collect_once import TERMINAL, existing_profile


def price_skip_reason(store, now):
    day = now.astimezone(SAO_PAULO).date().isoformat()
    _, package = calendar_context(store, [], now)
    sessions = package['document']['sessions'] if package else []
    session = next((entry for entry in sessions if entry[0] == day), None)
    if not session:
        return 'calendar_date_unverified'
    if not session[1]:
        return 'closed_session'
    if now <= instant(session[2]):
        return 'correction_period_not_ended'
    return None


def daily_plan(store, now):
    local = now.astimezone(SAO_PAULO)
    day = local.date().isoformat()
    monday = local.date() - timedelta(days=local.weekday())
    weekly_key = 'daily:catalog:' + monday.isoformat()
    plan = {'news': 'daily:news:' + day, 'macro': 'daily:macro:' + day}
    skipped = {}
    since = iso(datetime.combine(monday, civil_time.min, SAO_PAULO))
    previous = store.rows("""SELECT job_id FROM jobs WHERE kind='catalog' AND dedupe_key<>?
        AND status='succeeded' AND updated_at BETWEEN ? AND ?
        AND json_extract(result_json,'$.provider_catalog_complete')=1 LIMIT 1""",
        (weekly_key, since, iso(now)))
    if previous:
        skipped['catalog'] = 'catalog_already_collected_this_week'
    else:
        plan['catalog'] = weekly_key
    reason = price_skip_reason(store, now)
    if reason:
        skipped['prices'] = reason
    else:
        plan['prices'] = 'daily:prices:' + day
    return plan, skipped


def validate_identity(job, kind, key):
    payload = job['payload']
    if job['kind'] != kind or job['dedupe_key'] != key or set(payload) != {'as_of'}:
        raise Conflict('daily_job_identity_mismatch')
    date = instant(payload['as_of']).astimezone(SAO_PAULO).date()
    if kind == 'catalog':
        date -= timedelta(days=date.weekday())
    if key != f'daily:{kind}:{date.isoformat()}':
        raise Conflict('daily_job_identity_mismatch')
    return job['job_id']


def scoped_jobs(store, plan):
    """Read only this runner's validated buckets. Fencing recovery uses these exact IDs."""
    current = {}
    outdated = {}
    for kind, key in plan.items():
        rows = store.rows("""SELECT * FROM jobs WHERE dedupe_key=? OR
            (kind=? AND dedupe_key GLOB ? AND dedupe_key<? AND status IN ('queued','running','retry_wait'))
            ORDER BY dedupe_key""", (key, kind, f'daily:{kind}:????-??-??', key))
        outdated[kind] = []
        for row in rows:
            job = store.decode_job(row)
            validate_identity(job, kind, job['dedupe_key'])
            if job['dedupe_key'] == key:
                current[kind] = job
            else:
                outdated[kind].append(job)
    return current, outdated


def collect_daily(store, *, as_of=None, timeout=1200, worker_factory=Worker):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('positive_collection_budget_required')
    if store.profile != 'real' or not store.setting('schedule_enabled', True):
        raise Conflict('real_enabled_schedule_required')
    now = instant(as_of or iso())
    plan, skipped = daily_plan(store, now)
    current, outdated = scoped_jobs(store, plan)
    recover = [job['job_id'] for job in current.values()]
    recover.extend(job['job_id'] for jobs in outdated.values() for job in jobs)
    worker = worker_factory(store, execution_mode='collection-only')
    worker.start(recover_job_ids=recover, consumer_scope='restricted')
    try:
        current, outdated = scoped_jobs(store, plan)
        ids = []
        superseded = []
        with store.transaction() as con:
            for kind, key in plan.items():
                prior = outdated[kind]
                # Never query today's feeds with yesterday's as_of: doing so could
                # advance an RSS ETag while discarding a new article after the old cutoff.
                barrier = max((old['not_before'] for old in prior), default=None)
                job = store.enqueue(kind, {'as_of': iso(now)}, key, not_before=barrier,
                                priority={'catalog': 30, 'prices': 40, 'macro': 40, 'news': 50}[kind],
                                connection=con)
                ids.append(validate_identity(job, kind, key))
                for old in prior:
                    if old['status'] == 'running':
                        raise Conflict('daily_job_recovery_scope_changed')
                    store.cancel_job(old['job_id'], connection=con)
                    superseded.append({'job_id':old['job_id'], 'replacement_id':job['job_id'],
                                       'not_before':old['not_before']})
                if prior and job['status'] in {'queued', 'retry_wait'} and barrier > job['not_before']:
                    con.execute('UPDATE jobs SET not_before=? WHERE job_id=?', (barrier, job['job_id']))
            if superseded:
                con.execute('INSERT INTO audit_events(event_type,created_at,payload_json) VALUES(?,?,?)',
                            ('daily_collection_superseded', iso(), json.dumps(superseded)))
        deadline = time.monotonic() + timeout
        while True:
            jobs = [store.get_job(job_id) for job_id in ids]
            pending = [job for job in jobs if job['status'] not in TERMINAL]
            if not pending:
                reason = 'completed'
                break
            remaining = deadline - time.monotonic()
            delay = max(0, min((instant(job['not_before']) - instant(iso())).total_seconds() for job in pending))
            if remaining <= 0 or delay >= remaining:
                reason = 'backoff_after_deadline' if remaining > 0 else 'deadline_between_jobs'
                break
            if not worker.run_once(job_ids=ids):
                time.sleep(min(remaining, max(0.2, min(delay, 5))))
        status = 'succeeded'
        if pending or any(job['status'] not in {'succeeded', 'partial'} for job in jobs):
            status = 'incomplete'
        elif any(job['status'] == 'partial' for job in jobs) or skipped.get('prices') == 'calendar_date_unverified':
            status = 'partial'
        return {'local_date': now.astimezone(SAO_PAULO).date().isoformat(), 'started_at': iso(now),
                'finished_at': iso(), 'execution_mode': 'collection-only', 'status': status,
                'stop_reason': reason, 'skipped': skipped, 'superseded': superseded,
                'jobs': [{**{k: j[k] for k in ('job_id','kind','dedupe_key','status','attempts','not_before','error_code')},
                          'attempts_this_run': j['attempts'] - current.get(j['kind'], {}).get('attempts', 0),
                          'gaps_count': len((j['result'] or {}).get('gaps', []))} for j in jobs]}
    finally:
        worker.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--receipts-dir', required=True, type=Path)
    parser.add_argument('--timeout', type=float, default=1200, help='Prazo em segundos conferido entre jobs.')
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('timeout deve ser positivo e finito')
    os.environ['RADAR_EXECUTION_MODE'] = 'collection-only'
    for key in ('BRAPI_TOKEN', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'AZURE_OPENAI_API_KEY'):
        os.environ.pop(key, None)
    args.receipts_dir.mkdir(parents=True, exist_ok=True)
    start = iso()
    day = instant(start).astimezone(SAO_PAULO).date().isoformat()
    path = args.receipts_dir / f'{day}-{uuid4().hex}.json'
    try:
        if existing_profile(args.db) != 'real' or not wal_supported():
            raise Conflict('existing_real_wal_profile_required')
        receipt = collect_daily(Store(args.db, 'real', wal=True), as_of=start, timeout=args.timeout)
    except Exception as exc:
        # Never persist provider exceptions, authentication data or private paths.
        code = str(exc) if isinstance(exc, (Conflict, ValueError)) and str(exc).isascii() and str(exc).replace('_', '').isalnum() else type(exc).__name__
        receipt = {'local_date': day, 'started_at': start, 'finished_at': iso(), 'execution_mode': 'collection-only',
                   'status': 'incomplete', 'error_code': code}
    temporary = path.with_suffix('.tmp')
    with temporary.open('x', encoding='utf-8') as out:
        json.dump(receipt, out, ensure_ascii=False, indent=2)
    temporary.replace(path)
    print(json.dumps(receipt, ensure_ascii=False))
    return {'succeeded': 0, 'partial': 2, 'incomplete': 1}[receipt['status']]


if __name__ == '__main__':
    raise SystemExit(main())
