import copy
import io
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from backend.cli_process import CLIError, ProcessResult, execute_bounded
from backend.cli_runner import AnalysisRunner, MODEL, POLICY_VERSION, command_arguments, file_sha256, parse_cli_output, sanitized_environment, status, validated_configuration
from backend.core import QuotaExceeded, Store, dumps
from backend.evidence import build_bundle
from backend.ingest import upsert_catalog
from backend.ingest import ingest_article
from backend.analysis_schedule import schedule_analyses

ROOT = Path(__file__).resolve().parents[1]


def encode_events(analysis, *, tool=False, usage=True):
    events = [{"type": "thread.started", "thread_id": "synthetic"}, {"type": "turn.started"}]
    if tool:
        events.append({"type": "item.completed", "item": {"type": "command_execution", "command": "should never run"}})
    events.append({"type": "item.completed", "item": {"type": "agent_message", "text": dumps(analysis)}})
    events.append({"type": "turn.completed", **({"usage": {"input_tokens": 100, "output_tokens": 50}} if usage else {})})
    return ProcessResult(0, "\n".join(dumps(e) for e in events).encode(), b"")


def final_analysis(envelope):
    bundle = envelope["evidence_bundle"]
    value = {"schema_version": envelope.get("schema_version", "1.0"), "analysis_id": envelope["analysis_id"], "as_of": bundle["as_of"], "asset_id": bundle["asset"]["asset_id"], "status": "insufficient_data", "synthesis": "Dados insuficientes.", "claims": [], "scenarios": [], "opportunity": {"classification": "insufficient_data", "rationale": "Sem evidência suficiente.", "evidence_ids": []}, "risks": [], "missing_evidence": ["Notícias e exposição econômica"], "data_quality_flags": [], "evidence_ids": []}
    if value["schema_version"] == "1.1":
        value["signal"] = {"action": "insufficient_data", "horizon_days": bundle["forecast_horizons_days"][0],
                           "as_of": bundle["as_of"], "rationale": "Sem evidência suficiente.", "evidence_ids": [],
                           "risks": ["Cobertura incompleta."], "counterpoints": ["Evidência adicional pode mudar a conclusão."],
                           "invalidation_conditions": ["Reavaliar quando houver fundamentos verificáveis."]}
    return value


@pytest.fixture
def setup_runner(tmp_path):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"test double; never executed")
    config_path = tmp_path / "ai-runtime.json"
    config_path.write_text(dumps({"enabled": True, "model": MODEL, "timeout_seconds": 30, "max_repair_attempts": 1}), encoding="utf-8")
    report_path = tmp_path / "cli-validation.json"
    report_path.write_text(dumps({"enabled": True, "isolation_verified": True, "model_verified": True, "tools_disabled_verified": True, "authentication": "ChatGPT", "policy_version": POLICY_VERSION, "model": MODEL, "version": "test", "executable": str(executable), "executable_sha256": file_sha256(executable), "live_analysis_tested": False}), encoding="utf-8")
    store = Store(tmp_path / "test.sqlite3")
    upsert_catalog(store, [{"symbol": "TEST3", "name": "Synthetic test only", "assetType": "stock"}])
    bundle = build_bundle(store, "brapi:TEST3", schema_dir=ROOT / "schemas")
    calls = []
    def executor(args, **kwargs):
        envelope = json.loads(kwargs["stdin"].decode().rsplit("\n\n", 1)[1])
        assert Path(args[args.index("--output-schema") + 1]).is_file()
        calls.append({"args": args, "env": kwargs["env"], "envelope": envelope, "cwd": kwargs["cwd"]})
        return encode_events(final_analysis(envelope))
    runner = AnalysisRunner(store, schema_dir=ROOT / "schemas", prompt_dir=ROOT / "prompts", config_path=config_path, report_path=report_path, executor=executor, mutex_factory=nullcontext)
    return runner, bundle, calls, config_path, report_path, executable


def test_gate_fail_closed_and_binary_change(setup_runner):
    runner, bundle, calls, config, report, exe = setup_runner
    assert status(config, report)["enabled"] is True
    exe.write_bytes(b"changed")
    with pytest.raises(CLIError, match="binary_changed"):
        runner.run(bundle)
    assert calls == []
    assert status(config, report)["enabled"] is False


@pytest.mark.parametrize("field", ["enabled", "isolation_verified", "model_verified", "tools_disabled_verified"])
def test_each_attestation_is_required(setup_runner, field):
    runner, bundle, calls, config, report, exe = setup_runner
    data = json.loads(report.read_text())
    data[field] = False
    report.write_text(dumps(data))
    with pytest.raises(CLIError):
        runner.run(bundle)
    assert calls == []
    assert runner.store.rows("SELECT * FROM quota_ledger") == []


