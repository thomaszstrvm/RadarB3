"""Macro provider status is coherent without network, real data or AI calls."""
import pytest

from backend.adapters import BCBAdapter
from backend.api import create_app
from backend.core import Store, dumps
from backend.safe_http import FetchError, Response
from backend.worker import Worker


AS_OF = "2026-10-30T20:00:00Z"
OBSERVATIONS = [{"data": "30/10/2026", "valor": "13.25"}]


def fake_bcb(monkeypatch, outcomes):
    class Transport:
        def get(self, url):
            outcome = outcomes.pop(0)
            if isinstance(outcome, FetchError):
                raise outcome
            return Response(200, dumps(outcome).encode("utf-8"), {})

    transport = Transport()
    monkeypatch.setattr("backend.worker.BCBAdapter", lambda store: BCBAdapter(store, transport))


def collect(worker, kind):
    job = worker.store.enqueue(kind, {"as_of": AS_OF})
    assert worker.run_once(job_ids=[job["job_id"]])
    return worker.store.get_job(job["job_id"])


def provider_view(app):
    # Call the registered read-only handlers without opening sockets or a server.
    routes = {route.path: route.endpoint for route in app.routes if route.path in {"/api/v1/sources", "/api/v1/operations"}}
    sources = routes["/api/v1/sources"]()
    operations = routes["/api/v1/operations"]()
    source = next(item for item in sources["items"] if item["id"] == "bcb_sgs")
    collection = operations["collection"]
    assert collection["sources"] == [{
        "provider_id": "bcb_sgs",
        "last_attempt_at": source["last_attempt_at"],
        "last_success_at": source["last_success_at"],
        "error_code": source["error"],
        "next_attempt_at": source["next_attempt_at"],
    }]
    assert collection["latest_macro_fetch"] == source["last_success_at"]
    return source


@pytest.mark.parametrize("kind", ["macro", "bcb"])
@pytest.mark.parametrize("retryable", [False, True])
def test_macro_failure_and_recovery_share_provider_and_preserve_history(tmp_path, monkeypatch, kind, retryable):
    fake_bcb(monkeypatch, [OBSERVATIONS, FetchError("fixture_fetch_failure", retryable, 3600), OBSERVATIONS])
    monkeypatch.setattr("backend.api.ai_status", lambda: {"enabled": False, "reason": "isolated_test"})
    app = create_app(tmp_path / "macro.db", execution_mode="collection-only")
    store = app.state.store
    worker = Worker(store, execution_mode="collection-only")
    try:
        assert collect(worker, kind)["status"] == "succeeded"
        first_success = provider_view(app)
        assert first_success["status"] == "available"
        assert first_success["last_success_at"] is not None
        observations = store.rows("SELECT * FROM macro_observations")
        assert len(observations) == 1

        failed = collect(worker, kind)
        failed_runs = store.rows("SELECT * FROM job_runs WHERE job_id=?", (failed["job_id"],))
        assert failed["status"] == ("retry_wait" if retryable else "blocked")
        assert failed["error_code"] == "fixture_fetch_failure"
        failure = provider_view(app)
        assert failure["status"] == "unavailable"
        assert failure["error"] == "fixture_fetch_failure"
        assert failure["last_success_at"] == first_success["last_success_at"]
        assert store.rows("SELECT * FROM macro_observations") == observations

        assert collect(worker, kind)["status"] == "succeeded"
        recovered = provider_view(app)
        assert recovered["status"] == "available"
        assert recovered["error"] is None
        assert recovered["last_success_at"] == recovered["last_attempt_at"]
        assert store.rows("SELECT provider_id FROM provider_state") == [{"provider_id": "bcb_sgs"}]
        assert store.get_job(failed["job_id"]) == failed
        assert store.rows("SELECT * FROM job_runs WHERE job_id=?", (failed["job_id"],)) == failed_runs
        assert store.rows("SELECT * FROM macro_observations") == observations
        assert not store.rows("SELECT * FROM quota_ledger")
    finally:
        worker.close()


def test_macro_status_fix_preserves_legacy_aliases_and_other_providers(tmp_path, monkeypatch):
    fake_bcb(monkeypatch, [FetchError("fixture_fetch_failure"), OBSERVATIONS])
    store = Store(tmp_path / "legacy.db")
    for provider in ("macro", "bcb", "br_g1"):
        store.provider_status(provider, "unavailable", "fixture_older_failure")
    previous = store.rows("SELECT * FROM provider_state ORDER BY provider_id")
    worker = Worker(store, execution_mode="collection-only")
    try:
        assert collect(worker, "macro")["status"] == "blocked"
        failed = store.rows("SELECT * FROM provider_state WHERE provider_id='bcb_sgs'")[0]
        assert failed["last_success_at"] is None
        assert collect(worker, "macro")["status"] == "succeeded"
        assert store.rows("SELECT * FROM provider_state WHERE provider_id<>'bcb_sgs' ORDER BY provider_id") == previous
    finally:
        worker.close()
