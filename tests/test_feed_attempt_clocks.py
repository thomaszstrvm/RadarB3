"""Synthetic clocks only: no real transport, CLI, service, or operational DB."""
import json

import pytest

from backend.adapters import RSSAdapter
from backend.core import Store
from backend.safe_http import FetchError
from backend.source_status import CLOCK_ERROR, documentary_clock, provider_state_at
from backend.worker import Worker


NOW = '2026-11-11T12:00:00.000000Z'
OLD = '2026-10-30T12:00:00.000000Z'
FAILURE = '2026-11-10T12:00:00.000000Z'
SOURCE = {
    'id': 'us_yahoo', 'allowed_hostnames': ['news.yahoo.com'], 'rss_urls': [],
    'feed_status': 'discovery_http_429', 'last_operational_probe_at': '2026-10-29',
    'feed_checked_at': '2026-10-29',
}


class NoHTTP:
    def get(self, *args, **kwargs):
        raise AssertionError('Configuration evaluation must not access HTTP')


def state(store):
    return store.rows('SELECT * FROM provider_state WHERE provider_id=?', (SOURCE['id'],))[0]


def row(error='http_503', attempt=FAILURE, details=None):
    return dict(provider_id=SOURCE['id'], status='unavailable', last_attempt_at=attempt,
                last_success_at=OLD, error_code=error, next_attempt_at=None,
                details_json=json.dumps(details or {}))


def evaluation(**changes):
    return dict(evaluated_at=NOW, reason='discovery_http_429', network_attempted=False,
                documented_probe_at='2026-10-29', documented_check_at='2026-10-29', **changes)


def test_repeated_configuration_checks_never_create_network_clock(tmp_path, monkeypatch):
    store = Store(tmp_path / 'synthetic.db')
    monkeypatch.setattr('backend.adapters.iso', lambda: NOW)
    adapter = RSSAdapter(store, SOURCE, NoHTTP())
    first = adapter.collect(OLD)
    assert first['configuration_evaluation']['evaluated_at'] == NOW  # not the data cutoff
    assert first['configuration_evaluation']['network_attempted'] is False
    adapter.collect(NOW)
    stored = state(store)
    assert stored['last_attempt_at'] is None
    assert stored['last_success_at'] is None
    assert stored['error_code'] == 'rss_endpoint_not_verified'
    detail = json.loads(stored['details_json'])['configuration_evaluation']
    assert detail['documented_probe_at'] == '2026-10-29'
    assert detail['documented_check_at'] == '2026-10-29'
    assert provider_state_at(stored, NOW)['configuration_evaluation']['reason'] == 'discovery_http_429'


def test_local_check_preserves_real_failure_success_and_retry(tmp_path, monkeypatch):
    store = Store(tmp_path / 'synthetic.db')
    before = row(details={'gaps': ['http_503']})
    before['next_attempt_at'] = '2026-11-12T12:00:00.000000Z'
    with store.transaction() as con:
        con.execute('INSERT INTO provider_state VALUES(?,?,?,?,?,?,?)', tuple(before.values()))
    monkeypatch.setattr('backend.adapters.iso', lambda: NOW)
    RSSAdapter(store, SOURCE, NoHTTP()).collect()
    stored = state(store)
    for key in before:
        if key != 'details_json':
            assert stored[key] == before[key]
    projected = provider_state_at(stored, NOW)
    assert projected['error_code'] == 'http_503'
    assert projected['last_attempt_at'] == FAILURE
    assert projected['last_success_at'] == OLD
    assert json.loads(projected['details_json'])['gaps'] == ['http_503']


@pytest.mark.parametrize('error', ['discovery_http_429', 'rss_reuse_terms_review_required', 'rss_endpoint_not_verified'])
def test_legacy_documentary_clock_is_projected_without_rewriting(error):
    stored = row(error)
    original = dict(stored)
    projected = provider_state_at(stored, NOW, SOURCE)
    assert stored == original
    assert projected['last_attempt_at'] is None
    assert projected['last_success_at'] == OLD
    assert projected['error_code'] == 'rss_endpoint_not_verified'
    assert projected['configuration_evaluation']['reason'] == error
    assert projected['configuration_evaluation']['documented_probe_at'] == '2026-10-29'


@pytest.mark.parametrize('bad', ['2027-01-01T00:00:00Z', 'not-a-timestamp', '2026-11-11'])
def test_bad_evaluation_is_hidden_without_erasing_prior_network_error(bad):
    local = evaluation()
    local['evaluated_at'] = bad
    projected = provider_state_at(row(details={'configuration_evaluation': local}), NOW)
    assert 'configuration_evaluation' not in projected
    assert 'configuration_evaluation' not in json.loads(projected['details_json'])
    assert projected['error_code'] == 'http_503'
    assert projected['last_attempt_at'] == FAILURE


@pytest.mark.parametrize('bad', ['2027-01-01', '2026-02-30', 'bad', '2027-01-01T00:00:00Z'])
def test_bad_documentary_date_cannot_announce_recent_429(bad):
    local = evaluation()
    local['documented_probe_at'] = bad
    projected = provider_state_at(row(details={'configuration_evaluation': local}), NOW)
    detail = projected['configuration_evaluation']
    assert detail['documented_probe_at'] is None
    assert detail['reason'] is None
    assert detail['clock_error'] == CLOCK_ERROR
    assert projected['error_code'] == 'http_503'