def test_environment_no_paid_keys_or_user_overrides():
    env = sanitized_environment({"SystemRoot": "C:/Windows", "SystemDrive": "C:", "USERPROFILE": "C:/Users/Test", "OPENAI_API_KEY": "secret", "CODEX_API_KEY": "secret", "CODEX_HOME": "secret-location", "BRAPI_TOKEN": "secret", "HTTP_PROXY": "proxy", "AZURE_OPENAI_API_KEY": "secret"})
    assert env == {"SYSTEMROOT": "C:/Windows", "SYSTEMDRIVE": "C:", "USERPROFILE": "C:/Users/Test"}
    with pytest.raises(CLIError, match="windows_environment"):
        sanitized_environment({"SYSTEMROOT": "C:/Windows"})


def test_no_tools_minimal_policy_and_stdin_only(setup_runner, monkeypatch):
    runner, bundle, calls, *_ = setup_runner
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-leak")
    monkeypatch.setenv("CODEX_API_KEY", "do-not-leak")
    result = runner.run(bundle)
    assert result["calls"] == 1
    call = calls[0]
    joined = " ".join(call["args"])
    assert call["args"][-1] == "-"
    assert "evidence_bundle" not in joined and "TEST3" not in joined
    assert "--sandbox" not in call["args"]
    assert 'windows.sandbox="mxc"' in call["args"]
    assert 'forced_login_method="chatgpt"' in call["args"]
    assert 'model_reasoning_effort="ultra"' in call["args"]
    assert 'web_search="disabled"' in call["args"]
    policy = next(a for a in call["args"] if a.startswith("permissions.radar_analysis="))
    assert '":minimal"="read"' in policy and '"enabled"=false' in policy
    assert ":root" not in policy and "write" not in policy
    assert "--enable" not in call["args"]
    for feature in ("shell_tool", "apps", "plugins", "hooks", "memories", "multi_agent", "multi_agent_v2", "view_image", "browser_use"):
        assert call["args"][call["args"].index(feature) - 1] == "--disable"
    assert "OPENAI_API_KEY" not in call["env"] and "CODEX_API_KEY" not in call["env"]
    assert not call["cwd"].exists()  # Temporary inputs removed after completion.
    assert runner.store.rows("SELECT status FROM analysis_runs")[0]["status"] == "validated"
    assert len(runner.store.rows("SELECT * FROM analyses")) == 1


def test_cache_reuses_result_without_new_process_or_quota(setup_runner):
    runner, bundle, calls, *_ = setup_runner
    first = runner.run(bundle)
    second = runner.run(bundle)
    assert second["cache_reused"] is True and second["calls"] == 0
    assert first["analysis"] == second["analysis"]
    assert len(calls) == len(runner.store.rows("SELECT * FROM quota_ledger")) == 1


def test_reasoning_change_cannot_reuse_previous_effort_cache(setup_runner, monkeypatch):
    runner, bundle, calls, *_ = setup_runner
    with monkeypatch.context() as previous:
        previous.setattr("backend.cli_runner.REASONING_EFFORT", "low")
        old = runner.run(bundle)
    new = runner.run(bundle)
    assert new["cache_reused"] is False
    assert new["analysis"]["analysis_id"] != old["analysis"]["analysis_id"]
    assert 'model_reasoning_effort="ultra"' in calls[-1]["args"]
    assert len(calls) == len(runner.store.rows("SELECT * FROM quota_ledger")) == 2


def test_quota_before_subprocess(setup_runner):
    runner, bundle, calls, *_ = setup_runner
    runner.store.set_setting("max_analyses_per_day", 0)
    with pytest.raises(QuotaExceeded):
        runner.run(bundle)
    assert calls == []


def test_one_repair_same_model_bundle_and_counted(setup_runner):
    runner, bundle, calls, *_ = setup_runner
    def executor(args, **kw):
        env = json.loads(kw["stdin"].decode().rsplit("\n\n", 1)[1])
        calls.append(env)
        result = final_analysis(env)
        if len(calls) == 1:
            result["asset_id"] = "invented_asset"
        return encode_events(result)
    runner.executor = executor
    result = runner.run(bundle)
    assert result["calls"] == 2
    assert calls[0]["evidence_bundle"] == calls[1]["evidence_bundle"] == bundle
    assert calls[1]["validation_feedback_codes"] == ["cli_analysis_validation_rejected"]
    assert len(runner.store.rows("SELECT * FROM quarantine")) == 1
    assert [r["status"] for r in runner.store.rows("SELECT status FROM quota_ledger")] == ["consumed", "consumed"]


