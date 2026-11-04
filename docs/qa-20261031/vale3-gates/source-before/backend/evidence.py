from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import statistics
from datetime import timedelta
from pathlib import Path

from .core import Store, digest, dumps
from .validation_diagnostics import safe_issue
from .numeric_support import numbers_supported, numeric_rejection_reason
from .fundamentals import filings_for_bundle, is_reviewed_filing, validate_filing
from .session_continuity import calendar_for_bars, continuity_reason
from .timeutil import SAO_PAULO, instant, iso, news_window, publication_in_window, session_not_after


class EvidenceError(ValueError):
    def __init__(self, message, issues=None):
        super().__init__(message)
        self.issues = issues


def semantic_bundle_hash(bundle):
    """Hash material inputs, excluding only the request and calculation clocks.

    Calendar-day validity prevents a reused snapshot from hiding expiration or
    staleness changes, including month-end dates sharing the same clamped start.
    Source evidence clocks, revision IDs and market observation times stay intact.
    """
    semantic = copy.deepcopy(bundle)
    semantic.pop("bundle_id", None)
    semantic.pop("as_of", None)
    semantic["window"].pop("end", None)
    for item in semantic["evidence"]:
        if item["record_type"] == "computed_metric":
            for clock in ("first_seen_at", "fetched_at", "available_at"):
                item.pop(clock, None)
    return digest({"cache_version": "semantic_bundle_v1", "local_day": instant(bundle["as_of"]).astimezone(SAO_PAULO).date().isoformat(), "material_inputs": semantic})


def schema_validate(document, schema_name, schema_dir):
    from jsonschema import Draft202012Validator
    schema = json.loads((Path(schema_dir) / f"{schema_name}.schema.json").read_text(encoding="utf-8-sig"))
    errors = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda e: str(e.path))
    if errors:
        raise EvidenceError("schema_invalid:" + schema_name,
                            [safe_issue('schema_invalid', e.path, e.validator) for e in errors[:8]])


def comparable(bars, needed):
    if len(bars) < needed:
        return "insufficient_observations"
    sample = bars[-needed:]
    if len({(b["provider"], b["currency"], b["adjustment"]) for b in sample}) != 1:
        return "mixed_series_currency_or_adjustment"
    if sample[0]["adjustment"] in {"unknown", "raw"}:
        return "adjustment_unknown" if sample[0]["adjustment"] == "unknown" else "raw_series_corporate_actions_unverified"
    if any(b.get("close") is None or b["close"] <= 0 for b in sample):
        return "missing_or_nonpositive_close"
    if any(not b.get("complete_session") for b in sample):
        return "session_completeness_unverified"
    if any("stale" in b.get("quality_flags", []) for b in sample):
        return "stale_price"
    if len({b["session_date"] for b in sample}) != len(sample):
        return "duplicate_session"
    return continuity_reason(sample)


def metrics_for_bars(bars: list[dict]) -> list[dict]:
    bars = sorted(bars, key=lambda x: x["session_date"])
    def metric(key, label, needed, formula, unit, operation):
        reason = comparable(bars, needed)
        sample = bars[-needed:] if not reason else []
        value = operation(sample) if sample else None
        if value is not None and not math.isfinite(value):
            value, reason = None, "non_finite_calculation"
        return {"metric_id": key, "label": label, "value": value, "unit": unit, "start_at": sample[0].get("market_timestamp") if sample else None, "end_at": sample[-1].get("market_timestamp") if sample else None, "formula_version": formula, "adjustment": bars[-1]["adjustment"] if bars else "unknown", "evidence_ids": [b["revision_id"] for b in sample], "missing_reason": reason, "evidence_id": None}
    result = [metric(f"price_return_{n}", f"Retorno de preço em {n} pregões", n + 1, "price_end/price_start-1:v1", "fraction", lambda s: s[-1]["close"] / s[0]["close"] - 1) for n in (1, 5, 21, 63)]
    result.append(metric("realized_volatility_20", "Volatilidade realizada (20 retornos)", 21, "sample_std(log_daily_returns)*sqrt(252):v1", "annualized_fraction", lambda s: statistics.stdev(math.log(b["close"] / a["close"]) for a, b in zip(s, s[1:])) * math.sqrt(252)))
    volume = metric("relative_volume_20", "Volume relativo a 20 pregões", 21, "volume_last/median(previous20):v1", "multiple", lambda s: None)
    if volume["missing_reason"] is None:
        sample = bars[-21:]
        if any(b.get("volume") is None for b in sample):
            volume["missing_reason"] = "missing_volume"
        else:
            median = statistics.median(b["volume"] for b in sample[:-1])
            if median <= 0:
                volume["missing_reason"] = "nonpositive_volume_median"
            else:
                volume["value"] = sample[-1]["volume"] / median
    result.append(volume)
    return result


