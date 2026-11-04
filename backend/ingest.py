from __future__ import annotations

import html
import json
import math
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .core import Store, digest, dumps
from .timeutil import instant, iso, publication_in_window, session_not_after

TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref_src", "guccounter"}


def canonical_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.port not in {None, 80, 443}:
        raise ValueError("invalid_article_url")
    host = p.hostname.lower().encode("idna").decode()
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    return urlunsplit((p.scheme.lower(), host, p.path or "/", urlencode(sorted(query)), ""))


def clean_text(text: str | None, limit=20000) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()[:limit]


def finite_number(value):
    if value is None or value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non_finite_number")
    return result


def upsert_catalog(store: Store, records: list[dict], provider="brapi", complete=False, fetched_at=None):
    fetched_at = iso(fetched_at)
    eligible = unknown = 0
    with store.transaction() as con:
        for raw in records:
            ticker = str(raw.get("symbol") or raw.get("stock") or raw.get("ticker") or "").strip().upper()
            if not re.fullmatch(r"[A-Z0-9.\-]{3,20}", ticker):
                continue
            kind = str(raw.get("assetType") or raw.get("type") or "unknown").lower()
            subtype = str(raw.get("subType") or "").lower()
            # No classification from numeric ticker suffix: funds can end with 11.
            asset_type = "unit" if kind in {"stock", "unit"} and subtype == "unit" else kind
            if asset_type not in {"stock", "unit", "fund", "etf", "bdr", "option", "crypto", "future"}:
                asset_type = "unknown"
            active = raw.get("isActive", True) is not False
            name = raw.get("longName") or raw.get("name") or ticker
            # Fractional market duplicates and temporary rights/receipts must not
            # inflate the ordinary equity universe. Preserve them visibly.
            instrument_uncertain = ticker.endswith("F") or bool(re.search(r"\b(TEMP|RECIBO|DIREITO)\b", name, re.I))
            if instrument_uncertain:
                asset_type = "unknown"
            is_eligible = asset_type in {"stock", "unit"} and active
            eligible += int(is_eligible)
            unknown += int(asset_type == "unknown")
            asset_id = f"{provider}:{ticker}"
            con.execute("INSERT INTO assets(asset_id,ticker,name,asset_type,eligible,sector,currency,provider,provider_id,classification_reason,active,updated_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET name=excluded.name,asset_type=excluded.asset_type,eligible=excluded.eligible,sector=excluded.sector,active=excluded.active,classification_reason=excluded.classification_reason,updated_at=excluded.updated_at,metadata_json=excluded.metadata_json", (asset_id, ticker, raw.get("longName") or raw.get("name") or ticker, asset_type, int(is_eligible), raw.get("sector"), raw.get("currency") or "BRL", provider, raw.get("id"), "provider_type" if asset_type != "unknown" else "provider_classification_missing", int(active), fetched_at, dumps({"identity_quality": "provider_symbol_not_historical_issuer_id", "subtype": subtype or None})))
            actual = con.execute("SELECT asset_id FROM assets WHERE ticker=?", (ticker,)).fetchone()[0]
            con.execute("INSERT OR IGNORE INTO asset_aliases VALUES(?,?,?)", (actual, ticker, provider))
        snap_id = "universe_" + digest({"provider": provider, "records": records, "fetched_at": fetched_at})[:24]
        con.execute("INSERT OR IGNORE INTO universe_snapshots VALUES(?,?,?,?,?,?,?,?)", (snap_id, provider, fetched_at, len(records), eligible, unknown, int(complete), dumps({"complete": complete, "coverage_scope": "provider_catalog"})))
    return {"received": len(records), "eligible": eligible, "unclassified": unknown, "complete": complete}


