from __future__ import annotations

import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from email.message import Message
from urllib.parse import urlencode, urlsplit

from .core import Store, digest, dumps
from .ingest import clean_text, finite_number, ingest_article, ingest_price, upsert_catalog
from .safe_http import FetchError, SafeHTTP
from .timeutil import SAO_PAULO, UTC, instant, iso, news_window, session_not_after


def source_hosts(source):
    hosts = set(source["allowed_hostnames"])
    return hosts | ({"www." + h for h in hosts} if source.get("www_alias_allowed") else set())


def discovery_section_allowed(url, source):
    """Conservative path/host rule; unknown sections are not silently included."""
    parts = urlsplit(url)
    if parts.hostname in {"finance.yahoo.com", "economia.uol.com.br"}:
        return True
    sections = {"economia", "economy", "economics", "business", "world", "mundo", "politica", "politics", "internacional", "money", "mercados", "markets", "empresas", "companies"}
    tokens = {p.casefold() for p in parts.path.split("/") if p}
    return bool(tokens & sections)


def feed_publication_time(raw_date):
    # UOL publishes Portuguese RFC-style names; map tokens, never guess an offset.
    names = {'seg': 'Mon', 'ter': 'Tue', 'qua': 'Wed', 'qui': 'Thu', 'sex': 'Fri',
             'sab': 'Sat', 'sáb': 'Sat', 'dom': 'Sun', 'fev': 'Feb', 'abr': 'Apr',
             'mai': 'May', 'ago': 'Aug', 'set': 'Sep', 'out': 'Oct', 'dez': 'Dec'}
    normalized = re.sub(r'\b[^\W\d_]{3}\b', lambda match: names.get(match[0].casefold(), match[0]), raw_date)
    parsed = parsedate_to_datetime(normalized)
    if parsed.tzinfo is None and normalized.rstrip().endswith('-0000'):
        # RFC -0000 denotes UTC, though Python deliberately returns a naive value.
        parsed = parsed.replace(tzinfo=UTC)
    return iso(parsed)


def parse_feed(body: bytes, source: dict, fetched_at=None, feed_url=None, content_type=None) -> list[dict]:
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise FetchError("xml_entities_disallowed")
    if content_type:
        header = Message()
        header['content-type'] = content_type
        charset = header.get_content_charset()
        if charset:
            if charset not in {'utf-8', 'utf8', 'iso-8859-1', 'latin-1', 'windows-1252', 'us-ascii'}:
                raise FetchError('unsupported_feed_charset')
            try:
                body = body.decode(charset, errors='strict')
            except UnicodeDecodeError:
                raise FetchError('invalid_feed_encoding') from None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise FetchError("invalid_feed_xml") from None
    fetched_at = iso(fetched_at)
    records = []
    local = lambda tag: tag.rsplit("}", 1)[-1]
    entries = [n for n in root.iter() if local(n.tag) in {"item", "entry"}]
    if local(root.tag) not in {"rss", "feed", "RDF"}:
        raise FetchError("response_not_rss_or_atom")
    for item in entries[:1000]:
        values = {local(n.tag): n for n in item}
        def value(*names):
            for name in names:
                n = values.get(name)
                if n is not None:
                    return "".join(n.itertext()).strip()
            return None
        links = [n for n in item if local(n.tag) == "link" and n.attrib.get("rel", "alternate") == "alternate"]
        url = ((links[0].get("href") or links[0].text) if links else None)
        if not url or urlsplit(url.strip()).hostname not in source_hosts(source):
            continue
        def normalized(value):
            return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))
        aliases = {"business": "economia", "economy": "economia", "economic policy": "economia", "money": "economia", "world": "mundo", "internacional": "mundo", "politics": "politica", "markets": "mercados", "companies": "empresas"}
        norm = lambda value: aliases.get(normalized(value), normalized(value))
        allowed = {norm(s) for s in source.get("section_allowlist", [])}
        categories = [norm("".join(n.itertext())) for n in item if local(n.tag) == "category"]
        verified_section = source.get("rss_sections", {}).get(feed_url)
        path_sections = [norm(s) for s in urlsplit(url.strip()).path.split("/") if s]
        if allowed and not (allowed.intersection(categories + path_sections) or (verified_section and norm(verified_section) in allowed)):
            continue
        published, precision, flags = None, "unknown", []
        raw_date = value("pubDate", "published", "date")
        if raw_date:
            try:
                published = feed_publication_time(raw_date)
                precision = "instant"
            except (ValueError, TypeError, OverflowError):
                try:
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
                        published = date.fromisoformat(raw_date).isoformat()
                        precision = "date"
                    else:
                        published = iso(raw_date)
                        precision = "instant"
                except (ValueError, TypeError):
                    flags.append("publication_date_unparseable")
        excerpt = clean_text(value("description", "summary"), 4000)
        records.append({"url": url.strip(), "title": clean_text(value("title")), "content": excerpt, "access_level": "excerpt" if excerpt else "headline_only", "published_at": published, "modified_at": None, "fetched_at": fetched_at, "timestamp_precision": precision, "quality_flags": flags + ["feed_content_only"]})
    return records


