"""Independent compatibility/failure-diagnostic audit; no real subprocesses."""
import json

import pytest

from backend.cli_process import CLIError, ProcessResult
from backend.cli_runner import safe_cli_failure
from test_cli_runner import setup_runner


def test_unclassifiable_deep_error_json_cannot_break_the_failure_diagnostic():
    # Well below the transport's byte limit, but beyond Python's JSON recursion
    # depth. A diagnostic must never prevent the original failure from finishing.
    payload = ("[" * 2000 + '"PRIVATE_SENTINEL"' + "]" * 2000).encode()
    diagnostic = safe_cli_failure(ProcessResult(1, payload, b"PRIVATE_STDERR"))
    assert diagnostic == {"version": 1, "stage": "cli", "exit_code": 1,
                          "category": "nonzero_exit", "event_types": []}


@pytest.mark.parametrize("exit_code", [True, "PRIVATE_EXIT_CODE", 2**40, -(2**40)])
def test_exit_code_diagnostic_is_bounded_and_does_not_coerce_private_values(exit_code):
    diagnostic = safe_cli_failure(ProcessResult(exit_code, b"", b"PRIVATE_STDERR"))
    assert diagnostic["exit_code"] is None
    assert diagnostic["category"] == "nonzero_exit"
    assert set(diagnostic) == {"version", "stage", "exit_code", "category", "event_types"}


def test_deep_error_json_preserves_original_failure_and_final_run_state(setup_runner):
    runner, bundle, calls, config_path, *_ = setup_runner
    config = json.loads(config_path.read_text())
    config["max_repair_attempts"] = 0
    config_path.write_text(json.dumps(config))
    payload = ("[" * 2000 + '"PRIVATE_SENTINEL"' + "]" * 2000).encode()

    def failed(*args, **kwargs):
        calls.append(1)
        return ProcessResult(1, payload, b"PRIVATE_STDERR")

    runner.executor = failed
    with pytest.raises(CLIError, match="cli_execution_failed"):
        runner.run(bundle)
    assert len(calls) == 1
    assert runner.store.rows("SELECT status,error_code FROM analysis_runs") == [
        {"status": "blocked", "error_code": "cli_execution_failed"}]
    assert runner.store.rows("SELECT status FROM quota_ledger") == [{"status": "consumed"}]
    rows = runner.store.rows("SELECT payload_json FROM audit_events WHERE event_type='analysis_cli_failure'")
    assert len(rows) == 1 and "PRIVATE" not in rows[0]["payload_json"]
    diagnostic = json.loads(rows[0]["payload_json"])["diagnostic"]
    assert diagnostic["category"] == "nonzero_exit" and diagnostic["exit_code"] == 1
    assert not runner.store.rows("SELECT * FROM analyses")
