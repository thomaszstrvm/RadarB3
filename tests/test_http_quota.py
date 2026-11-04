"""Transport failures exercise the real ledger with entirely fake I/O."""
import http.client

import pytest

from backend.core import QuotaExceeded, Store
from backend.safe_http import FetchError, SafeHTTP


@pytest.mark.parametrize('phase,expected', [
    ('connect', 'released'), ('tls', 'released'),
    ('request', 'uncertain'), ('response', 'uncertain'),
    ('read', 'consumed'), ('success', 'consumed'),
])
@pytest.mark.parametrize('failure_type', [OSError, http.client.HTTPException])
def test_request_boundary_preserves_conservative_quota(tmp_path, monkeypatch, phase, expected, failure_type):
    store = Store(tmp_path / 'http-quota.sqlite3', 'demo')
    observed = []

    def fail_at(point):
        observed.append(point)
        if phase == point:
            raise failure_type('synthetic transport failure')

    class Reply:
        status = 200

        def getheaders(self):
            return []

        def read(self, size):
            fail_at('read')
            return b''

    class Connection:
        def __init__(self, *args, **kwargs):
            self._context = self

        def wrap_socket(self, *args, **kwargs):
            fail_at('tls')
            return object()

        def request(self, *args, **kwargs):
            # request() may already have written headers when it raises.
            fail_at('request')

        def getresponse(self):
            fail_at('response')
            return Reply()

        def close(self):
            observed.append('close')

    def connect(*args, **kwargs):
        fail_at('connect')
        return object()

    monkeypatch.setattr('backend.safe_http.public_addresses', lambda host: ['93.184.216.34'])
    monkeypatch.setattr('backend.safe_http.socket.create_connection', connect)
    monkeypatch.setattr('backend.safe_http.http.client.HTTPSConnection', Connection)
    client = SafeHTTP(store, {'example.org'}, 'fixture', limit=1, period='fixture-period', min_interval=0)
    if phase == 'success':
        assert client.get('https://example.org/feed').status == 200
    else:
        with pytest.raises(FetchError, match='network_or_tls_failure'):
            client.get('https://example.org/feed')
    ledger = store.rows('SELECT * FROM quota_ledger')
    assert len(ledger) == 1 and ledger[0]['status'] == expected
    assert observed[-1] == 'close'
    if expected == 'released':
        assert 'request' not in observed
        store.reserve_quota('fixture', 'fixture-period', 1)
    else:
        # An uncertain send still uses the budget: retry cannot obtain a new unit.
        with pytest.raises(QuotaExceeded):
            store.reserve_quota('fixture', 'fixture-period', 1)