def test_repair_limit_and_no_restart_replay(setup_runner):
    runner, bundle, calls, *_ = setup_runner
    def invalid(args, **kw):
        calls.append(1)
        return encode_events({"bad": "schema"})
    runner.executor = invalid
    with pytest.raises(CLIError, match="validation_rejected"):
        runner.run(bundle)
    assert len(calls) == 2
    with pytest.raises(CLIError, match="previous_attempt_requires_review"):
        runner.run(bundle)
    assert len(calls) == 2
    assert runner.store.rows("SELECT * FROM analyses") == []


def test_unexpected_tool_event_rejected_without_repair(setup_runner):
    runner, bundle, calls, *_ = setup_runner
    def tool(args, **kw):
        calls.append(1)
        env = json.loads(kw["stdin"].decode().rsplit("\n\n", 1)[1])
        return encode_events(final_analysis(env), tool=True)
    runner.executor = tool
    with pytest.raises(CLIError, match="unexpected_tool_activity"):
        runner.run(bundle)
    assert len(calls) == 1
    assert runner.store.rows("SELECT * FROM analyses") == []


def test_missing_usage_or_real_error_rejected():
    with pytest.raises(CLIError, match="usage_missing"):
        parse_cli_output(encode_events({}, usage=False))
    output = dumps({"type": "item.completed", "item": {"type": "error", "message": "failed"}}).encode()
    with pytest.raises(CLIError, match="reported_error"):
        parse_cli_output(ProcessResult(0, output, b""))


@pytest.mark.parametrize("stderr", [b"ERROR codex_core::tools::router: execution error", b"2026-10-26T19:00:00Z WARN unexpected permission fallback"])
def test_stderr_runtime_error_rejected_despite_valid_final(stderr):
    result = encode_events({})
    result.stderr = stderr
    with pytest.raises(CLIError, match="cli_reported_error"):
        parse_cli_output(result)


def test_known_stderr_notice_is_not_runtime_error():
    result = encode_events({})
    result.stderr = b"Reading additional input from stdin...\nUnder-development features enabled: skip_host_skill_discovery. Under-development features are incomplete.\n"
    assert parse_cli_output(result)[0] == {}


def test_timeout_preserves_uncertain_usage_without_retry(setup_runner):
    runner, bundle, calls, *_ = setup_runner
    def timeout(*a, **kw):
        calls.append(1)
        raise CLIError("cli_timeout_usage_uncertain", uncertain=True)
    runner.executor = timeout
    with pytest.raises(CLIError, match="timeout"):
        runner.run(bundle)
    assert len(calls) == 1
    assert runner.store.rows("SELECT status FROM quota_ledger")[0]["status"] == "uncertain"
    with pytest.raises(CLIError, match="previous_attempt"):
        runner.run(bundle)
    assert len(calls) == 1


def test_bundle_snapshot_identity_is_enforced(setup_runner):
    runner, bundle, calls, *_ = setup_runner
    altered = copy.deepcopy(bundle)
    altered["quality_flags"].append("tampered")
    with pytest.raises(CLIError, match="snapshot_mismatch"):
        runner.run(altered)
    assert calls == []


class FakeProcess:
    def __init__(self, *, output=b"", running=False):
        self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(output), io.BytesIO()
        self.returncode = None if running else 0
        self.killed = False
    def poll(self):
        return self.returncode
    def kill(self):
        self.killed = True
        self.returncode = -9
    def wait(self, timeout=None):
        return self.returncode


def transport_for(process, order):
    def popen(args, **kwargs):
        assert kwargs["shell"] is False
        assert kwargs["stdin"] is not None
        order.append("spawn")
        return process
    class Job:
        def __init__(self, p):
            order.append("contained")
        def close(self):
            order.append("kill_owned_tree")
            if process.poll() is None:
                process.kill()
    return popen, Job


def test_process_timeout_kills_only_contained_process(tmp_path):
    process, order = FakeProcess(running=True), []
    popen, job = transport_for(process, order)
    with pytest.raises(CLIError, match="timeout"):
        execute_bounded(["fake.exe", "-"], stdin=b"test", cwd=tmp_path, env={}, timeout=0.01, popen_factory=popen, job_factory=job)
    assert order[:2] == ["spawn", "contained"]
    assert "kill_owned_tree" in order and process.killed