def validate_bundle(bundle, schema_dir=None):
    if schema_dir:
        schema_validate(bundle, "evidence-bundle", schema_dir)
    as_of = instant(bundle["as_of"])
    start, end = news_window(as_of)
    if bundle["window"] != {"start": start, "end": end, "lookback_calendar_months": 3, "timezone": "America/Sao_Paulo"}:
        raise EvidenceError("incorrect_calendar_window")
    evidence = {e["evidence_id"]: e for e in bundle["evidence"]}
    if len(evidence) != len(bundle["evidence"]):
        raise EvidenceError("duplicate_evidence_id")
    for e in evidence.values():
        if e["content_sha256"] != hashlib.sha256(e["content"].encode()).hexdigest():
            raise EvidenceError("evidence_content_hash_mismatch")
        if is_reviewed_filing(e):
            try:
                validate_filing(e, bundle["asset"]["asset_id"], bundle["as_of"])
            except (ValueError, TypeError, KeyError):
                raise EvidenceError("fundamentals_evidence_invalid") from None
        clocks = {}
        for key in ("first_seen_at", "fetched_at", "available_at", "market_timestamp", "modified_at"):
            if e.get(key):
                clocks[key] = instant(e[key])
                if clocks[key] > as_of:
                    raise EvidenceError("future_evidence:" + key)
        if clocks["available_at"] < max(clocks["first_seen_at"], clocks["fetched_at"]):
            raise EvidenceError("availability_precedes_knowledge")
        if e.get("published_at"):
            if e["timestamp_precision"] == "date":
                from datetime import date
                if date.fromisoformat(e["published_at"][:10]) > as_of.astimezone(SAO_PAULO).date():
                    raise EvidenceError("future_evidence:published_at")
            elif instant(e["published_at"]) > as_of:
                raise EvidenceError("future_evidence:published_at")
        if e["record_type"] == "article" and not publication_in_window(e["published_at"], e["timestamp_precision"], bundle["as_of"]):
            raise EvidenceError("article_outside_window_or_unknown_time")
        if e["record_type"] == "article" and e["timestamp_precision"] == "instant" and e.get("modified_at") and instant(e["modified_at"]) < instant(e["published_at"]):
            raise EvidenceError("modification_precedes_publication")
        if e["record_type"] in {"price", "computed_metric"} and bundle["asset"]["asset_id"] not in e["entity_ids"]:
            raise EvidenceError("financial_evidence_wrong_asset")
        if e["record_type"] == "price":
            try:
                price = json.loads(e["content"])
                session_allowed = session_not_after(price["session_date"], as_of)
            except (KeyError, TypeError, ValueError):
                raise EvidenceError("invalid_price_session_date") from None
            calendar = price.get('calendar')
            if calendar:
                try:
                    if instant(calendar['verified_at']) > as_of or instant(calendar['closes_at']) > as_of:
                        raise EvidenceError('future_calendar_evidence')
                except (KeyError, TypeError, ValueError) as exc:
                    if isinstance(exc, EvidenceError):
                        raise
                    raise EvidenceError('invalid_calendar_evidence') from None
            if not session_allowed:
                raise EvidenceError("future_evidence:session_date")
            if price.get("currency") != bundle["asset"]["currency"]:
                raise EvidenceError("price_currency_does_not_match_asset")
    for metric in bundle["computed_metrics"]:
        if not set(metric["evidence_ids"]) <= evidence.keys():
            raise EvidenceError("metric_input_missing")
        if metric["value"] is None:
            if metric["evidence_id"] is not None or not metric["missing_reason"]:
                raise EvidenceError("null_metric_requires_reason")
            continue
        if metric["adjustment"] == "unknown" or metric["missing_reason"] is not None:
            raise EvidenceError("unsupported_nonnull_metric")
        ev = evidence.get(metric["evidence_id"])
        if not ev or ev["record_type"] != "computed_metric":
            raise EvidenceError("metric_evidence_missing")
        value = json.loads(ev["content"])
        for field in ("value", "unit", "formula_version", "evidence_ids"):
            if value.get(field) != metric[field]:
                raise EvidenceError("metric_evidence_contradiction")
        try:
            inputs = []
            for eid in metric["evidence_ids"]:
                source = evidence[eid]
                if source["record_type"] != "price":
                    raise EvidenceError("metric_requires_price_inputs")
                inputs.append({**json.loads(source["content"]), "provider": source["source_id"], "revision_id": eid, "market_timestamp": source["market_timestamp"], "quality_flags": source["quality_flags"]})
            calculated = next(m for m in metrics_for_bars(inputs) if m["metric_id"] == metric["metric_id"])
            if calculated["value"] is None or not math.isclose(calculated["value"], metric["value"], rel_tol=1e-10, abs_tol=1e-12) or calculated["formula_version"] != metric["formula_version"] or calculated["unit"] != metric["unit"]:
                raise EvidenceError("metric_recalculation_mismatch")
        except (StopIteration, KeyError, TypeError, json.JSONDecodeError):
            raise EvidenceError("metric_input_contract_invalid") from None
    return True