class RSSAdapter:
    def __init__(self, store: Store, source: dict, transport=None):
        self.store, self.source = store, source
        # RSS discovery hosts must be explicitly configured after a successful probe.
        hosts = source_hosts(source) | set(source.get("rss_allowed_hostnames", []))
        self.http = transport or SafeHTTP(store, hosts, source["id"])

    def collect(self, as_of=None):
        if not self.source.get("rss_urls"):
            reason = self.source.get("feed_status") if self.source.get("feed_status") not in {None, "discovery_and_probe_required"} else "rss_endpoint_not_verified"
            evaluation = {
                "evaluated_at": iso(), "reason": reason, "network_attempted": False,
                "documented_probe_at": self.source.get("last_operational_probe_at"),
                "documented_check_at": self.source.get("feed_checked_at"),
            }
            # This is a local configuration check, not a new HTTP observation.
            # Preserve earlier network outcomes and documentary date precision.
            with self.store.transaction() as con:
                row = con.execute("SELECT details_json FROM provider_state WHERE provider_id=?",
                                  (self.source["id"],)).fetchone()
                try:
                    details = json.loads(row["details_json"]) if row else {}
                except (ValueError, TypeError):
                    raise FetchError("provider_state_details_invalid") from None
                if not isinstance(details, dict):
                    raise FetchError("provider_state_details_invalid")
                details["configuration_evaluation"] = evaluation
                con.execute("""INSERT INTO provider_state
                    (provider_id,status,error_code,details_json) VALUES(?,?,?,?)
                    ON CONFLICT(provider_id) DO UPDATE SET details_json=excluded.details_json""",
                    (self.source["id"], "unavailable", "rss_endpoint_not_verified", dumps(details)))
            return {"status": "blocked", "reason": reason, "count": 0,
                    "configuration_evaluation": evaluation}
        counts = {"created": 0, "revised": 0, "duplicate": 0, "outside_window": 0, "future_at_cutoff": 0}
        failures = []
        successes = 0
        last_exception = None
        for url in self.source["rss_urls"]:
            try:
                response = self.http.get(url, conditional=True)
                if response.status == 304:
                    successes += 1
                    continue
                cutoff = as_of or iso()
                has_future_article = False
                for record in parse_feed(response.body, self.source, feed_url=url, content_type=response.headers.get('content-type')):
                    result = ingest_article(self.store, record, self.source, cutoff, "rss")
                    counts[result["status"]] += 1
                    if result["status"] == "outside_window":
                        published = record["published_at"]
                        future = (not session_not_after(published[:10], cutoff) if record["timestamp_precision"] == "date"
                                  else instant(published) > instant(cutoff))
                        if future:
                            has_future_article = True
                            counts["future_at_cutoff"] += 1
                # A subsequent 304 would hide the article while it enters the window.
                if not has_future_article and hasattr(self.http, "confirm_cache"):
                    self.http.confirm_cache(url, response)
                successes += 1
            except FetchError as exc:
                failures.append(exc.code)
                last_exception = exc
        status = "unavailable" if not successes else "partial" if failures else "available"
        next_attempt = iso(datetime.now(UTC) + timedelta(seconds=last_exception.retry_after or 60)) if last_exception and last_exception.retryable else None
        self.store.provider_status(self.source["id"], status, failures[0] if failures else None, {"counts": counts, "gaps": failures, "historical_coverage": "feed_window_only"}, next_attempt_at=next_attempt)
        if not successes and last_exception and last_exception.retryable:
            raise last_exception
        return {"status": "blocked" if not successes else "partial" if failures else "succeeded", **counts, "gaps": failures}


