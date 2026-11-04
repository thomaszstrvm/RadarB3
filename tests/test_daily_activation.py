"""The scheduled entry uses disposable databases, local fixtures and fake transports."""
import json
from datetime import timedelta

import pytest

from backend.core import Conflict, Store
from backend.market_calendar import import_calendar_package
from backend.safe_http import FetchError
from backend.timeutil import instant, iso
from backend.worker import Worker
from scripts import collect_daily as daily
from test_cumulative_calendar import DIRECTORY, ROOT, reviewed_document


FIRST = '2026-11-04T20:45:00-03:00'
LATER = '2026-11-04T23:30:00-03:00'
NEXT = '2026-11-05T20:45:00-03:00'


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.market_calendar.iso', lambda value=None: iso(value or '2026-11-04T10:00:00Z'))
    store = Store(tmp_path / 'temporary.sqlite3', 'real')
    with store.connect() as con:
        import_calendar_package(con, reviewed_document(False), ROOT / 'docs/qa-20261031/data-readiness/calendar/sources', expected_profile='real')
        import_calendar_package(con, reviewed_document(True), DIRECTORY / 'sources', expected_profile='real')
    calls = []
    def handle(job):
        calls.append((job['kind'], job['job_id'], job['payload'], job['checkpoint']))
        return {'provider_catalog_complete': True} if job['kind'] == 'catalog' else {}
    def factory(db, execution_mode):
        assert execution_mode == 'collection-only'
        return Worker(db, execution_mode=execution_mode, handlers={kind: handle for kind in ('news','macro','catalog','prices')})
    return store, calls, factory


def test_local_day_replay_advances_only_due_jobs_and_isolates_queue(system):
    store, calls, factory = system
    unrelated = [store.enqueue(kind) for kind in ('analyse', 'gdelt_partition', 'refresh')]
    first = daily.collect_daily(store, as_of=FIRST, worker_factory=factory)
    assert first['status'] == 'succeeded' and len(calls) == 4
    again = daily.collect_daily(store, as_of=LATER, worker_factory=factory)
    assert len(calls) == 4 and all(j['attempts_this_run'] == 0 for j in again['jobs'])
    assert [j['job_id'] for j in first['jobs']] == [j['job_id'] for j in again['jobs']]
    daily.collect_daily(store, as_of=NEXT, worker_factory=factory)
    assert len(calls) == 7 and [kind for kind, *_ in calls].count('catalog') == 1
    assert all(store.get_job(j['job_id']) == j for j in unrelated)
    assert not store.rows('SELECT * FROM quota_ledger') and not store.rows('SELECT * FROM worker_lease')


@pytest.mark.parametrize('when,expected', [
    ('2026-11-06T20:00:00-03:00', 'correction_period_not_ended'),
    ('2026-11-06T20:00:01-03:00', None),
    ('2026-11-20T20:45:00-03:00', 'closed_session'),
    ('2027-01-04T20:45:00-03:00', 'calendar_date_unverified'),
])
def test_reviewed_calendar_gates_prices(system, when, expected):
    store, calls, factory = system
    receipt = daily.collect_daily(store, as_of=when, worker_factory=factory)
    assert receipt['skipped'].get('prices') == expected
    assert ('prices' in [kind for kind, *_ in calls]) == (expected is None)
    assert (receipt['status'] == 'partial') == (expected == 'calendar_date_unverified')


def test_existing_weekly_catalog_prevents_first_trigger_recollection(system):
    store, calls, factory = system
    job = store.enqueue('catalog', {}, 'refresh:fixture:catalog')
    with store.transaction() as con:
        con.execute("""UPDATE jobs SET status='succeeded',result_json='{"provider_catalog_complete":true}',
                    updated_at=? WHERE job_id=?""", (iso(FIRST), job['job_id']))
    receipt = daily.collect_daily(store, as_of=NEXT, worker_factory=factory)
    assert receipt['skipped']['catalog'] == 'catalog_already_collected_this_week'
    assert 'catalog' not in [kind for kind, *_ in calls]


def test_active_worker_and_bad_identity_abort_before_writing_jobs(system):
    store, _, factory = system
    store.acquire_worker('test-owner', execution_mode='collection-only')
    with pytest.raises(Conflict, match='another_worker_is_active'):
        daily.collect_daily(store, as_of=FIRST, worker_factory=factory)
    assert not store.rows('SELECT * FROM jobs')
    store.release_worker('test-owner')
    foreign = store.enqueue('analyse', {'as_of': iso(FIRST)}, 'daily:prices:2026-11-04')
    with pytest.raises(Conflict, match='daily_job_identity_mismatch'):
        daily.collect_daily(store, as_of=FIRST, worker_factory=factory)
    assert store.get_job(foreign['job_id']) == foreign and not store.rows('SELECT * FROM worker_lease')