def validate_analysis(analysis, bundle, schema_dir=None, analysis_id=None, *, expected_schema_version=None):
    if schema_dir:
        schema_validate(analysis, "analysis-v1.1" if analysis.get("schema_version") == "1.1" else "analysis", schema_dir)
    if expected_schema_version and analysis.get("schema_version") != expected_schema_version:
        raise EvidenceError("signal_required", [safe_issue("signal_required", ["schema_version"])])
    validate_bundle(bundle, schema_dir)
    if analysis_id and analysis["analysis_id"] != analysis_id:
        raise EvidenceError("analysis_id_mismatch")
    if analysis["asset_id"] != bundle["asset"]["asset_id"] or analysis["as_of"] != bundle["as_of"]:
        raise EvidenceError("analysis_identity_or_time_mismatch")
    evidence = {e["evidence_id"]: e for e in bundle["evidence"]}
    used = set()
    def contains_certainty(text):
        normalized = " ".join(text.casefold().split())
        # Recognize only this immediate, explicit negation. Removing one
        # disclaimer must not hide a separate affirmative promise later on.
        normalized = re.sub(r"\b(?:não|nao) garante retorno\b", "", normalized)
        return any(phrase in normalized for phrase in ("não precificado", "nao precificado", "garante retorno", "lucro garantido", "retorno garantido"))
    def supported_text(text, refs, path, kind=None):
        if analysis.get("schema_version") == "1.1":
            if not text.strip():
                raise EvidenceError("explanation_empty", [safe_issue("explanation_empty", path)])
            if contains_certainty(text):
                raise EvidenceError("unsupported_financial_certainty", [safe_issue("unsupported_financial_certainty", path)])
        if not numbers_supported(text, [evidence[i] for i in refs]):
            try:
                reason = numeric_rejection_reason(text, [evidence[i] for i in refs])
            except Exception:
                # Best-effort metadata must never replace the original rejection.
                reason = None
            raise EvidenceError("factual_number_not_in_cited_evidence", [
                safe_issue("factual_number_not_in_cited_evidence", path,
                           evidence_ids=refs, known_evidence_ids=evidence, claim_kind=kind, numeric_reason=reason)])
    for claim_index, claim in enumerate(analysis["claims"]):
        refs = set(claim["evidence_ids"])
        if not refs or not refs <= evidence.keys():
            raise EvidenceError("claim_evidence_missing")
        used |= refs
        if claim["kind"] == "fact":
            if contains_certainty(claim["statement"]):
                raise EvidenceError("unsupported_financial_certainty")
            # Numeric facts must occur in cited content; semantic support still
            # requires human review and is never claimed to be fully proven here.
            supported_text(claim["statement"], refs, ["claims", claim_index, "statement"], claim["kind"])
        elif analysis.get("schema_version") == "1.1":
            supported_text(claim["statement"], refs, ["claims", claim_index, "statement"], claim["kind"])
    for scenario_index, scenario in enumerate(analysis["scenarios"]):
        if scenario["horizon_days"] not in bundle["forecast_horizons_days"]:
            raise EvidenceError("invalid_scenario_horizon")
        if not scenario["conditions"] or not scenario["invalidation_conditions"]:
            raise EvidenceError("scenario_requires_conditions")
        refs = set(scenario["evidence_ids"])
        if not refs or not refs <= evidence.keys():
            raise EvidenceError("scenario_evidence_missing")
        if analysis.get("schema_version") == "1.1":
            supported_text(scenario["description"], refs, ["scenarios", scenario_index, "description"])
            for field in ("conditions", "invalidation_conditions"):
                for index, text in enumerate(scenario[field]):
                    supported_text(text, refs, ["scenarios", scenario_index, field, index])
        used |= refs
    opportunity = analysis["opportunity"]
    refs = set(opportunity["evidence_ids"])
    if not refs <= evidence.keys():
        raise EvidenceError("opportunity_evidence_missing")
    if opportunity["classification"] != "insufficient_data" and not refs:
        raise EvidenceError("opportunity_support_required")
    used |= refs
    if analysis.get("schema_version") == "1.1":
        signal = analysis.get("signal")
        if not isinstance(signal, dict):
            raise EvidenceError("signal_required", [safe_issue("signal_required", ["signal"])])
        signal_refs = set(signal["evidence_ids"])
        if len(signal_refs) != len(signal["evidence_ids"]):
            raise EvidenceError("signal_evidence_duplicate", [safe_issue("signal_evidence_duplicate", ["signal", "evidence_ids"])])
        if not signal_refs <= evidence.keys():
            raise EvidenceError("signal_evidence_missing", [safe_issue("signal_evidence_missing", ["signal", "evidence_ids"])])
        if signal["as_of"] != analysis["as_of"]:
            raise EvidenceError("signal_identity_or_time_mismatch", [safe_issue("signal_identity_or_time_mismatch", ["signal", "as_of"])])
        if type(signal["horizon_days"]) is not int or signal["horizon_days"] not in bundle["forecast_horizons_days"]:
            raise EvidenceError("signal_horizon_invalid", [safe_issue("signal_horizon_invalid", ["signal", "horizon_days"])])
        expected_classification = {"buy": "investigate", "sell": "avoid_for_now", "wait": "watch", "insufficient_data": "insufficient_data"}
        allowed_status = {"buy": {"supported_hypothesis"}, "sell": {"supported_hypothesis"},
                          "wait": {"inconclusive", "supported_hypothesis"}, "insufficient_data": {"insufficient_data"}}
        action = signal["action"]
        if opportunity["classification"] != expected_classification[action] or analysis["status"] not in allowed_status[action]:
            raise EvidenceError("signal_classification_mismatch", [safe_issue("signal_classification_mismatch", ["signal", "action"])])
        # A limited reviewed release is not a directional readiness contract.
        # JSON shape or isolated financial facts cannot establish that readiness.
        if action in {"buy", "sell"}:
            raise EvidenceError("signal_directional_data_unverified", [safe_issue("signal_directional_data_unverified", ["signal", "action"])])
        substantive = [evidence[i] for i in signal_refs if evidence[i]["record_type"] in {"article", "filing", "exposure"}
                       and evidence[i]["access_level"] != "headline_only" and bundle["asset"]["asset_id"] in evidence[i]["entity_ids"]]
        if action == "wait" and not substantive:
            raise EvidenceError("signal_support_insufficient", [safe_issue("signal_support_insufficient", ["signal", "evidence_ids"])])
        supported_text(signal["rationale"], signal_refs, ["signal", "rationale"])
        for field in ("risks", "counterpoints", "invalidation_conditions"):
            for index, text in enumerate(signal[field]):
                supported_text(text, signal_refs, ["signal", field, index])
        supported_text(opportunity["rationale"], refs, ["opportunity", "rationale"])
        used |= signal_refs
        supported_text(analysis["synthesis"], used, ["synthesis"])
        for field in ("risks", "missing_evidence", "data_quality_flags"):
            for index, text in enumerate(analysis[field]):
                supported_text(text, used, [field, index])
    if set(analysis["evidence_ids"]) != used:
        raise EvidenceError("root_evidence_not_exact_union")
    if analysis["status"] == "insufficient_data" and opportunity["classification"] != "insufficient_data":
        raise EvidenceError("abstention_classification_mismatch")
    if analysis["status"] == "supported_hypothesis":
        substantive = [evidence[i] for i in used if evidence[i]["record_type"] in {"article", "filing", "exposure"} and evidence[i]["access_level"] != "headline_only"]
        if not substantive or not analysis["risks"] or not analysis["scenarios"]:
            raise EvidenceError("insufficient_support_for_hypothesis")
    return True


