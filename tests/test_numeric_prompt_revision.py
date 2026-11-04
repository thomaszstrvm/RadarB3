import json
import shutil

import pytest

from backend.cli_process import CLIError, ProcessResult
from backend.cli_runner import MODEL, POLICY_VERSION, REASONING_EFFORT
from backend.core import digest
from test_cli_runner import ROOT, setup_runner


def test_material_numeric_prompt_revision_changes_key_without_replaying_prior_attempt(setup_runner, tmp_path):
    runner, bundle, calls, *_ = setup_runner
    prompt_dir = tmp_path / "prompts"
    shutil.copytree(ROOT / "prompts", prompt_dir)
    runner.prompt_dir = prompt_dir
    supplement = prompt_dir / "analysis-signal-v1.1.txt"
    revised = supplement.read_text(encoding="utf-8-sig")
    start, body = revised.split("FORMAS NUMÉRICAS — REVISÃO LEXICAL BRL TERMINAL\n", 1)
    lexical, tail = body.split("A justificativa e as listas de signal", 1)
    assert "somente quando encerram o texto" in lexical
    assert "três letras, de qualquer caixa" in lexical
    previous = start + "A justificativa e as listas de signal" + tail
    supplement.write_text(previous, encoding="utf-8")
    valid_executor = runner.executor

    def failed(*args, **kwargs):
        calls.append("isolated old attempt")
        return ProcessResult(1, b"", b"")

    runner.executor = failed
    with pytest.raises(CLIError, match="cli_execution_failed"):
        runner.run(bundle)
    old_run = runner.store.rows("SELECT * FROM analysis_runs")[0]
    old_quota = runner.store.rows("SELECT * FROM quota_ledger")[0]
    supplement.write_text(revised, encoding="utf-8")

    def inspect_revision(*args, **kwargs):
        assert lexical.encode("utf-8") in kwargs["stdin"]
        return valid_executor(*args, **kwargs)

    runner.executor = inspect_revision
    result = runner.run(bundle)
    assert not result["cache_reused"] and len(calls) == 2
    runs = runner.store.rows("SELECT * FROM analysis_runs ORDER BY started_at")
    assert runs[0] == old_run and runs[1]["cache_key"] != old_run["cache_key"]
    system = (prompt_dir / "analysis-system.txt").read_text(encoding="utf-8-sig") + "\n\n" + revised
    expected_prompt = digest({"system": system, "request_template": (prompt_dir / "analysis-request.txt").read_text(encoding="utf-8-sig"),
                              "wrapper": "json_envelope_v1", "policy": POLICY_VERSION, "reasoning": REASONING_EFFORT})
    schema_hash = digest(json.loads((ROOT / "schemas/analysis-v1.1.schema.json").read_text(encoding="utf-8")))
    assert runs[1]["prompt_hash"] == expected_prompt
    assert runs[1]["cache_key"] == digest([bundle["bundle_id"], MODEL, expected_prompt, schema_hash])
    assert runner.store.rows("SELECT * FROM quota_ledger WHERE reservation_id=?", (old_quota["reservation_id"],))[0] == old_quota

    supplement.write_text(previous, encoding="utf-8")
    with pytest.raises(CLIError, match="cli_previous_attempt_requires_review"):
        runner.run(bundle)
    assert len(calls) == 2 and len(runner.store.rows("SELECT * FROM quota_ledger")) == 2
