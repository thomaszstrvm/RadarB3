"""Deterministic, budget-bounded automatic selection; never invokes the CLI."""
from __future__ import annotations

import json
import re
from datetime import datetime

from .cli_runner import read_json, status
from .cli_process import CLIError
from .timeutil import SAO_PAULO, instant, iso, publication_in_window


def schedule_analyses(store, config_path, report_path, as_of=None):
    from .execution_mode import resolve_mode
    if resolve_mode() == 'collection-only':
        return []
    now = iso(as_of)
    day = instant(now).astimezone(SAO_PAULO).date().isoformat()
    if store.profile != "real":
        return []
    try:
        runtime = read_json(config_path)
    except CLIError:
        return []
    if runtime.get("automatic_analysis") is not True or not status(config_path, report_path)["enabled"]:
        return []
    limit = store.setting("max_analyses_per_day", 10)
    if type(limit) is not int or not 0 < limit <= 10:
        return []
    used = store.rows("SELECT COALESCE(SUM(units),0) AS units FROM quota_ledger WHERE provider='codex_cli' AND period=? AND status<>'released'", (day,))[0]["units"]
    queued = store.rows("SELECT created_at,payload_json FROM jobs WHERE kind IN ('analyse','analysis') AND status IN ('queued','running','retry_wait')")
    active_today = sum(1 for job in queued if instant(job["created_at"]).astimezone(SAO_PAULO).date().isoformat() == day or json.loads(job["payload_json"]).get("schedule_day") == day)
    # Conservatively count active jobs in addition to reservations; a currently
    # running reserved attempt may occupy both, which only reduces consumption.
    remaining = max(0, limit - used - active_today)
    if not remaining:
        return []
    articles = store.rows("SELECT r.title,r.content,r.published_at,r.timestamp_precision,r.available_at FROM articles a JOIN article_revisions r ON r.revision_id=a.latest_revision_id WHERE r.available_at<=?", (now,))
    articles = [a for a in articles if publication_in_window(a["published_at"], a["timestamp_precision"], now)]
    assets = store.rows("SELECT a.asset_id,a.ticker,MAX(n.as_of) AS last_analysis FROM assets a LEFT JOIN analyses n ON n.asset_id=a.asset_id WHERE a.eligible=1 GROUP BY a.asset_id,a.ticker ORDER BY a.ticker")
    existing = {row["dedupe_key"] for row in store.rows("SELECT dedupe_key FROM jobs WHERE dedupe_key LIKE ?", ("auto-analysis:" + day + ":%",))}
    candidates = []
    for asset in assets:
        dedupe_key = f"auto-analysis:{day}:{asset['asset_id']}"
        if dedupe_key in existing:
            continue
        ticker = re.compile(r"(?<![A-Za-z0-9])" + re.escape(asset["ticker"]) + r"(?![A-Za-z0-9])", re.I)
        timestamps = []
        for article in articles:
            if ticker.search(article["title"] + " " + article["content"]):
                published = article["published_at"]
                dt = datetime.fromisoformat(published[:10]).replace(tzinfo=SAO_PAULO) if article["timestamp_precision"] == "date" else instant(published)
                timestamps.append(dt.timestamp())
        if timestamps:
            candidates.append((bool(asset["last_analysis"]), asset["last_analysis"] or "", -max(timestamps), asset["ticker"], asset["asset_id"], dedupe_key))
    result = []
    for _, _, _, _, asset_id, dedupe_key in sorted(candidates)[:remaining]:
        job = store.enqueue("analyse", {"asset_id": asset_id, "as_of": now, "automatic": True, "schedule_day": day}, dedupe_key, priority=80, max_attempts=1)
        result.append(job["job_id"])
    return result