def fit_bundle_budget(bundle, max_characters, omitted=0):
    """Fit a fresh builder candidate, once, by omitting only whole records.

    Prices must be chronological, articles newest first, and the final gap must
    be the builder's omitted-article counter. Keep filings whole, latest price
    and metric inputs. If required evidence cannot fit, fail before persistence.
    """
    evidence = bundle["evidence"]
    required = {eid for metric in bundle["computed_metrics"] for eid in metric["evidence_ids"]}
    prices = [e for e in evidence if e["record_type"] == "price"]
    if prices:
        required.add(prices[-1]["evidence_id"])
    original_gaps = list(bundle["coverage"]["known_gaps"][:-1])
    price_omitted = 0
    while True:
        coverage = bundle["coverage"]
        coverage["known_gaps"] = original_gaps + [f"Evidências omitidas por data desconhecida, janela ou limite: {omitted}"]
        if price_omitted:
            coverage["known_gaps"].append(f"Barras de preço antigas omitidas do bundle por limite de tamanho: {price_omitted}; histórico completo preservado na base.")
            if "bundle_price_evidence_omitted" not in bundle["quality_flags"]:
                bundle["quality_flags"].append("bundle_price_evidence_omitted")
        coverage["editorial_sources_reached"] = len({e["source_id"] for e in evidence if e["record_type"] == "article"})
        coverage["full_text_count"] = sum(e["access_level"] == "full_text" for e in evidence)
        coverage["partial_text_count"] = sum(e["access_level"] in {"excerpt", "headline_only"} for e in evidence)
        if len(dumps(bundle)) <= max_characters:
            return
        index = next((i for i, e in enumerate(evidence) if e["record_type"] == "price" and e["evidence_id"] not in required), None)
        if index is not None:
            evidence.pop(index)
            price_omitted += 1
            continue
        articles = [i for i, e in enumerate(evidence) if e["record_type"] == "article"]
        if not articles:
            raise EvidenceError("bundle_size_limit")
        evidence.pop(articles[-1])
        omitted += 1