def test_old_news_retry_keeps_history_and_backoff_but_never_reads_with_old_cutoff(system):
    store, calls, normal = system
    def failed_once(db, execution_mode):
        worker = normal(db, execution_mode)
        def backoff(job):
            worker.store.checkpoint(job['job_id'], worker.owner, {'source':'fixture'}, 0.5)
            raise FetchError('http_429', retryable=True, retry_after=600)
        worker.handlers['news'] = backoff
        return worker
    first = daily.collect_daily(store, as_of=FIRST, timeout=15, worker_factory=failed_once)
    assert first['status'] == 'incomplete' and first['stop_reason'] == 'backoff_after_deadline'
    old = next(j for j in first['jobs'] if j['kind'] == 'news')
    same_day = daily.collect_daily(store, as_of=LATER, timeout=15, worker_factory=normal)
    assert same_day['stop_reason'] == 'backoff_after_deadline' and not same_day['superseded']
    next_day = daily.collect_daily(store, as_of=NEXT, timeout=15, worker_factory=normal)
    new = next(j for j in next_day['jobs'] if j['kind'] == 'news')
    assert next_day['stop_reason'] == 'backoff_after_deadline'
    assert next_day['superseded'] == [{'job_id':old['job_id'], 'replacement_id':new['job_id'], 'not_before':old['not_before']}]
    assert new['attempts'] == 0 and new['not_before'] == old['not_before']
    preserved = store.get_job(old['job_id'])
    assert preserved['status'] == 'cancelled' and preserved['attempts'] == 1
    assert preserved['not_before'] == old['not_before'] and preserved['checkpoint'] == {'source':'fixture'}
    with store.transaction() as con:
        con.execute('UPDATE jobs SET not_before=? WHERE job_id=?', (iso(instant(iso()) - timedelta(seconds=1)),new['job_id']))
    resumed = daily.collect_daily(store, as_of=NEXT, worker_factory=normal)
    news = next(j for j in resumed['jobs'] if j['kind'] == 'news')
    assert news['job_id'] == new['job_id'] and news['attempts_this_run'] == 1
    assert [x[2:] for x in calls if x[0]=='news'] == [({'as_of':iso(NEXT)}, {})]
    again = daily.collect_daily(store, as_of=NEXT, worker_factory=normal)
    assert all(j['attempts_this_run'] == 0 for j in again['jobs'])
    assert len(store.rows("SELECT job_id FROM jobs WHERE kind='news'")) == 2


def test_partial_source_results_distinct_from_success_and_other_jobs_still_run(system):
    store, calls, normal = system
    def partial(db, execution_mode):
        worker = normal(db, execution_mode)
        worker.handlers['news'] = lambda job: {'status':'partial', 'gaps':['unavailable_fixture']}
        return worker
    result = daily.collect_daily(store, as_of=FIRST, worker_factory=partial)
    assert result['status'] == 'partial' and result['stop_reason'] == 'completed'
    assert next(j for j in result['jobs'] if j['kind']=='news')['gaps_count'] == 1
    assert {j[0] for j in calls} == {'prices','catalog','macro'}


def test_same_day_price_retry_keeps_its_checkpoint_and_finishes_once(system):
    store, calls, normal = system
    def fail_on_first(db, execution_mode):
        worker = normal(db, execution_mode)
        standard = worker.handlers['prices']
        def prices_with_retry(job):
            if job['attempts'] == 1:
                worker.store.checkpoint(job['job_id'], worker.owner, {'completed':['fixture-A']}, 0.5)
                raise FetchError('fixture_transient', retryable=True, retry_after=0)
            return standard(job)
        worker.handlers['prices'] = prices_with_retry
        return worker
    receipt = daily.collect_daily(store, as_of=FIRST, worker_factory=fail_on_first)
    prices = next(j for j in receipt['jobs'] if j['kind']=='prices')
    assert receipt['status'] == 'succeeded' and prices['attempts'] == 2
    assert [call[-1] for call in calls if call[0]=='prices'] == [{'completed':['fixture-A']}]
    replay = daily.collect_daily(store, as_of=LATER, worker_factory=normal)
    assert all(job['attempts_this_run'] == 0 for job in replay['jobs'])


def test_acquiring_stale_lease_does_not_recover_an_unrelated_running_job(system):
    store, _, factory = system
    job = store.enqueue('analyse')
    store.acquire_worker('synthetic-stale-worker')
    assert store.claim('synthetic-stale-worker')['job_id'] == job['job_id']
    with store.transaction() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00.000000Z'")
    prior = store.get_job(job['job_id'])
    assert daily.collect_daily(store, as_of=FIRST, worker_factory=factory)['status'] == 'succeeded'
    assert prior == store.get_job(job['job_id'])


def test_cli_records_safe_failure_and_never_creates_missing_database(tmp_path, monkeypatch):
    target = tmp_path/'missing.sqlite3'
    folder = tmp_path/'receipts'
    for key in ('BRAPI_TOKEN','OPENAI_API_KEY','ANTHROPIC_API_KEY','AZURE_OPENAI_API_KEY'):
        monkeypatch.setenv(key, 'synthetic-secret')
    monkeypatch.setenv('RADAR_EXECUTION_MODE','analysis-enabled')
    assert daily.main(['--db', str(target), '--receipts-dir', str(folder)]) == 1
    receipt = json.loads(next(folder.glob('*.json')).read_text(encoding='utf-8'))
    assert receipt['status'] == 'incomplete' and receipt['execution_mode'] == 'collection-only'
    assert 'synthetic-secret' not in json.dumps(receipt) and not target.exists()
    with pytest.raises(SystemExit):
        daily.main(['--db', str(target), '--receipts-dir', str(folder),'--timeout','nan'])