class BrapiAdapter:
    def __init__(self, store: Store, transport=None):
        self.store = store
        self.http = transport or SafeHTTP(store, {"brapi.dev"}, "brapi", limit=12000, period=datetime.now(UTC).strftime("%Y-%m"))

    def headers(self):
        token = os.environ.get("BRAPI_TOKEN")
        return {"Authorization": "Bearer " + token} if token else {}

    def catalog_page(self, page=1, limit=100):
        if not 1 <= page <= 10000 or not 1 <= limit <= 100:
            raise ValueError("invalid_catalog_pagination")
        payload = self.http.get("https://brapi.dev/api/v2/tickers?" + urlencode({"page": page, "limit": limit}), headers=self.headers()).json()
        records = payload.get("results")
        if not isinstance(records, list):
            raise FetchError("catalog_shape_invalid")
        page_info = payload.get("pagination", payload)
        has_next = page_info.get("hasNextPage")
        if has_next is None:
            has_next = len(records) == limit
        result = upsert_catalog(self.store, records, complete=not has_next and page == 1)
        return {**result, "has_next": bool(has_next), "page": page, "total_items": page_info.get("totalItems"), "records": records}

    def prices(self, asset: dict, as_of=None):
        ticker = asset["ticker"]
        if not self.headers() and ticker not in {"PETR4", "VALE3", "ITUB4", "MGLU3"}:
            raise FetchError("brapi_free_token_required")
        response = self.http.get(f"https://brapi.dev/api/quote/{ticker}?range=3mo&interval=1d", headers=self.headers())
        payload = response.json()
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            raise FetchError("quote_shape_invalid")
        quote = results[0]
        if not isinstance(quote, dict) or quote.get('symbol') != ticker:
            raise FetchError('quote_symbol_mismatch')
        if quote.get('historicalSymbol') not in (None, ticker):
            raise FetchError('historical_symbol_mismatch')
        if quote.get('usedInterval') not in (None, '1d'):
            raise FetchError('historical_interval_mismatch')
        fetched = iso()
        as_of = as_of or fetched
        from .session_continuity import calendar_context
        from .price_vintages import persist_vintage
        _,package=calendar_context(self.store,[],fetched)
        if package is not None:
            # Preserve the response before any per-bar deduplication. Current
            # quote metadata never replaces a documented daily period.
            receipt=persist_vintage(self.store,asset['asset_id'],response.body,fetched_at=fetched)
            count=len(quote.get('historicalDataPrice') or [])
            self.store.provider_status('brapi_prices','partial',None,
                {'asset_id':asset['asset_id'],'bars':count,'vintage_id':receipt['vintage_id'],
                 'policy':'brapi_v1_daily_ended_revisable_v1','subject_to_revision':True})
            return {'bars':count,'adjustment':'provider_adjusted_close_with_proventos','vintage_id':receipt['vintage_id']}
        count = 0
        for item in quote.get("historicalDataPrice") or []:
            try:
                dt = datetime.fromtimestamp(float(item["date"]), UTC)
                if dt > instant(as_of):
                    continue
                session = dt.astimezone(SAO_PAULO).date().isoformat()
                # Provider daily labels may be 00:00 UTC; preserve date label in that case.
                if dt.hour == dt.minute == dt.second == 0:
                    session = dt.date().isoformat()
                # A published market schedule cannot certify this provider's
                # OHLC finality. Persisting the whole response/vintage and the
                # documentary daily-snapshot policy is still an internal gap.
                ingest_price(self.store, asset["asset_id"], {**item, "session_date": session, "market_timestamp": iso(dt), "fetched_at": fetched, "adjustment": "unknown", "currency": quote.get("currency", "BRL"), "complete_session": False, "quality_flags": ["provider_adjustment_not_verified", "provider_finality_unverified", "daily_bar_timestamp_not_trade_time"]})
                count += 1
            except (ValueError, TypeError, KeyError):
                continue
        market_time = quote.get("regularMarketTime")
        if quote.get("regularMarketPrice") is not None and market_time:
            try:
                market = iso(datetime.fromtimestamp(market_time, UTC)) if isinstance(market_time, (int, float)) else iso(market_time)
                if instant(market) <= instant(as_of):
                    # Snapshot is kept as current day's raw/unknown series; not full session.
                    ingest_price(self.store, asset["asset_id"], {"close": quote["regularMarketPrice"], "volume": quote.get("regularMarketVolume"), "currency": quote.get("currency", "BRL"), "session_date": instant(market).astimezone(SAO_PAULO).date().isoformat(), "market_timestamp": market, "fetched_at": fetched, "adjustment": "unknown", "quality_flags": ["delayed_quote", "provider_adjustment_not_verified"], "complete_session": False})
                    count += 1
            except (ValueError, TypeError):
                pass
        self.store.provider_status("brapi_prices", "partial" if count else "unavailable", None if count else "no_timestamped_prices", {"asset_id": asset["asset_id"], "bars": count, "adjustment": "unknown", "delay_minutes_documented": 30})
        return {"bars": count, "adjustment": "unknown"}