def test_process_output_limit_kills_tree(tmp_path):
    process, order = FakeProcess(output=b"X" * 5000), []
    popen, job = transport_for(process, order)
    with pytest.raises(CLIError, match="output_size_limit"):
        execute_bounded(["fake.exe", "-"], stdin=b"test", cwd=tmp_path, env={}, max_stdout=100, popen_factory=popen, job_factory=job)
    assert "kill_owned_tree" in order


def test_process_cancel_kills_tree(tmp_path):
    process, order = FakeProcess(running=True), []
    popen, job = transport_for(process, order)
    count = [0]
    def cancelled():
        count[0] += 1
        if count[0] >= 2:
            raise InterruptedError("user cancelled")
    with pytest.raises(InterruptedError):
        execute_bounded(["fake.exe", "-"], stdin=b"test", cwd=tmp_path, env={}, cancel_check=cancelled, popen_factory=popen, job_factory=job)
    assert process.killed and "kill_owned_tree" in order


def schedule_fixture(setup_runner, count=15):
    runner, bundle, calls, config, report, exe = setup_runner
    data = json.loads(config.read_text())
    data["automatic_analysis"] = True
    config.write_text(dumps(data))
    records = [{"symbol": f"COMP{i:02d}3", "name": "Fixture", "assetType": "stock"} for i in range(count)]
    upsert_catalog(runner.store, records)
    source = {"id": "fin_yahoo", "allowed_hostnames": ["finance.yahoo.com"], "publisher_group": "yahoo"}
    for i, record in enumerate(records):
        ingest_article(runner.store, {"url": f"https://finance.yahoo.com/news/test-{i}", "title": record["symbol"] + " resultado", "content": "Informação sintética", "published_at": "2026-10-25T12:00:00Z", "fetched_at": "2026-10-25T13:00:00Z", "timestamp_precision": "instant"}, source)
    return runner, config, report, calls, source


def test_automatic_schedule_bounded_coalesced_and_no_cli(setup_runner):
    runner, config, report, calls, _ = schedule_fixture(setup_runner)
    jobs = schedule_analyses(runner.store, config, report, "2026-10-26T23:00:00Z")
    assert len(jobs) == 10 and calls == []
    assert schedule_analyses(runner.store, config, report, "2026-10-26T23:30:00Z") == []
    selected = [runner.store.get_job(job)["payload"]["asset_id"] for job in jobs]
    assert selected == sorted(selected)
    assert runner.store.rows("SELECT * FROM analyses") == []


def test_automatic_schedule_subtracts_durable_usage_and_manual_queue(setup_runner):
    runner, config, report, calls, _ = schedule_fixture(setup_runner)
    runner.store.reserve_quota("codex_cli", "2026-10-26", 10, "used", units=4)
    runner.store.enqueue("analyse", {"asset_id": "brapi:TEST3", "schedule_day": "2026-10-26"})
    jobs = schedule_analyses(runner.store, config, report, "2026-10-26T23:00:00Z")
    assert len(jobs) == 5 and calls == []


@pytest.mark.parametrize("mode", ["disabled", "demo", "zero_quota", "gate_disabled"])
def test_automatic_schedule_fail_closed(setup_runner, mode):
    runner, config, report, calls, _ = schedule_fixture(setup_runner)
    if mode == "disabled":
        data = json.loads(config.read_text())
        data["automatic_analysis"] = False
        config.write_text(dumps(data))
    elif mode == "demo":
        runner.store.profile = "demo"
    elif mode == "zero_quota":
        runner.store.set_setting("max_analyses_per_day", 0)
    else:
        data = json.loads(report.read_text())
        data["enabled"] = False
        report.write_text(dumps(data))
    assert schedule_analyses(runner.store, config, report, "2026-10-26T23:00:00Z") == []
    assert calls == []


def test_automatic_schedule_excludes_expired_unknown_future_news(setup_runner):
    runner, config, report, calls, source = schedule_fixture(setup_runner, count=0)
    for i, publication in enumerate(("2026-07-25T12:00:00Z", None, "2026-10-27T12:00:00Z")):
        ingest_article(runner.store, {"url": f"https://finance.yahoo.com/news/excluded-{i}", "title": "TEST3 resultado", "content": "Fixture", "published_at": publication, "fetched_at": "2026-10-25T13:00:00Z", "timestamp_precision": "instant" if publication else "unknown"}, source)
    assert schedule_analyses(runner.store, config, report, "2026-10-26T23:00:00Z") == []
    assert calls == []
