"""The one-shot egress guard rejects any payload outside the reviewed hash."""
import copy
import importlib.util
from pathlib import Path

import pytest

from backend.core import digest
from test_numeric_validator_audit import documents

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reviewed_manual_operation", ROOT / "docs/qa-20261031/second-manual-ai/execute_manual_analysis.py")
operation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operation)  # main() is not executed; no DB, HTTP or CLI.


def test_reviewed_payload_is_returned_unchanged():
    _, bundle = documents("Public synthetic content.", "Public synthetic content.")
    assert operation.require_reviewed_bundle(bundle, bundle["bundle_id"], digest(bundle)) is bundle


@pytest.mark.parametrize("change", ["identity", "content"])
def test_different_payload_is_blocked_before_analysis_runner(change):
    _, bundle = documents("Public synthetic content.", "Public synthetic content.")
    reviewed_id, reviewed_hash = bundle["bundle_id"], digest(bundle)
    changed = copy.deepcopy(bundle)
    if change == "identity":
        changed["bundle_id"] = "different_bundle"
    else:
        changed["evidence"][0]["content"] = "UNREVIEWED_SENTINEL"
        changed["evidence"][0]["content_sha256"] = digest("UNREVIEWED_SENTINEL")
    with pytest.raises(ValueError, match="unreviewed_public_payload_blocked"):
        operation.require_reviewed_bundle(changed, reviewed_id, reviewed_hash)