class GDELTAdapter:
    MAX_RECORDS = 250
    def __init__(self, store: Store, source: dict, transport=None, min_split_minutes=60):
        self.store, self.source = store, source
        self.http = transport or SafeHTTP(store, {"api.gdeltproject.org"}, "gdelt_doc2", limit=500)
        self.minimum = timedelta(minutes=min_split_minutes)

    def partition(self, start, end, parent_id=None):
        start, end = iso(start), iso(end)
        pid = "part_" + digest([self.source["id"], start, end])[:24]
        with self.store.transaction() as con:
            con.execute("INSERT OR IGNORE INTO coverage_partitions(partition_id,provider,source_id,start_at,end_at,status,parent_id,updated_at) VALUES(?,?,?,?,?,'pending',?,?)", (pid, "gdelt_doc2", self.source["id"], start, end, parent_id, iso()))
        return pid

    def collect_partition(self, partition_id):
        rows = self.store.rows("SELECT * FROM coverage_partitions WHERE partition_id=? AND source_id=?", (partition_id, self.source["id"]))
        if not rows:
            raise ValueError("partition_not_found")
        part = rows[0]
        if part["status"] in {"complete", "split", "truncated"}:
            return {"status": part["status"], "count": part["count"], "children": []}
        start, end = instant(part["start_at"]), instant(part["end_at"])
        domains = " OR ".join("domainis:" + h for h in self.source["allowed_hostnames"])
        query = f"({domains})"
        params = {"query": query, "mode": "artlist", "format": "json", "maxrecords": 250, "sort": "datedesc", "startdatetime": start.strftime("%Y%m%d%H%M%S"), "enddatetime": end.strftime("%Y%m%d%H%M%S")}
        try:
            payload = self.http.get("https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode(params)).json()
            records = payload.get("articles", [])
            if not isinstance(records, list):
                raise FetchError("gdelt_shape_invalid")
            fetched = iso()
            accepted = 0
            for item in records:
                if urlsplit(item.get("url", "")).hostname not in source_hosts(self.source):
                    continue
                if not discovery_section_allowed(item["url"], self.source):
                    continue
                # GDELT seendate is not original publication. Preserve unknown publication.
                ingest_article(self.store, {"url": item["url"], "title": item.get("title", ""), "content": "", "published_at": None, "timestamp_precision": "unknown", "fetched_at": fetched, "access_level": "headline_only", "quality_flags": ["gdelt_discovery_only", "publication_time_unknown", "index_seen_at:" + str(item.get("seendate", "unknown"))]}, self.source, method="gdelt_doc2")
                accepted += 1
            children = []
            saturated = len(records) >= self.MAX_RECORDS
            if saturated and end - start >= self.minimum * 2:
                midpoint = start + (end - start) / 2
                children = [self.partition(start, midpoint, partition_id), self.partition(midpoint, end, partition_id)]
                status = "split"
            else:
                status = "truncated" if saturated else "complete"
            with self.store.transaction() as con:
                con.execute("UPDATE coverage_partitions SET status=?,count=?,truncated=?,query_json=?,error_code=NULL,updated_at=? WHERE partition_id=?", (status, accepted, int(status == "truncated"), dumps(params), iso(), partition_id))
            self.store.provider_status(self.source["id"] + ":gdelt", "partial", details={"partition": partition_id, "state": status, "count": accepted, "publication_dates": "unverified", "coverage": "discovery_index_only"})
            self.store.provider_status("gdelt_doc2", "partial", details={"partition": partition_id, "state": status, "coverage": "discovery_index_only"})
            return {"status": status, "count": accepted, "children": children}
        except FetchError as exc:
            self.store.provider_status("gdelt_doc2", "unavailable", exc.code)
            with self.store.transaction() as con:
                con.execute("UPDATE coverage_partitions SET status='failed',error_code=?,updated_at=? WHERE partition_id=?", (exc.code, iso(), partition_id))
            raise


class BCBAdapter:
    SERIES = {1178: "percent_per_year_effective_selic_252", 432: "percent_per_year_target_selic"}
    def __init__(self, store: Store, transport=None):
        self.store = store
        self.http = transport or SafeHTTP(store, {"api.bcb.gov.br"}, "bcb_sgs", limit=100)

    def collect(self, code=432, as_of=None):
        if code not in self.SERIES:
            raise ValueError("series_not_configured")
        start, end = news_window(as_of or iso())
        params = urlencode({"formato": "json", "dataInicial": instant(start).astimezone(SAO_PAULO).strftime("%d/%m/%Y"), "dataFinal": instant(end).astimezone(SAO_PAULO).strftime("%d/%m/%Y")})
        payload = self.http.get(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados?{params}").json()
        if not isinstance(payload, list):
            raise FetchError("bcb_shape_invalid")
        count = 0
        with self.store.transaction() as con:
            for item in payload:
                day = datetime.strptime(item["data"], "%d/%m/%Y").date()
                if not instant(start).astimezone(SAO_PAULO).date() <= day <= instant(end).astimezone(SAO_PAULO).date():
                    continue
                value = finite_number(item.get("valor"))
                h = digest([code, str(day), value])
                con.execute("INSERT OR IGNORE INTO macro_observations VALUES(?,?,?,?,?,?,?)", ("macro_" + h[:24], code, str(day), value, self.SERIES[code], iso(), h))
                count += 1
        self.store.provider_status("bcb_sgs", "available", details={"code": code, "unit": self.SERIES[code], "observations": count})
        return {"series": code, "observations": count}
