"""One reviewed earnings release. Offline import; no directional readiness.

The pinned mapping approves the transcription, not just the PDF bytes. Decimal
coefficients retain the source's million-USD scale; no implicit conversions.
"""
from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from .core import digest, dumps
from .timeutil import SAO_PAULO, instant, iso, publication_in_window


MAPPING_ID = "vale3-earnings-release-3t26-v1"
MAPPING_SHA256 = "4dfbbffc6cbf6779c5a95ec1f7ef164599f42114107925794cca38f09da64cc4"
SOURCE_ID = "vale_ri_reviewed_fundamentals_v1"
RELEASE_ID = "fundamentals_" + MAPPING_SHA256[:24]
MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 32 * 1024
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas/fundamentals-v1.schema.json"


def validate_manifest(document):
    """Validate types/meaning and require the exact independently reviewed map."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(document):
        raise ValueError("fundamentals_schema_invalid")
    if len(dumps(document).encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ValueError("fundamentals_manifest_too_large")
    published = date.fromisoformat(document["publication"]["date"])
    fetched = instant(document["source"]["fetched_at"])
    if published > fetched.astimezone(SAO_PAULO).date():
        raise ValueError("fundamentals_publication_after_fetch")
    seen = set()
    for item in document["observations"]:
        period = item["period"]
        key = (item["account"], period["kind"])
        if key in seen:
            raise ValueError("fundamentals_duplicate_observation")
        seen.add(key)
        if (item["value"] is None) != (item["missing_reason"] is not None):
            raise ValueError("fundamentals_null_requires_reason")
        if item["value"] is not None and not Decimal(item["value"]).is_finite():
            raise ValueError("fundamentals_nonfinite_value")
        if period["kind"] == "instant":
            if period["start"] is not None or period["end"] is not None or period["at"] is None:
                raise ValueError("fundamentals_invalid_instant_period")
            end = date.fromisoformat(period["at"])
        else:
            if period["at"] is not None or period["start"] is None or period["end"] is None:
                raise ValueError("fundamentals_invalid_flow_period")
            end = date.fromisoformat(period["end"])
            if date.fromisoformat(period["start"]) > end:
                raise ValueError("fundamentals_invalid_flow_period")
        if end > published:
            raise ValueError("fundamentals_period_after_publication")
    # A caller cannot approve changed facts by updating a hash inside its input.
    if digest(document) != MAPPING_SHA256:
        raise ValueError("fundamentals_mapping_not_reviewed")
    return document


def verify_pdf(pdf_path, document):
    """Read at most the local cap; PDF identity does not replace mapping review."""
    with Path(pdf_path).open("rb") as stream:
        body = stream.read(MAX_PDF_BYTES + 1)
    source = document["source"]
    if len(body) > MAX_PDF_BYTES or len(body) != source["byte_length"] or not body.startswith(b"%PDF-"):
        raise ValueError("fundamentals_pdf_invalid")
    if hashlib.sha256(body).hexdigest() != source["pdf_sha256"]:
        raise ValueError("fundamentals_pdf_hash_mismatch")


def import_fundamentals(connection, document, pdf_path, *, expected_profile="demo"):
    """Atomically import into an existing inactive DB with explicit profile.

    Availability is assigned by this operation's real clock, never supplied by
    the document. Repeated imports keep the first availability unchanged. DDL
    uses individual execute calls because executescript would commit early.
    """
    if not isinstance(expected_profile, str) or expected_profile not in {"demo", "real"}:
        raise ValueError("fundamentals_invalid_profile")
    document = copy.deepcopy(document)
    validate_manifest(document)
    verify_pdf(pdf_path, document)
    if connection.in_transaction:
        raise ValueError("fundamentals_existing_transaction")
    content = dumps(document)
    source = document["source"]
    connection.execute("BEGIN IMMEDIATE")
    try:
        imported_at = iso()
        if instant(source["fetched_at"]) > instant(imported_at):
            raise ValueError("fundamentals_future_fetch")
        profile = connection.execute("SELECT value_json FROM settings WHERE key='profile'").fetchone()
        if not profile or json.loads(profile[0]) != expected_profile:
            raise ValueError("fundamentals_profile_mismatch")
        lease = connection.execute("SELECT expires_at FROM worker_lease WHERE singleton=1").fetchone()
        if lease and instant(lease[0]) > instant(imported_at):
            raise ValueError("fundamentals_worker_active")
        if connection.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():
            raise ValueError("fundamentals_job_active")
        asset = connection.execute("SELECT ticker,asset_type,eligible FROM assets WHERE asset_id=?", (document["asset"]["asset_id"],)).fetchone()
        if not asset or tuple(asset) != ("VALE3", "stock", 1):
            raise ValueError("fundamentals_asset_mismatch")
        connection.execute("""CREATE TABLE IF NOT EXISTS fundamental_releases(
            release_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(asset_id),
            mapping_sha256 TEXT NOT NULL UNIQUE, pdf_sha256 TEXT NOT NULL,
            published_date TEXT NOT NULL, fetched_at TEXT NOT NULL,
            available_at TEXT NOT NULL, manifest_json TEXT NOT NULL)""")
        for action in ("UPDATE", "DELETE"):
            connection.execute(f"""CREATE TRIGGER IF NOT EXISTS fundamental_releases_no_{action.lower()}
                BEFORE {action} ON fundamental_releases BEGIN SELECT RAISE(ABORT,'immutable_record'); END""")
        existing = connection.execute("SELECT * FROM fundamental_releases WHERE release_id=?", (RELEASE_ID,)).fetchone()
        values = (RELEASE_ID, document["asset"]["asset_id"], MAPPING_SHA256, source["pdf_sha256"],
                  document["publication"]["date"], source["fetched_at"])
        if existing:
            if tuple(existing)[:6] != values or existing[7] != content or not instant(source["fetched_at"]) <= instant(existing[6]) <= instant(imported_at):
                raise ValueError("fundamentals_conflicting_release")
            available_at, inserted = existing[6], 0
        else:
            available_at, inserted = imported_at, 1
            connection.execute("INSERT INTO fundamental_releases VALUES(?,?,?,?,?,?,?,?)", (*values, available_at, content))
            connection.execute("INSERT INTO audit_events(event_type,created_at,payload_json) VALUES('fundamentals_imported',?,?)",
                               (imported_at, dumps({"release_id": RELEASE_ID, "mapping_sha256": MAPPING_SHA256})))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return {"inserted": inserted, "unchanged": 1 - inserted, "release_id": RELEASE_ID,
            "mapping_sha256": MAPPING_SHA256, "available_at": available_at}


def is_reviewed_filing(item):
    """Recognize reserved provenance even if the envelope was mislabeled."""
    if item.get("source_id") == SOURCE_ID:
        return True
    try:
        data = json.loads(item["content"])
        return isinstance(data, dict) and data.get("mapping_id") == MAPPING_ID
    except (ValueError, KeyError, TypeError):
        return False


def validate_filing(item, asset_id, as_of):
    """Recheck mapping and its evidence envelope, including replay boundaries."""
    document = validate_manifest(json.loads(item["content"]))
    if (item["evidence_id"] != RELEASE_ID or item["source_id"] != SOURCE_ID
            or item["record_type"] != "filing" or item["access_level"] != "structured"
            or item["entity_ids"] != [asset_id] or document["asset"]["asset_id"] != asset_id
            or item["url"] != document["source"]["document_url"]
            or item["published_at"] != document["publication"]["date"]
            or item["timestamp_precision"] != "date"
            or item["fetched_at"] != document["source"]["fetched_at"]
            or item["first_seen_at"] != item["available_at"]):
        raise ValueError("fundamentals_evidence_mismatch")
    if not instant(item["fetched_at"]) <= instant(item["available_at"]) <= instant(as_of):
        raise ValueError("fundamentals_unavailable_at_cutoff")
    if not publication_in_window(item["published_at"], "date", as_of):
        raise ValueError("fundamentals_publication_outside_window")
    return document


def filings_for_bundle(store, asset_id, as_of):
    """Read optional rows only. Missing table is a compatible empty result."""
    if not store.rows("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fundamental_releases'"):
        return []
    result = []
    for row in store.rows("SELECT * FROM fundamental_releases WHERE asset_id=? AND available_at<=? ORDER BY available_at,release_id", (asset_id, as_of)):
        document = validate_manifest(json.loads(row["manifest_json"]))
        if (row["mapping_sha256"] != MAPPING_SHA256 or row["release_id"] != RELEASE_ID
                or row["pdf_sha256"] != document["source"]["pdf_sha256"]
                or row["published_date"] != document["publication"]["date"]):
            raise ValueError("fundamentals_stored_provenance_mismatch")
        if not publication_in_window(row["published_date"], "date", as_of):
            continue
        item = {"evidence_id": row["release_id"], "record_type": "filing", "source_id": SOURCE_ID,
                "url": document["source"]["document_url"], "title": "Vale: quatro contas revisadas do release trimestral",
                "content": row["manifest_json"], "content_sha256": digest(row["manifest_json"]),
                "access_level": "structured", "published_at": row["published_date"], "modified_at": None,
                # First visibility inside this application, not first publication
                # or the earlier manual discovery of the public document.
                "first_seen_at": row["available_at"], "fetched_at": row["fetched_at"], "market_timestamp": None,
                "timestamp_precision": "date", "publisher_group": "Vale", "original_publisher": "Vale S.A.",
                "entity_ids": [asset_id], "quality_flags": list(document["limitations"]), "available_at": row["available_at"]}
        validate_filing(item, asset_id, as_of)
        result.append(item)
    return result
