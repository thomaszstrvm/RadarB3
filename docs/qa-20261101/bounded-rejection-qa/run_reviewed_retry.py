"""Explicit operator-only QA helper. No enabled authorization is bundled here."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import backend
import argparse
from contextlib import closing
import json
import sqlite3
from backend.bounded_rejection_qa import ReviewedRetry, QAError, cleanup_capture, create_record, QA_ROOT, ERROR_CODES
from backend.cli_runner import AnalysisRunner
from backend.cli_process import CLIError
from backend.core import Store, QuotaExceeded
from backend.execution_mode import resolve_mode

PUBLIC_BUNDLE = ROOT / 'docs/qa-20261101/manual-ai-v1.2/actual-bundle.json'
LIVE_DB = ROOT / 'data/real/radar.sqlite3'


def checked_live_db(path):
    # The operator CLI must never charge an isolated copy's independent ledger.
    # Isolated tests inject a runner into run_reviewed instead of using this CLI.
    if path is None or Path(path).absolute() != LIVE_DB.absolute():
        raise QAError('qa_binding_mismatch')
    candidate = Path(path).absolute()
    for part in (candidate, *candidate.parents):
        info = part.lstat()
        if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise QAError('qa_path_not_regular')
    if not candidate.is_file():
        raise QAError('qa_binding_mismatch')
    return candidate


def run_reviewed(runner, bundle, permission):
    """Accept an injected isolated runner in tests; never change its transport."""
    try:
        result = runner.run(bundle, repair_limit=0, reviewed_retry=permission)
        return {'status': 'validated', 'calls': result['calls'], 'capture': permission.capture_outcome}
    except CLIError as exc:
        allowed = ERROR_CODES | {'qa_manual_scope_required', 'cli_analysis_validation_rejected',
            'cli_previous_attempt_requires_review', 'cli_execution_failed', 'cli_bundle_snapshot_mismatch',
            'cli_local_quota_configuration_invalid', 'cli_subscription_limit_reached', 'cli_interrupted_usage_uncertain'}
        return {'status': 'rejected' if exc.code == 'cli_analysis_validation_rejected' else 'blocked',
                'code': exc.code if exc.code in allowed else 'qa_execution_blocked', 'capture': permission.capture_outcome}
    except QuotaExceeded:
        return {'status': 'blocked', 'code': 'qa_quota_exhausted', 'capture': permission.capture_outcome}
    except Exception:
        return {'status': 'blocked', 'code': 'qa_execution_failed', 'capture': permission.capture_outcome}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'run-approved-attempt', 'cleanup-capture'))
    parser.add_argument('--authorization-sha256')
    parser.add_argument('--capture-sha256')
    parser.add_argument('--db', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'cleanup-capture':
            cleanup_capture(args.capture_sha256)
            result = {'status': 'capture_deleted'}
        else:
            permission = ReviewedRetry.load(QA_ROOT / 'authorization.json', args.authorization_sha256)
            if args.action == 'inspect':
                result = {'status': 'permission_loaded', 'permission_sha256': permission.sha256}
            else:
                if resolve_mode() != 'manual-analysis':
                    raise QAError('qa_permission_invalid')
                db_path = checked_live_db(args.db)
                with closing(sqlite3.connect(db_path.as_uri() + '?mode=rw', uri=True)) as con:
                    if json.loads(con.execute("SELECT value_json FROM settings WHERE key='profile'").fetchone()[0]) != 'real':
                        raise QAError('qa_permission_invalid')
                    if con.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','retry_wait')").fetchone():
                        raise QAError('qa_prior_run_invalid')
                store = Store(db_path, 'real', wal=True)
                runner = AnalysisRunner(store, schema_dir=ROOT/'schemas', prompt_dir=ROOT/'prompts',
                    config_path=ROOT/'config/ai-runtime.json', report_path=ROOT/'docs/cli-validation.json')
                bundle = json.loads(PUBLIC_BUNDLE.read_text(encoding='utf-8'))
                result = run_reviewed(runner, bundle, permission)
                create_record(QA_ROOT, 'operator-receipt.json', result)
    except QAError as exc:
        result = {'status': 'blocked', 'code': str(exc)}
    except Exception:
        result = {'status': 'blocked', 'code': 'qa_helper_failed'}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
