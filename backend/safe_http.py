"""Exact host policy; public-address DNS pinning; TLS SNI; no proxy/redirects."""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import time
import zlib
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .core import Store, digest
from .timeutil import UTC, iso


class FetchError(RuntimeError):
    def __init__(self, code: str, retryable=False, retry_after=None, status=None):
        super().__init__(code)
        self.code, self.retryable, self.retry_after, self.status = code, retryable, retry_after, status


def validate_url(url: str, allowed_hosts: set[str]) -> tuple[str, str]:
    try:
        parts = urlsplit(url)
        hostname = (parts.hostname or "").lower().encode("idna").decode()
        if parts.scheme != "https" or not hostname or parts.username or parts.password or parts.port not in {None, 443} or parts.fragment:
            raise ValueError()
        if hostname not in allowed_hosts or any(c in url for c in ("\r", "\n", "\\")):
            raise ValueError()
        return hostname, (parts.path or "/") + ("?" + parts.query if parts.query else "")
    except (ValueError, UnicodeError):
        raise FetchError("url_not_allowed") from None


def public_addresses(host: str, resolver=socket.getaddrinfo) -> list[str]:
    try:
        addresses = sorted({item[4][0] for item in resolver(host, 443, type=socket.SOCK_STREAM)})
    except OSError:
        raise FetchError("dns_failed", retryable=True) from None
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise FetchError("non_public_address_blocked")
    return addresses


def retry_delay(value: str | None):
    if not value:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value).astimezone(UTC) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError):
            return None


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict
    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeDecodeError):
            raise FetchError("invalid_json") from None


def decode_body(body, encoding, max_bytes):
    if encoding in {'identity', ''}:
        return body
    if encoding not in {'gzip', 'deflate'}:
        raise FetchError('unexpected_content_encoding')
    try:
        decoder = zlib.decompressobj(31 if encoding == 'gzip' else 15)
        decoded = decoder.decompress(body, max_bytes + 1)
        if len(decoded) > max_bytes or decoder.unconsumed_tail:
            raise FetchError('response_size_limit')
        if not decoder.eof or decoder.unused_data:
            raise FetchError('invalid_compressed_response')
        return decoded
    except zlib.error:
        raise FetchError('invalid_compressed_response') from None


class SafeHTTP:
    def __init__(self, store: Store, allowed_hosts: set[str], provider: str, limit=500, period=None, timeout=30, max_bytes=5_000_000, min_interval=6):
        self.store, self.allowed_hosts, self.provider = store, allowed_hosts, provider
        self.limit, self.period = limit, period
        self.timeout, self.max_bytes, self.min_interval = timeout, max_bytes, min_interval

    def get(self, url: str, headers=None, conditional=False) -> Response:
        host, path = validate_url(url, self.allowed_hosts)
        blocked = self.store.rows('SELECT next_attempt_at FROM http_backoff WHERE host=?', (host,))
        if blocked and blocked[0]['next_attempt_at'] > time.time():
            raise FetchError('http_backoff_active', True, blocked[0]['next_attempt_at'] - time.time())
        addresses = public_addresses(host)
        # Persist delay slots so process restarts do not reset the provider throttle.
        with self.store.transaction() as con:
            row = con.execute("SELECT last_requested_at FROM host_requests WHERE host=?", (host,)).fetchone()
            due = max(time.time(), (row[0] + self.min_interval) if row else 0)
            con.execute("INSERT INTO host_requests VALUES(?,?) ON CONFLICT(host) DO UPDATE SET last_requested_at=excluded.last_requested_at", (host, due))
        wait = due - time.time()
        if wait > 0:
            time.sleep(wait)
        period = self.period or datetime.now(UTC).strftime("%Y-%m-%d")
        reservation = self.store.reserve_quota(self.provider, period, self.limit)
        request_headers = {"User-Agent": "RadarB3Local/1.0 (personal research)", "Accept-Encoding": "identity", "Connection": "close"}
        request_headers.update(headers or {})
        key = digest(url)
        if conditional:
            cached = self.store.rows("SELECT * FROM http_cache WHERE url_hash=?", (key,))
            if cached:
                if cached[0]["etag"]:
                    request_headers["If-None-Match"] = cached[0]["etag"]
                if cached[0]["last_modified"]:
                    request_headers["If-Modified-Since"] = cached[0]["last_modified"]
        connection = http.client.HTTPSConnection(host, timeout=self.timeout, context=ssl.create_default_context())
        request_started = False
        try:
            # Connect to the already validated numeric IP, but retain hostname for
            # TLS hostname verification and SNI. No second DNS lookup/rebinding.
            raw_socket = socket.create_connection((addresses[0], 443), timeout=self.timeout)
            connection.sock = connection._context.wrap_socket(raw_socket, server_hostname=host)
            # request() can transmit headers/body before raising. From this
            # boundary onward we cannot safely return the reserved budget.
            request_started = True
            connection.request("GET", path, headers=request_headers)
            response = connection.getresponse()
            h = {k.lower(): v for k, v in response.getheaders()}
            self.store.settle_quota(reservation, "consumed")
            if 300 <= response.status < 400 and response.status != 304:
                raise FetchError("redirect_blocked", status=response.status)
            if response.status == 429:
                delay = self.defer_host(host, h.get('retry-after'))
                raise FetchError("http_429", True, delay, 429)
            if response.status >= 400:
                delay = self.defer_host(host, h.get('retry-after')) if response.status >= 500 else None
                raise FetchError(f"http_{response.status}", response.status >= 500, delay, status=response.status)
            if response.status == 304:
                # Representation headers may describe gzip/size of the cached
                # resource; a 304 itself has no body to read or decompress.
                return Response(304, b'', h)
            encoding = h.get('content-encoding', 'identity').strip().lower()
            if encoding not in {"identity", "", "gzip", "deflate"}:
                raise FetchError("unexpected_content_encoding")
            try:
                if int(h.get("content-length", 0)) > self.max_bytes:
                    raise FetchError("response_size_limit")
            except ValueError:
                raise FetchError("invalid_content_length") from None
            pieces, size = [], 0
            deadline = time.monotonic() + self.timeout
            while True:
                if time.monotonic() > deadline:
                    raise FetchError("response_deadline_exceeded", True)
                block = response.read(min(65536, self.max_bytes - size + 1))
                if not block:
                    break
                size += len(block)
                if size > self.max_bytes:
                    raise FetchError("response_size_limit")
                pieces.append(block)
            return Response(response.status, decode_body(b"".join(pieces), encoding, self.max_bytes), h)
        except FetchError:
            raise
        except (OSError, http.client.HTTPException):
            self.store.settle_quota(reservation, "uncertain" if request_started else "released")
            raise FetchError("network_or_tls_failure", retryable=True) from None
        finally:
            connection.close()

    def defer_host(self, host, retry_after):
        delay = max(60, retry_delay(retry_after) or 60)
        with self.store.transaction() as con:
            con.execute('INSERT INTO http_backoff VALUES(?,?) ON CONFLICT(host) DO UPDATE SET next_attempt_at=MAX(http_backoff.next_attempt_at,excluded.next_attempt_at)', (host, time.time() + delay))
        return delay

    def confirm_cache(self, url, response):
        """Only call after successful parsing AND durable ingestion."""
        if response.status == 200:
            with self.store.transaction() as con:
                con.execute("INSERT INTO http_cache VALUES(?,?,?,?) ON CONFLICT(url_hash) DO UPDATE SET etag=excluded.etag,last_modified=excluded.last_modified,updated_at=excluded.updated_at", (digest(url), response.headers.get("etag"), response.headers.get("last-modified"), iso()))