def build_bundle(store: Store, asset_id: str, as_of=None, schema_dir=None, max_characters=60000):
    as_of = iso(as_of)
    assets = store.rows("SELECT * FROM assets WHERE asset_id=? AND eligible=1", (asset_id,))
    if not assets:
        raise EvidenceError("eligible_asset_not_found")
    asset = assets[0]
    start, end = news_window(as_of)
    evidence, omitted = [], 0
    # Select revisions actually known at cutoff, not today's latest revision.
    articles = store.rows("SELECT a.*,r.* FROM articles a JOIN article_revisions r ON r.article_id=a.article_id WHERE r.available_at<=? AND r.revision_id=(SELECT r2.revision_id FROM article_revisions r2 WHERE r2.article_id=a.article_id AND r2.available_at<=? ORDER BY r2.available_at DESC,r2.rowid DESC LIMIT 1) ORDER BY r.published_at DESC,a.article_id", (as_of, as_of))
    ticker_pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(asset["ticker"]) + r"(?![A-Za-z0-9])", re.I)
    for article in articles:
        if not ticker_pattern.search(article["title"] + " " + article["content"]):
            continue
        if not publication_in_window(article["published_at"], article["timestamp_precision"], as_of):
            omitted += 1
            continue
        evidence.append({"evidence_id": article["revision_id"], "record_type": "article", "source_id": article["source_id"], "url": article["canonical_url"], "title": article["title"], "content": article["content"], "content_sha256": digest(article["content"]), "access_level": article["access_level"], "published_at": article["published_at"], "modified_at": article["modified_at"], "first_seen_at": article["first_seen_at"], "fetched_at": article["fetched_at"], "market_timestamp": None, "timestamp_precision": article["timestamp_precision"], "publisher_group": article["publisher_group"], "original_publisher": article["original_publisher"], "entity_ids": [asset_id], "quality_flags": json.loads(article["quality_flags_json"]), "available_at": article["available_at"]})
        if len(evidence) >= 40:
            omitted += 1
            break
    filings = filings_for_bundle(store, asset_id, as_of)
    evidence.extend(filings)
    bars = store.rows("SELECT b.*,r.* FROM price_bars b JOIN price_revisions r ON r.bar_id=b.bar_id WHERE b.asset_id=? AND r.available_at<=? AND (r.market_timestamp IS NULL OR r.market_timestamp<=?) AND r.revision_id=(SELECT r2.revision_id FROM price_revisions r2 WHERE r2.bar_id=b.bar_id AND r2.available_at<=? ORDER BY r2.available_at DESC,r2.rowid DESC LIMIT 1) ORDER BY b.session_date", (asset_id, as_of, as_of, as_of))
    # Legacy rows can predate ingestion checks; filter before selecting a family
    # or calculating metrics, even when market_timestamp is missing or earlier.
    bars = [bar for bar in bars if session_not_after(bar["session_date"], as_of)]
    # Choose latest provider/adjustment family; never mix families to fill gaps.
    if bars:
        family = (bars[-1]["provider"], bars[-1]["adjustment"])
        bars = [b for b in bars if (b["provider"], b["adjustment"]) == family][-64:]
    bars = calendar_for_bars(store, bars, as_of)
    for bar in bars:
        content = dumps({k: bar[k] for k in ("close", "open", "high", "low", "volume", "currency", "session_date", "adjustment", "complete_session", "calendar")})
        flags = json.loads(bar["quality_flags_json"])
        evidence.append({"evidence_id": bar["revision_id"], "record_type": "price", "source_id": bar["provider"], "url": None, "title": f"{asset['ticker']} {bar['session_date']}", "content": content, "content_sha256": digest(content), "access_level": "structured", "published_at": None, "modified_at": None, "first_seen_at": bar["fetched_at"], "fetched_at": bar["fetched_at"], "market_timestamp": bar["market_timestamp"], "timestamp_precision": "instant" if bar["market_timestamp"] else "date", "publisher_group": None, "original_publisher": None, "entity_ids": [asset_id], "quality_flags": flags, "available_at": max(bar["available_at"], bar["calendar"]["verified_at"]) if bar["calendar"] else bar["available_at"]})
        if "daily_bar_timestamp_not_trade_time" in flags:
            evidence[-1]["timestamp_precision"] = "date"
    if bars and (instant(as_of).astimezone(SAO_PAULO).date() - __import__("datetime").date.fromisoformat(bars[-1]["session_date"])).days > 7:
        bars[-1]["quality_flags"] = ["stale"]
    metrics = metrics_for_bars(bars)
    for metric in metrics:
        if metric["value"] is not None:
            content = dumps({k: metric[k] for k in ("value", "unit", "formula_version", "evidence_ids")})
            metric["evidence_id"] = "metric_evidence_" + digest(content)[:24]
            evidence.append({"evidence_id": metric["evidence_id"], "record_type": "computed_metric", "source_id": "deterministic_metrics_v1", "url": None, "title": metric["label"], "content": content, "content_sha256": digest(content), "access_level": "structured", "published_at": None, "modified_at": None, "first_seen_at": as_of, "fetched_at": as_of, "market_timestamp": metric["end_at"], "timestamp_precision": "instant", "publisher_group": None, "original_publisher": None, "entity_ids": [asset_id], "quality_flags": [], "available_at": as_of})
    coverage = store.coverage()
    bundle = {"schema_version": "1.0", "bundle_id": "pending", "as_of": as_of, "asset": {k: asset[k] for k in ("asset_id", "ticker", "name", "asset_type", "currency", "sector")}, "window": {"start": start, "end": end, "lookback_calendar_months": 3, "timezone": "America/Sao_Paulo"}, "forecast_horizons_days": store.setting("horizons_days", [30, 90]), "evidence": evidence, "computed_metrics": metrics, "coverage": {"eligible_assets": coverage["eligible_assets"], "assets_with_recent_prices": len({b["asset_id"] for b in bars if b.get("market_timestamp") and instant(b["market_timestamp"]) >= instant(as_of) - timedelta(days=7)}), "editorial_sources_selected": 8, "editorial_sources_reached": len({e["source_id"] for e in evidence if e["record_type"] == "article"}), "full_text_count": sum(e["access_level"] == "full_text" for e in evidence), "partial_text_count": sum(e["access_level"] in {"excerpt", "headline_only"} for e in evidence), "truncated_queries_count": coverage["truncated_queries_count"], "known_gaps": [("Fundamentos parciais: quatro contas de um release revisado; direção permanece bloqueada" if filings else "Fundamentos e exposição econômica ainda não verificados"), "Calendário de pregões exige fonte verificada", f"Evidências omitidas por data desconhecida, janela ou limite: {omitted}"]}, "quality_flags": ["link_by_explicit_ticker_only", "not_a_complete_news_archive", "no_automatic_causal_validation"]}
    if filings:
        bundle["quality_flags"].append("limited_reviewed_fundamentals")
    # Reserve the final ID length so a near-limit bundle cannot grow afterward.
    bundle["bundle_id"] = "bundle_" + "0" * 24
    fit_bundle_budget(bundle, max_characters, omitted)
    content_hash = semantic_bundle_hash(bundle)
    bundle["bundle_id"] = "bundle_" + content_hash[:24]
    if len(dumps(bundle)) > max_characters:
        raise EvidenceError("bundle_size_limit")
    validate_bundle(bundle, schema_dir)
    with store.transaction() as con:
        cached = con.execute("SELECT as_of,payload_json FROM evidence_bundles WHERE content_hash=?", (content_hash,)).fetchone()
        if cached and instant(cached["as_of"]) <= instant(as_of):
            # Return the immutable snapshot, including its original as_of and
            # computed-metric availability. Never rewrite it to look freshly run.
            bundle = json.loads(cached["payload_json"])
        else:
            if cached:
                # A historical request must never borrow a future snapshot, even
                # when its material inputs happen to be identical.
                content_hash = digest({"semantic_hash": content_hash, "historical_cutoff": as_of})
                bundle["bundle_id"] = "bundle_" + content_hash[:24]
            con.execute("INSERT OR IGNORE INTO evidence_bundles VALUES(?,?,?,?,?,?)", (bundle["bundle_id"], asset_id, as_of, content_hash, dumps(bundle), iso()))
            bundle = json.loads(con.execute("SELECT payload_json FROM evidence_bundles WHERE content_hash=?", (content_hash,)).fetchone()[0])
    if len(dumps(bundle)) > max_characters:
        raise EvidenceError("bundle_size_limit")
    validate_bundle(bundle, schema_dir)
    return bundle


def save_analysis(store, analysis, bundle, model, prompt_hash, schema_hash, schema_dir=None, expected_analysis_id=None):
    try:
        validate_analysis(analysis, bundle, schema_dir, expected_analysis_id)
    except (EvidenceError, ValueError, KeyError, TypeError):
        with store.transaction() as con:
            con.execute("INSERT INTO quarantine(record_type,reason,record_hash,created_at) VALUES('analysis','analysis_validation_failed',?,?)", (digest(analysis), iso()))
        raise
    with store.transaction() as con:
        con.execute("INSERT OR IGNORE INTO analyses VALUES(?,?,?,?,?,?,?,?,?,?)", (analysis["analysis_id"], analysis["asset_id"], bundle["bundle_id"], analysis["as_of"], model, prompt_hash, schema_hash, analysis["status"], dumps(analysis), iso()))
    return analysis
