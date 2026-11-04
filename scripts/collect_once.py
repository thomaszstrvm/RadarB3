"""Explicit collection-only refresh, scoped to its own children; never inference.

The timeout is checked between jobs. An already running synchronous collector
finishes or reaches its transport timeout; this is not a hard process deadline.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend  # Load the verified SQLite DLL before sqlite3.
import sqlite3
from backend.core import Conflict, Store, uid, wal_supported
from backend.timeutil import instant, iso
from backend.worker import Worker

TERMINAL = {'succeeded', 'partial', 'failed', 'cancelled', 'blocked', 'paused'}
CHILD_KINDS = {'catalog', 'news', 'prices', 'macro'}


def refresh_children(store, refresh):
    """Validate the internal result before it can widen the execution scope."""
    ids = (refresh.get('result') or {}).get('children')
    if not isinstance(ids, list) or len(ids) != 4 or len(set(ids)) != 4:
        raise ValueError('refresh_children_invalid')
    children = [store.get_job(job_id) for job_id in ids]
    if any(not child for child in children) or {child['kind'] for child in children} != CHILD_KINDS:
        raise ValueError('refresh_children_invalid')
    for child in children:
        if child['dedupe_key'] != f"refresh:{refresh['job_id']}:{child['kind']}":
            raise ValueError('refresh_child_identity_mismatch')
    return children


def collect_once(store, *, timeout=300.0, max_iterations=1000, request_key=None,
                 worker_factory=Worker, clock=time.monotonic, sleep=time.sleep):
    """Run only a refresh and its children; retain all unrelated jobs/reservations."""
    if timeout <= 0 or max_iterations < 1:
        raise ValueError('positive_collection_budget_required')
    previous_mode = os.environ.get('RADAR_EXECUTION_MODE')
    os.environ['RADAR_EXECUTION_MODE'] = 'collection-only'
    worker = None
    started = False
    deadline = clock() + timeout
    iterations = 0
    reason = 'iteration_limit'
    refresh = None
    jobs = []
    try:
        worker = worker_factory(store, execution_mode='collection-only')
        recover_ids = []
        if request_key:
            previous = store.rows('SELECT * FROM jobs WHERE dedupe_key=?', (request_key,))
            if previous:
                previous = store.decode_job(previous[0])
                if previous['kind'] != 'refresh' or previous['payload'] != {}:
                    raise ValueError('refresh_identity_mismatch')
                recover_ids = [previous['job_id']]
                if previous['status'] == 'succeeded':
                    recover_ids.extend(child['job_id'] for child in refresh_children(store, previous))
        # Acquire first: an active worker must reject this utility before enqueue.
        # A resumed request recovers only its validated tree, never unrelated jobs.
        worker.start(recover_job_ids=recover_ids, consumer_scope='restricted')
        started = True
        refresh = store.enqueue('refresh', {}, request_key or uid('collect_once'))
        if refresh['kind'] != 'refresh' or refresh['payload'] != {}:
            raise ValueError('refresh_identity_mismatch')
        while iterations < max_iterations:
            refresh = store.get_job(refresh['job_id'])
            jobs = [refresh]
            if refresh['status'] == 'succeeded':
                jobs.extend(refresh_children(store, refresh))
            elif refresh['status'] in TERMINAL:
                reason = 'refresh_not_succeeded'
                break
            if len(jobs) == 5 and all(j['status'] in TERMINAL for j in jobs):
                reason = 'completed'
                break
            if clock() >= deadline:
                reason = 'deadline_between_jobs'
                break
            iterations += 1
            if not worker.run_once(job_ids=[j['job_id'] for j in jobs]):
                sleep(min(1.0, max(0.0, deadline-clock())))
        # A final job may have completed on the final permitted iteration.
        refresh = store.get_job(refresh['job_id'])
        jobs = [refresh] + (refresh_children(store, refresh) if refresh['status'] == 'succeeded' else [])
        complete = len(jobs) == 5 and all(j['status'] in TERMINAL for j in jobs)
        if complete:
            reason = 'completed'
        return {'as_of': iso(), 'execution_mode': 'collection-only',
                'refresh_id': refresh['job_id'], 'completed': complete,
                'all_succeeded': complete and all(j['status'] == 'succeeded' for j in jobs),
                'stop_reason': reason, 'iterations': iterations,
                'jobs': [{k: j[k] for k in ('job_id', 'kind', 'status', 'attempts', 'not_before', 'error_code')} for j in jobs]}
    finally:
        try:
            if started:
                worker.close()
        finally:
            if previous_mode is None:
                os.environ.pop('RADAR_EXECUTION_MODE', None)
            else:
                os.environ['RADAR_EXECUTION_MODE'] = previous_mode


def existing_profile(path):
    # Refuse a missing DB or an active worker before Store initialization.
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as con:
        con.execute('PRAGMA query_only=ON')
        profile = con.execute("SELECT value_json FROM settings WHERE key='profile'").fetchone()
        if not profile or json.loads(profile[0]) not in {'real', 'demo'}:
            raise ValueError('existing_profile_required')
        lease = con.execute('SELECT expires_at FROM worker_lease WHERE singleton=1').fetchone()
        if lease and instant(lease[0]) > instant(iso()):
            raise Conflict('another_worker_is_active')
        return json.loads(profile[0])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True, help='Base existente; coleta real exige autorização operacional.')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--max-iterations', type=int, default=1000)
    parser.add_argument('--request-key', help='Identidade opcional para acompanhar o mesmo refresh.')
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.max_iterations < 1:
        parser.error('timeout e max-iterations devem ser positivos')
    profile = existing_profile(args.db)
    store = Store(args.db, profile, wal=wal_supported())
    result = collect_once(store, timeout=args.timeout, max_iterations=args.max_iterations, request_key=args.request_key)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['all_succeeded'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
