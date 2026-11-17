"""Defensive audit regressions; synthetic data only, no worker, collection or inference."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.adapters import parse_feed
from backend.api import create_app
from backend.safe_http import FetchError

SOURCE = {'id': 's', 'name': 'S', 'allowed_hostnames': ['example.invalid'], 'section_allowlist': []}
FEED = ('<?xml version="1.0"{enc}?>{dtd}<rss><channel><item><title>{title}</title>'
        '<link>https://example.invalid/economia/a</link><pubDate>Tue, 03 Nov 2026 10:00:00 -0300</pubDate>'
        '<description>d</description></item></channel></rss>')
DTD = '<!DOCTYPE rss [<!ENTITY x "EXPANDED">]>'


def test_plain_utf8_feed_is_still_accepted():
    assert [r['title'] for r in parse_feed(FEED.format(enc='', dtd='', title='Title').encode(), SOURCE)] == ['Title']


@pytest.mark.parametrize('codec', ['utf-16', 'utf-16-le', 'utf-16-be', 'utf-32'])
@pytest.mark.parametrize('content_type', [None, 'application/rss+xml'])
def test_non_ascii_compatible_feed_cannot_hide_a_doctype(codec, content_type):
    body = FEED.format(enc=' encoding="utf-16"', dtd=DTD, title='&x;').encode(codec)
    with pytest.raises(FetchError):
        parse_feed(body, SOURCE, content_type=content_type)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / 'radar.sqlite3', 'demo', 'collection-only')
    with TestClient(app, base_url='http://127.0.0.1:8765') as test_client:
        test_client.headers['x-radar-token'] = test_client.get('/api/v1/session').json()['token']
        yield test_client


def test_command_without_declared_length_is_rejected_before_parsing(client):
    body = json.dumps({'name': 'n', 'padding': 'a' * 300_000}).encode()
    chunked = client.post('/api/v1/alert-rules', content=(part for part in [body]), headers={'content-type': 'application/json'})
    assert chunked.status_code == 411
    declared = client.post('/api/v1/alert-rules', content=body, headers={'content-type': 'application/json'})
    assert declared.status_code == 413


def test_small_declared_command_still_works(client):
    assert client.post('/api/v1/alert-rules', json={'name': 'n'}).status_code == 200
    assert client.delete('/api/v1/watchlist/unknown').status_code == 200
