"""Prove the existing one-use capturer cannot diagnose counterpoints.

Synthetic executors and temporary grants only; no real capture is read.
"""
import json

import pytest

from backend import bounded_rejection_qa as qa
from backend.cli_process import CLIError
from test_bounded_rejection_qa import prepared, public_bundle, setup_runner
from test_cli_runner import encode_events
from test_vintage_bundle import analysis_for


def test_reviewed_capture_refuses_counterpoint_even_with_public_bundle(prepared):
    runner, bundle, permission, calls, _ = prepared

    def synthetic_counterpoint(*args, **kwargs):
        envelope = json.loads(kwargs['stdin'].decode().rsplit('\n\n', 1)[1])
        analysis = analysis_for(envelope['evidence_bundle'])
        analysis['analysis_id'] = envelope['analysis_id']
        analysis['signal']['counterpoints'] = ['R63 indisponível.']
        calls.append('synthetic counterpoint')
        return encode_events(analysis)

    runner.executor = synthetic_counterpoint
    with pytest.raises(CLIError, match='cli_analysis_validation_rejected'):
        runner.run(bundle, repair_limit=0, reviewed_retry=permission)

    assert len(calls) == 2
    diagnostic = json.loads(runner.store.rows(
        "SELECT payload_json FROM audit_events WHERE event_type='analysis_validation' ORDER BY id DESC LIMIT 1"
    )[0]['payload_json'])['diagnostic']['issues'][0]
    assert diagnostic['path'] == '/signal/counterpoints/0'
    assert diagnostic['numeric_reason'] == 'unsupported_format'
    assert permission.capture_outcome['reason'] == 'qa_capture_issue_blocked'
    assert permission.capture_outcome['retention_pending'] is False
    assert not (permission.base / 'rejected-claim.json').exists()
    assert (permission.base / 'authorization-used.json').is_file()
    assert len(runner.store.rows('SELECT * FROM quota_ledger')) == 2


@pytest.mark.parametrize('synthetic_text', [
    'R63 indisponível.',
    'Retorno de 0.125 fraction.',
    'Alinhamento de -0.75 dimensionless.',
    'Volatilidade de 0.25 annualized_fraction.',
    'Receita trimestral de USD 1e3 milhões.',
])
def test_current_capture_is_narrower_than_counterpoint_numeric_families(synthetic_text):
    with pytest.raises(qa.QAError):
        qa.sanitized_sentence(synthetic_text)