def test_date_precision_has_conservative_intraday_cutoff():
    assert documentary_clock('2026-10-29', '2026-10-29T23:00:00Z') == (None, True)
    assert documentary_clock('2026-10-29', '2026-10-30T03:00:00Z') == ('2026-10-29', False)
    assert documentary_clock(None, NOW) == (None, False)


@pytest.mark.parametrize('bad', ['2027-01-01T00:00:00Z', 'bad'])
def test_bad_legacy_attempt_never_leaks_documentary_error(bad):
    projected = provider_state_at(row('discovery_http_429', bad), NOW, SOURCE)
    assert projected['last_attempt_at'] is None
    assert projected['error_code'] == CLOCK_ERROR
    assert 'configuration_evaluation' not in projected


def test_missing_documentary_metadata_stays_unknown():
    projected = provider_state_at(row('discovery_http_429'), NOW)
    detail = projected['configuration_evaluation']
    assert detail['documented_probe_at'] is None
    assert detail['documented_check_at'] is None
    assert projected['last_attempt_at'] is None


def test_local_block_does_not_present_old_success_as_current_availability():
    stored = row(None, OLD, {'configuration_evaluation': evaluation()})
    stored['status'] = 'available'
    projected = provider_state_at(stored, NOW)
    assert projected['status'] == 'unavailable'
    assert projected['last_attempt_at'] == OLD
    assert projected['last_success_at'] == OLD


def test_worker_preserves_custom_documentary_dates(tmp_path):
    store = Store(tmp_path / 'synthetic.db')
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'news-sources.json').write_text(json.dumps({'sources': [SOURCE]}), encoding='utf-8')
    (config / 'verified-feeds.json').write_text(json.dumps({
        'checked_at': '2026-10-28', 'sources': [{
            'id': SOURCE['id'], 'status': 'discovery_http_429',
            'last_operational_probe_at': '2026-10-27',
        }],
    }), encoding='utf-8')
    loaded = Worker(store, config_dir=config).sources()[0]
    assert loaded['feed_checked_at'] == '2026-10-28'
    assert loaded['last_operational_probe_at'] == '2026-10-27'
    assert loaded['rss_urls'] == []


@pytest.mark.parametrize('raw', ['{invalid', 'null', '[]', None])
def test_malformed_details_projection_is_explicit_and_keeps_network_failure(raw):
    stored = row()
    stored['details_json'] = raw
    projected = provider_state_at(stored, NOW)
    assert projected['error_code'] == 'http_503'
    assert projected['last_attempt_at'] == FAILURE
    assert json.loads(projected['details_json'])['details_error'] == 'provider_state_details_invalid'


@pytest.mark.parametrize('raw', ['{invalid', 'null', '[]'])
def test_adapter_rejects_corrupt_details_without_overwriting_evidence(tmp_path, raw):
    store = Store(tmp_path / 'synthetic.db')
    stored = row()
    stored['details_json'] = raw
    with store.transaction() as con:
        con.execute('INSERT INTO provider_state VALUES(?,?,?,?,?,?,?)', tuple(stored.values()))
    with pytest.raises(FetchError, match='provider_state_details_invalid'):
        RSSAdapter(store, SOURCE, NoHTTP()).collect()
    assert state(store) == stored


def test_document_newer_than_legacy_evaluation_is_not_backdated():
    source = dict(SOURCE, last_operational_probe_at='2026-11-09', feed_checked_at='2026-11-09')
    projected = provider_state_at(row('discovery_http_429', OLD), NOW, source)
    detail = projected['configuration_evaluation']
    assert detail['documented_probe_at'] is None
    assert detail['documented_check_at'] is None
    assert detail['reason'] is None
    assert detail['clock_error'] == CLOCK_ERROR


def test_api_reads_documentary_dates_without_starting_worker(tmp_path, monkeypatch):
    from backend import api
    config = tmp_path / 'config'
    config.mkdir()
    source = dict(SOURCE, name='Synthetic Yahoo')
    (config / 'news-sources.json').write_text(json.dumps({'sources': [source]}), encoding='utf-8')
    (config / 'verified-feeds.json').write_text(json.dumps({
        'checked_at': '2026-10-29', 'sources': [{
            'id': source['id'], 'status': 'discovery_http_429',
            'last_operational_probe_at': '2026-10-29',
        }],
    }), encoding='utf-8')

    def forbidden(*args, **kwargs):
        raise AssertionError('API source projection must not start worker/HTTP/CLI')

    monkeypatch.setattr(api, 'ROOT', tmp_path)
    monkeypatch.setattr(api, 'iso', lambda: NOW)
    monkeypatch.setattr(api, 'ai_status', forbidden)
    monkeypatch.setattr(Worker, 'start', forbidden)
    monkeypatch.setattr(RSSAdapter, 'collect', forbidden)
    app = api.create_app(tmp_path / 'synthetic.db')
    stored = row('discovery_http_429')
    with app.state.store.transaction() as con:
        con.execute('INSERT INTO provider_state VALUES(?,?,?,?,?,?,?)', tuple(stored.values()))
    # Invoke the pure endpoint in-process: no listener, client, or live service.
    endpoint = next(route.endpoint for route in app.routes if route.path == '/api/v1/sources')
    response = endpoint()
    item = next(item for item in response['items'] if item['id'] == source['id'])
    assert item['last_attempt_at'] is None
    assert item['last_success_at'] == OLD
    assert item['error'] == 'rss_endpoint_not_verified'
    detail = item['details']['configuration_evaluation']
    assert detail['documented_probe_at'] == '2026-10-29'
    assert detail['documented_check_at'] == '2026-10-29'
    assert detail['network_attempted'] is False
    assert state(app.state.store) == stored
