import json

import pytest

from backend.cli_process import CLIError, ProcessResult
from backend.cli_runner import safe_cli_failure
from backend.core import dumps
from backend.evidence import EvidenceError, validate_analysis
from backend.validation_diagnostics import diagnostic_for, safe_execution_diagnostic
from test_cli_runner import ROOT, setup_runner
from test_informative_signal import signal_document


def test_duplicate_signal_evidence_still_rejected_without_wire_keyword():
    value, bundle = signal_document()
    value["signal"]["evidence_ids"] = ["synthetic_cited", "synthetic_cited"]
    value["evidence_ids"] = ["synthetic_cited"]
    with pytest.raises(EvidenceError, match="signal_evidence_duplicate") as failure:
        validate_analysis(value, bundle, ROOT / "schemas")
    assert diagnostic_for(failure.value, bundle)["issues"][0]["path"] == "/signal/evidence_ids"


def test_actual_outbound_schema_omits_unsupported_unique_items(setup_runner):
    runner, bundle, calls, *_ = setup_runner
    original_executor = runner.executor
    def inspect(args, **kwargs):
        from pathlib import Path
        schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text(encoding="utf-8"))
        assert "uniqueItems" not in dumps(schema)
        assert schema["properties"]["schema_version"]["const"] == "1.1"
        assert "signal" in schema["required"]
        return original_executor(args, **kwargs)
    runner.executor = inspect
    assert runner.run(bundle)["analysis"]["schema_version"] == "1.1"
    assert len(calls) == 1


def failed_schema_result():
    error = {"type": "error", "message": "Invalid schema for response_format 'SECRET_SCHEMA': PRIVATE_SENTINEL uniqueItems is not permitted. value=998877"}
    failed = {"type": "turn.failed", "error": {"message": "Invalid schema for response_format SECRET_SCHEMA"}}
    return ProcessResult(1, (dumps(error) + "\n" + dumps(failed)).encode(), b"PRIVATE_STDERR_SENTINEL")


def test_schema_failure_metadata_contains_only_exit_code_and_fixed_categories():
    result = safe_cli_failure(failed_schema_result())
    assert result == {"version": 1, "stage": "cli", "exit_code": 1, "category": "output_schema_rejected", "event_types": ["error", "turn.failed"]}
    assert "SECRET" not in dumps(result) and "PRIVATE" not in dumps(result) and "998877" not in dumps(result)


def test_model_message_cannot_be_classified_as_protocol_schema_error():
    event = {"type": "item.completed", "item": {"type": "agent_message", "text": "Invalid schema for response_format PRIVATE_SENTINEL"}}
    diagnostic = safe_cli_failure(ProcessResult(1, dumps(event).encode(), b""))
    assert diagnostic["category"] == "nonzero_exit" and diagnostic["event_types"] == []


def test_cli_diagnostic_persists_and_api_exposes_a_separate_safe_section(setup_runner):
    from backend.api import create_app
    from fastapi.testclient import TestClient
    runner, bundle, calls, config, *_ = setup_runner
    runtime = json.loads(config.read_text())
    runtime["max_repair_attempts"] = 0
    config.write_text(dumps(runtime))
    job = runner.store.enqueue("analyse", {"asset_id": bundle["asset"]["asset_id"]}, "synthetic-cli-failure")
    def failed(*args, **kwargs):
        calls.append(1)
        return failed_schema_result()
    runner.executor = failed
    with pytest.raises(CLIError, match="cli_execution_failed"):
        runner.run(bundle, job_id=job["job_id"])
    assert len(calls) == 1
    assert runner.store.rows("SELECT status FROM analysis_runs") == [{"status": "blocked"}]
    assert runner.store.rows("SELECT status FROM quota_ledger") == [{"status": "consumed"}]
    events = runner.store.rows("SELECT * FROM audit_events WHERE event_type='analysis_cli_failure'")
    assert len(events) == 1 and "PRIVATE" not in events[0]["payload_json"] and "SECRET" not in events[0]["payload_json"]
    assert not runner.store.rows("SELECT * FROM analyses")
    with TestClient(create_app(runner.store.path, profile="real"), base_url="http://127.0.0.1:8000") as client:
        response = client.get("/api/v1/assets/brapi:TEST3/analysis-job").json()
    assert "validation_diagnostics" not in response
    section = response["execution_diagnostics"]
    assert len(section) == 1 and section[0]["diagnostic"]["category"] == "output_schema_rejected"
    assert section[0]["diagnostic"]["exit_code"] == 1
    assert "SECRET" not in dumps(response) and "PRIVATE" not in dumps(response)


def test_runtime_sanitizer_cannot_echo_arbitrary_stored_values():
    output = safe_execution_diagnostic({"category": "PRIVATE_SENTINEL", "exit_code": "SECRET_PATH", "event_types": ["error", "PRIVATE_EVENT"], "raw": "PRIVATE_BODY"})
    assert output == {"version": 1, "stage": "cli", "exit_code": None, "category": "nonzero_exit", "event_types": ["error"]}
