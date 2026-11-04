import pytest
import gzip
import zlib

from backend.adapters import RSSAdapter, parse_feed, feed_publication_time
from backend.core import Store, QuotaExceeded
from backend.safe_http import FetchError, SafeHTTP, Response, retry_delay, decode_body
from backend.worker import Worker

SOURCE = {'id': 'br_uol', 'allowed_hostnames': ['economia.uol.com.br'],
          'publisher_group': 'uol', 'section_allowlist': ['Economia'],
          'rss_urls': ['https://rss.uol.com.br/feed/economia.xml'],
          'rss_sections': {'https://rss.uol.com.br/feed/economia.xml': 'Economia'}}
NOW = '2026-10-29T23:00:00Z'
XML = '''<rss><channel><item><title>Ações PETR4</title>
<link>https://economia.uol.com.br/noticias/acoes.htm</link>
<pubDate>Thu, 29 Oct 2026 12:00:00 GMT</pubDate>
<description>Informação pública</description></item></channel></rss>'''


def test_latin1_feed_ingests_and_deduplicates(tmp_path):
    store = Store(tmp_path/'qa.db')
    class HTTP:
        def get(self, *a, **kw):
            return Response(200, XML.encode('iso-8859-1'), {'content-type': 'text/xml;charset=ISO-8859-1'})
    adapter = RSSAdapter(store, SOURCE, HTTP())
    assert adapter.collect(NOW)['created'] == 1
    assert adapter.collect(NOW)['duplicate'] == 1
    assert store.list_articles()[0]['title'] == 'Ações PETR4'


def test_feed_encoding_does_not_relax_xml_or_host_policy():
    with pytest.raises(FetchError, match='xml_entities_disallowed'):
        parse_feed(b'<!DOCTYPE rss><rss/>', SOURCE, content_type='text/xml;charset=ISO-8859-1')
    with pytest.raises(FetchError, match='invalid_feed_encoding'):
        parse_feed(XML.encode('latin-1'), SOURCE, content_type='text/xml;charset=UTF-8')
    assert parse_feed(XML.replace('economia.uol.com.br', 'evil.test').encode(), SOURCE,
                      feed_url=SOURCE['rss_urls'][0]) == []


@pytest.mark.parametrize('raw,expected', [
    ('Thu, 29 Oct 2026 21:27:42 -0000', '2026-10-29T21:27:42.000000Z'),
    ('Qui, 29 Out 2026 20:09:08 -0200', '2026-10-29T22:09:08.000000Z'),
    ('Sáb, 24 Out 2026 10:00:00 -0300', '2026-10-24T13:00:00.000000Z'),
])
def test_feed_dates_keep_explicit_offset(raw, expected):
    assert feed_publication_time(raw) == expected


def test_date_without_timezone_stays_unknown():
    with pytest.raises(ValueError):
        feed_publication_time('Thu, 29 Oct 2026 21:27:42')


@pytest.mark.parametrize('published,first,second,precision', [
    ('2026-10-29T20:55:00-03:00', '2026-10-29T23:50:00Z', '2026-10-29T23:55:00Z', 'instant'),
    ('2026-10-30', '2026-10-30T02:50:00Z', '2026-10-30T03:00:00Z', 'date'),
])
def test_future_article_keeps_old_validators_until_it_can_be_ingested(tmp_path, published, first, second, precision):
    store = Store(tmp_path/'feed.sqlite3')
    url = SOURCE['rss_urls'][0]
    class Feed(SafeHTTP):
        def get(self, target, conditional=False):
            assert target == url and conditional
            cached = store.rows('SELECT etag,last_modified FROM http_cache')[0]
            if cached['etag'] == '"new"':
                return Response(304, b'', {})
            return Response(200, XML.replace('Thu, 29 Oct 2026 12:00:00 GMT', published).encode(),
                            {'etag':'"new"', 'last-modified':'new-http-value'})
    feed = Feed(store, {'rss.uol.com.br'}, SOURCE['id'])
    feed.confirm_cache(url, Response(200, b'', {'etag':'"old"', 'last-modified':'original-http-value'}))
    adapter = RSSAdapter(store, SOURCE, feed)
    previous = store.rows('SELECT * FROM http_cache')
    skipped = adapter.collect(first)
    assert skipped['future_at_cutoff'] == skipped['outside_window'] == 1
    assert not store.rows('SELECT article_id FROM articles')
    assert store.rows('SELECT * FROM http_cache') == previous
    assert adapter.collect(second)['created'] == 1
    assert store.rows('SELECT timestamp_precision FROM article_revisions') == [{'timestamp_precision':precision}]
    assert store.rows('SELECT etag,last_modified FROM http_cache') == [{'etag':'"new"','last_modified':'new-http-value'}]
    assert adapter.collect(second)['created'] == 0
    assert len(store.rows('SELECT article_id FROM article_revisions')) == 1


def test_only_expired_feed_entries_can_still_confirm_the_validator(tmp_path):
    store = Store(tmp_path/'feed.sqlite3')
    class Feed(SafeHTTP):
        def get(self, url, conditional=False):
            return Response(200, XML.replace('Thu, 29 Oct 2026 12:00:00 GMT', '2026-06-03').encode(), {'etag':'"expired"'})
    received = RSSAdapter(store, SOURCE, Feed(store, {'rss.uol.com.br'}, SOURCE['id'])).collect(NOW)
    assert received['outside_window'] == 1 and received['future_at_cutoff'] == 0
    assert store.rows('SELECT etag FROM http_cache') == [{'etag':'"expired"'}]


