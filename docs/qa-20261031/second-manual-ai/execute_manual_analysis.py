"""Reviewed one-shot operation. No scheduler, collection, repair or replay."""
from pathlib import Path
import json
import os
import sys
import urllib.request
from datetime import datetime

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.cli_runner import REASONING_EFFORT, file_sha256, validated_configuration
from backend.core import Store, digest
from backend.evidence import validate_bundle
import backend.worker as worker_module
from backend.timeutil import SAO_PAULO, iso
from backend.worker import Worker

OUT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8765"
KEY = "second-manual-ai-20261031-vale3-signal-v11-ultra-one"
CONFIG = OUT / "scoped-runtime/config"
REPORT = CONFIG.parent / "docs/cli-validation.json"
ASSET = "brapi:VALE3"


def require_reviewed_bundle(bundle, reviewed_id, reviewed_hash):
    validate_bundle(bundle, ROOT / "schemas")
    if bundle["bundle_id"] != reviewed_id or digest(bundle) != reviewed_hash:
        raise ValueError("unreviewed_public_payload_blocked")
    return bundle


def main():
    if sys.argv[1:] != ["--execute-one-authorized-signal"]:
        raise SystemExit("Explicit one-shot argument required; nothing executed.")
    marker = OUT / "execution-start.json"
    if marker.exists():
        raise SystemExit("Execution marker exists: inspect its job and ledger; no automatic replay.")
    assert REASONING_EFFORT == "ultra"
    assert not (OUT / "execution-result.json").exists()
    runtime, _ = validated_configuration(CONFIG / "ai-runtime.json", REPORT)
    assert runtime["max_repair_attempts"] == 0 and runtime["timeout_seconds"] == 300 and runtime["automatic_analysis"] is False
    expected = json.loads((OUT / "operation-plan.json").read_text(encoding="utf-8"))
    public_audit = json.loads((OUT / "public-payload-audit.json").read_text(encoding="utf-8"))
    assert public_audit["bundle_id"] == expected["expected_bundle"]
    assert public_audit["canonical_bundle_sha256"] == expected["audited_bundle_sha256"]
    for name, digest in expected["protected_hashes"].items():
        assert file_sha256(ROOT / name) == digest, name
    period = datetime.now(SAO_PAULO).date().isoformat()
    assert period == "2026-10-31"
    os.environ["RADAR_EXECUTION_MODE"] = "analysis-enabled"
    store = Store(ROOT / "data/real/radar.sqlite3", "real", wal=True)
    assert store.setting("max_analyses_per_day", 10) == 10
    quota_before = store.rows("SELECT * FROM quota_ledger WHERE provider='codex_cli' ORDER BY reservation_id")
    baseline = json.loads((OUT / "baseline.json").read_text(encoding="utf-8"))
    assert quota_before == baseline["quota"], "Ledger changed: reconcile before execution."
    assert sum(r["units"] for r in quota_before if r["period"] == period and r["status"] != "released") == 1
    assert not store.rows("SELECT * FROM worker_lease")
    assert not store.rows("SELECT job_id FROM jobs WHERE status IN ('queued','running','retry_wait')")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, *, method="GET", headers=None):
        req = urllib.request.Request(BASE + path, method=method, data=b"" if method == "POST" else None, headers=headers or {})
        with opener.open(req, timeout=25) as response:
            return json.load(response)

    operations = request("/api/v1/operations")
    assert operations["execution_mode"] == "analysis-enabled" and operations["ai"]["enabled"]
    assert operations["worker"]["status"] != "active"
    token = request("/api/v1/session")["token"]  # Local CSRF token stays in memory.
    with marker.open("x", encoding="utf-8") as stream:
        json.dump({"started_at": iso(), "key": KEY, "asset_id": ASSET, "quota_before": quota_before}, stream, indent=2)
    queued = request("/api/v1/assets/" + ASSET + "/analyse", method="POST", headers={
        "x-radar-token": token, "origin": BASE, "idempotency-key": KEY})
    job_id = queued["job_id"]
    (OUT / "queued-job.json").write_text(json.dumps(queued, indent=2), encoding="utf-8")
    job = store.get_job(job_id)
    assert job["kind"] == "analyse" and job["payload"] == {"asset_id": ASSET} and job["status"] == "queued"
    print(json.dumps({"job_id": job_id, "state": "queued", "max_calls": 1}), flush=True)
    worker = Worker(store, config_dir=CONFIG, schema_dir=ROOT / "schemas", execution_mode="analysis-enabled")
    original_builder = worker_module.build_bundle
    def reviewed_builder(*args, **kwargs):
        return require_reviewed_bundle(original_builder(*args, **kwargs), expected["expected_bundle"], expected["audited_bundle_sha256"])
    worker_module.build_bundle = reviewed_builder  # This one-shot process only.
    try:
        worker.start(recover_job_ids=[])
        assert worker.run_once(job_ids=[job_id])
    finally:
        worker.close()
        worker_module.build_bundle = original_builder
    final = store.get_job(job_id)
    quota_after = store.rows("SELECT * FROM quota_ledger WHERE provider='codex_cli' ORDER BY reservation_id")
    previous = {r["reservation_id"]: r for r in quota_before}
    assert all(r == previous[r["reservation_id"]] for r in quota_after if r["reservation_id"] in previous)
    added = [r for r in quota_after if r["reservation_id"] not in previous]
    assert len(added) <= 1 and sum(r["units"] for r in added) <= 1
    result = {"finished_at": iso(), "job": final, "quota_added": added,
        "tracking": request("/api/v1/assets/" + ASSET + "/analysis-job"),
        "operations": request("/api/v1/operations")}
    (OUT / "execution-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": final["status"], "error_code": final["error_code"], "result": final["result"], "quota_added": added}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