def ingest_article(store: Store, record: dict, source: dict, as_of=None, method="rss"):
    url = canonical_url(record["url"])
    hostname = urlsplit(url).hostname
    hosts = set(source["allowed_hostnames"])
    if source.get("www_alias_allowed"):
        hosts |= {"www." + h for h in hosts}
    if hostname not in hosts:
        raise ValueError("source_hostname_not_allowed")
    fetched = iso(record.get("fetched_at"))
    published = record.get("published_at")
    precision = record.get("timestamp_precision", "instant" if published else "unknown")
    if published and precision == "instant":
        published = iso(published)
    if as_of and published and not publication_in_window(published, precision, as_of):
        return {"status": "outside_window"}
    title = clean_text(record.get("title"), 1000)
    if not title:
        raise ValueError("article_title_required")
    content = clean_text(record.get("content"), 20000)
    access = record.get("access_level", "excerpt" if content else "headline_only")
    if access not in {"headline_only", "excerpt", "full_text"}:
        raise ValueError("invalid_article_access")
    if access == "headline_only":
        content = title
    flags = sorted(set(record.get("quality_flags", []) + (["publication_time_unknown"] if not published else [])))
    if len(clean_text(record.get("content"), 20001)) > 20000:
        flags.append("content_truncated_to_local_limit")
        if access == "full_text":
            access = "excerpt"
    modified = iso(record["modified_at"]) if record.get("modified_at") else None
    # Timestamps conveying revision semantics are part of revision identity; fetch clock is not.
    h = digest({"title": title, "content": content, "published": published, "modified": modified, "access": access, "flags": flags, "precision": precision})
    article_id = "article_" + digest(url)[:24]
    rev_id = "article_revision_" + digest(article_id + h)[:24]
    title_key = re.sub(r"[^\w]+", " ", title.casefold()).strip()
    # Exact title/day candidates only; clustering is never claimed to prove a shared event.
    cluster = digest({"title": title_key, "date": published[:10] if published else fetched[:10]})
    with store.transaction() as con:
        old = con.execute("SELECT * FROM articles WHERE article_id=?", (article_id,)).fetchone()
        con.execute("INSERT OR IGNORE INTO articles VALUES(?,?,?,?,NULL,?)", (article_id, url, source["id"], fetched, cluster))
        inserted = con.execute("INSERT OR IGNORE INTO article_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rev_id, article_id, h, title, content, access, published, modified, fetched, fetched, precision, source.get("publisher_group"), record.get("original_publisher"), dumps(flags))).rowcount
        if inserted:
            con.execute("UPDATE articles SET latest_revision_id=? WHERE article_id=?", (rev_id, article_id))
        con.execute("INSERT OR IGNORE INTO article_discoveries VALUES(?,?,?)", (article_id, method, fetched))
    if inserted:
        build_event(store, article_id)
    return {"status": "revised" if old and inserted else "created" if inserted else "duplicate", "article_id": article_id, "revision_id": rev_id}


def ingest_price(store: Store, asset_id: str, bar: dict, provider="brapi"):
    fetched = iso(bar.get("fetched_at"))
    session = str(bar["session_date"])
    if not session_not_after(session, fetched):
        raise ValueError("future_session_date")
    adjustment = bar.get("adjustment", "unknown")
    if adjustment not in {"raw", "split_adjusted", "total_return", "unknown"}:
        raise ValueError("invalid_adjustment")
    values = {key: finite_number(bar.get(key)) for key in ("close", "open", "high", "low", "volume")}
    if any(value is not None and value < 0 for value in values.values()):
        raise ValueError("negative_price_or_volume")
    market = iso(bar["market_timestamp"]) if bar.get("market_timestamp") else None
    if market and instant(market) > instant(fetched):
        raise ValueError("future_market_timestamp")
    currency = bar.get("currency") or "BRL"
    flags = sorted(set(bar.get("quality_flags", []) + (["adjustment_unknown"] if adjustment == "unknown" else []) + (["market_time_unknown"] if not market else [])))
    h = digest({**values, "currency": currency, "market": market, "complete": bool(bar.get("complete_session")), "flags": flags})
    bar_id = "bar_" + digest([asset_id, provider, session, adjustment])[:24]
    rev_id = "price_revision_" + digest(bar_id + h)[:24]
    with store.transaction() as con:
        con.execute("INSERT OR IGNORE INTO price_bars VALUES(?,?,?,?,?,NULL)", (bar_id, asset_id, provider, session, adjustment))
        inserted = con.execute("INSERT OR IGNORE INTO price_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rev_id, bar_id, h, values["close"], values["open"], values["high"], values["low"], values["volume"], currency, market, fetched, fetched, int(bool(bar.get("complete_session"))), dumps(flags))).rowcount
        if inserted:
            con.execute("UPDATE price_bars SET latest_revision_id=? WHERE bar_id=?", (rev_id, bar_id))
    return {"status": "created" if inserted else "duplicate", "bar_id": bar_id, "revision_id": rev_id}


def build_event(store: Store, article_id: str):
    rows = store.rows("SELECT a.*,r.* FROM articles a JOIN article_revisions r ON r.revision_id=a.latest_revision_id WHERE a.article_id=?", (article_id,))
    article = rows[0]
    siblings = store.rows("SELECT a.*,r.* FROM articles a JOIN article_revisions r ON r.revision_id=a.latest_revision_id WHERE a.cluster_key=? ORDER BY a.article_id", (article["cluster_key"],))
    evidence_ids = [a["revision_id"] for a in siblings]
    text = " ".join(a["title"] + " " + a["content"] for a in siblings)
    links = []
    for asset in store.rows("SELECT asset_id,ticker FROM assets WHERE eligible=1"):
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(asset["ticker"]) + r"(?![A-Za-z0-9])", text, re.I):
            supporting = [a["revision_id"] for a in siblings if re.search(r"(?<![A-Za-z0-9])" + re.escape(asset["ticker"]) + r"(?![A-Za-z0-9])", a["title"] + " " + a["content"], re.I)]
            links.append({"entity_id": asset["asset_id"], "link_type": "candidate", "mechanism": "Ticker explicitamente mencionado; exposição econômica ainda não verificada.", "evidence_ids": supporting})
    event_id = "event_" + article["cluster_key"][:24]
    with store.transaction() as con:
        previous = con.execute("SELECT latest_version FROM events WHERE event_id=?", (event_id,)).fetchone()
        version = previous[0] + 1 if previous else 1
        body = {"schema_version": "1.0", "event_id": event_id, "event_version": version, "title": article["title"], "event_type": "other", "event_date": None, "first_disclosed_at": None, "first_seen_at": min(a["first_seen_at"] for a in siblings), "timestamp_precision": "unknown", "asset_ids": [x["entity_id"] for x in links], "sector_ids": [], "evidence_ids": evidence_ids, "links": links, "novelty": "unknown", "supersedes_event_version": previous[0] if previous else None, "quality_flags": ["deterministic_candidate_group", "economic_link_unverified", "first_disclosure_unknown"]}
        content_hash = digest({k: v for k, v in body.items() if k not in {"event_version", "supersedes_event_version"}})
        if con.execute("SELECT 1 FROM event_versions WHERE event_id=? AND content_hash=?", (event_id, content_hash)).fetchone():
            return event_id
        con.execute("INSERT INTO events VALUES(?,?,?) ON CONFLICT(event_id) DO UPDATE SET latest_version=excluded.latest_version", (event_id, article["cluster_key"], version))
        con.execute("INSERT INTO event_versions VALUES(?,?,?,?,?)", (event_id, version, dumps(body), content_hash, iso()))
        con.execute("DELETE FROM event_asset_links WHERE event_id=?", (event_id,))
        for link in links:
            con.execute("INSERT OR REPLACE INTO event_asset_links VALUES(?,?,?,?)", (event_id, link["entity_id"], link["link_type"], link["mechanism"]))
    return event_id