@pytest.mark.parametrize('failure', [FetchError('http_429', True, 180), QuotaExceeded('local_request_budget_exhausted')])
def test_news_failure_does_not_starve_next_source(tmp_path, monkeypatch, failure):
    store = Store(tmp_path/'qa.db')
    worker = Worker(store)
    monkeypatch.setattr(worker, 'sources', lambda: [{'id': 'first', 'enabled': True}, {'id': 'second', 'enabled': True}])
    monkeypatch.setattr(worker, 'check_cancelled', lambda job: None)
    monkeypatch.setattr(store, 'checkpoint', lambda *a: None)
    calls = []
    def collect(self, as_of):
        calls.append(self.source['id'])
        if self.source['id'] == 'first': raise failure
        return {'status': 'succeeded', 'created': 1}
    monkeypatch.setattr(RSSAdapter, '__init__', lambda self, store, source: setattr(self, 'source', source))
    monkeypatch.setattr(RSSAdapter, 'collect', collect)
    result = worker.execute({'kind': 'news', 'payload': {'as_of': NOW}, 'job_id': 'qa'})
    assert calls == ['first', 'second']
    assert result['status'] == 'partial' and result['gaps'] == ['first']
    assert not store.rows('SELECT * FROM worker_lease')


def test_backoff_survives_new_store_and_prevents_dns_and_quota(tmp_path, monkeypatch):
    store = Store(tmp_path/'qa.db')
    first = SafeHTTP(store, {'example.org'}, 'first')
    assert first.defer_host('example.org', '172800') == 172800
    assert retry_delay('172800') == 172800
    second = SafeHTTP(Store(tmp_path/'qa.db'), {'example.org'}, 'second')
    monkeypatch.setattr('backend.safe_http.public_addresses', lambda host: pytest.fail('DNS before backoff'))
    with pytest.raises(FetchError, match='http_backoff_active') as caught:
        second.get('https://example.org/feed')
    assert caught.value.retry_after > 172790
    assert not store.rows('SELECT * FROM quota_ledger')


def test_macro_schedule_is_daily_coalesced_without_starting_worker(tmp_path, monkeypatch):
    store = Store(tmp_path/'qa.db')
    monkeypatch.setattr('backend.worker.schedule_analyses', lambda *args: [])
    worker = Worker(store)
    worker.schedule()
    worker.schedule()
    assert len(store.rows("SELECT * FROM jobs WHERE kind='macro'")) == 1
    assert not store.rows("SELECT * FROM jobs WHERE kind='prices'")
    assert not store.rows('SELECT * FROM worker_lease')


@pytest.mark.parametrize('compress,encoding', [(gzip.compress, 'gzip'), (zlib.compress, 'deflate')])
def test_compressed_feeds_are_bounded_and_complete(compress, encoding):
    body = XML.encode()
    assert decode_body(compress(body), encoding, len(body)) == body
    with pytest.raises(FetchError, match='response_size_limit'):
        decode_body(compress(b'A' * 10000), encoding, 100)
    with pytest.raises(FetchError, match='invalid_compressed_response'):
        decode_body(compress(body)[:-3], encoding, 5000)
    with pytest.raises(FetchError, match='invalid_compressed_response'):
        decode_body(compress(body) + b'trailing', encoding, 5000)


@pytest.mark.parametrize('code', [429, 503])
def test_http_response_persists_retry_after_before_next_request(tmp_path, monkeypatch, code):
    store = Store(tmp_path/'qa.db')
    class RemoteResponse:
        status = code
        def getheaders(self): return [('Retry-After', '900')]
    class Connection:
        def __init__(self, *a, **kw): self._context = self
        def wrap_socket(self, *a, **kw): return object()
        def request(self, *a, **kw): pass
        def getresponse(self): return RemoteResponse()
        def close(self): pass
    monkeypatch.setattr('backend.safe_http.public_addresses', lambda host: ['93.184.216.34'])
    monkeypatch.setattr('backend.safe_http.socket.create_connection', lambda *a, **kw: object())
    monkeypatch.setattr('backend.safe_http.http.client.HTTPSConnection', Connection)
    http = SafeHTTP(store, {'example.org'}, 'provider')
    with pytest.raises(FetchError, match=f'http_{code}') as caught:
        http.get('https://example.org/feed')
    assert caught.value.retry_after == 900
    with pytest.raises(FetchError, match='http_backoff_active'):
        SafeHTTP(Store(tmp_path/'qa.db'), {'example.org'}, 'other-provider').get('https://example.org/other')
    rows = store.rows('SELECT * FROM quota_ledger')
    assert len(rows) == 1 and rows[0]['status'] == 'consumed'


def test_compressed_304_skips_body_and_feed_parser(tmp_path, monkeypatch):
    store = Store(tmp_path/'qa.db')
    class CachedResponse:
        status = 304
        def getheaders(self): return [('Content-Encoding', 'gzip'), ('Content-Length', '999999999')]
        def read(self, size): pytest.fail('304 must not read a body')
    class Connection:
        def __init__(self, *a, **kw): self._context = self
        def wrap_socket(self, *a, **kw): return object()
        def request(self, *a, **kw): pass
        def getresponse(self): return CachedResponse()
        def close(self): pass
    monkeypatch.setattr('backend.safe_http.public_addresses', lambda host: ['93.184.216.34'])
    monkeypatch.setattr('backend.safe_http.socket.create_connection', lambda *a, **kw: object())
    monkeypatch.setattr('backend.safe_http.http.client.HTTPSConnection', Connection)
    monkeypatch.setattr('backend.adapters.parse_feed', lambda *a, **kw: pytest.fail('304 must not parse'))
    result = RSSAdapter(store, SOURCE, SafeHTTP(store, {'rss.uol.com.br'}, SOURCE['id'])).collect(NOW)
    assert result['status'] == 'succeeded' and result['created'] == 0
    assert not store.rows('SELECT * FROM article_revisions')
    assert not store.rows('SELECT * FROM http_cache')
