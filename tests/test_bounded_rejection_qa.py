"""Synthetic executions only; no real authorization, model, network or database."""
import asyncio  # Windows subprocess classes load before the autouse Popen guard.
import copy
import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import bounded_rejection_qa as qa
from backend.cli_process import CLIError
from backend.core import QuotaExceeded, digest, dumps
from backend.ingest import upsert_catalog
from backend.timeutil import iso
from test_cli_runner import setup_runner, encode_events
from test_numeric_context_diagnostics import public_bundle, filing, analysis_with_claim
from test_vintage_bundle import analysis_for


def load_permission(directory, document):
    body = dumps(document).encode()
    path = directory / 'authorization.json'
    path.write_bytes(body)
    return qa.ReviewedRetry.load(path, hashlib.sha256(body).hexdigest())


@pytest.fixture
def prepared(setup_runner, public_bundle, monkeypatch, tmp_path):
    monkeypatch.setenv('RADAR_EXECUTION_MODE', 'manual-analysis')
    directory = tmp_path / 'bounded-qa'
    directory.mkdir()
    monkeypatch.setattr(qa, 'QA_ROOT', directory)
    runner, _, _, *_ = setup_runner
    upsert_catalog(runner.store, [{'symbol': 'VALE3', 'name': 'Isolated public fixture', 'assetType': 'stock'}])
    with runner.store.transaction() as con:
        con.execute('INSERT INTO evidence_bundles VALUES(?,?,?,?,?,?)', (
            public_bundle['bundle_id'], public_bundle['asset']['asset_id'], public_bundle['as_of'],
            digest(public_bundle), dumps(public_bundle), iso()))
    calls = []
    def reject(*args, **kwargs):
        envelope = json.loads(kwargs['stdin'].decode().rsplit('\n\n', 1)[1])
        value = analysis_with_claim(envelope['evidence_bundle'], 'Lucro de US$1254 milhões.')
        value['analysis_id'] = envelope['analysis_id']
        calls.append(kwargs['stdin'])
        return encode_events(value)
    runner.executor = reject
    with pytest.raises(CLIError, match='cli_analysis_validation_rejected'):
        runner.run(public_bundle, repair_limit=0)
    prior = runner.store.rows('SELECT * FROM analysis_runs')[0]
    now = datetime.now(timezone.utc)
    document = {'version': 1, 'scope': 'public_rejection_qa_v1',
        'retention_authorization_ref': 'synthetic_retention_consent', 'execution_authorization_ref': 'synthetic_execution_consent',
        'asset_id': public_bundle['asset']['asset_id'], 'bundle_id': public_bundle['bundle_id'],
        'bundle_sha256': digest(public_bundle), 'cache_key': prior['cache_key'], 'prior_run_id': prior['run_id'],
        'prior_attempt': 1, 'next_attempt': 2, 'issued_at': iso(now - timedelta(minutes=1)),
        'expires_at': iso(now + timedelta(minutes=30)), 'prompt_hash': prior['prompt_hash'],
        'schema_hash': prior['schema_hash'], 'executable_sha256': prior['executable_hash'],
        'code_pins': {name: hashlib.sha256((qa.ROOT/name).read_bytes()).hexdigest() for name in qa.VALIDATION_FILES}}
    permission = load_permission(directory, document)
    return runner, public_bundle, permission, calls, prior


def test_default_guard_and_capture_remain_disabled(prepared):
    runner, bundle, permission, calls, prior = prepared
    with pytest.raises(CLIError, match='cli_previous_attempt_requires_review'):
        runner.run(bundle, repair_limit=0)
    assert len(calls) == len(runner.store.rows('SELECT * FROM quota_ledger')) == 1
    assert not (permission.base/'rejected-claim.json').exists()


def test_reviewed_attempt_is_single_exact_key_and_capture_is_bounded(prepared):
    runner, bundle, permission, calls, prior = prepared
    old_quota = runner.store.rows('SELECT * FROM quota_ledger')[0]
    with pytest.raises(CLIError, match='cli_analysis_validation_rejected'):
        runner.run(bundle, repair_limit=0, reviewed_retry=permission)
    rows = runner.store.rows('SELECT * FROM analysis_runs ORDER BY attempt')
    assert rows[0] == prior and [r['attempt'] for r in rows] == [1, 2]
    assert rows[1]['cache_key'] == prior['cache_key'] and rows[1]['status'] == 'quarantined'
    assert calls[0] == calls[1] and len(calls) == 2
    assert runner.store.rows('SELECT * FROM quota_ledger WHERE reservation_id=?', (old_quota['reservation_id'],))[0] == old_quota
    assert all(row['status'] == 'consumed' for row in runner.store.rows('SELECT * FROM quota_ledger'))
    content = json.loads((permission.base/'rejected-claim.json').read_text(encoding='utf-8'))
    assert content['statement'] == 'Lucro de US$1254 milhões.'
    assert content['origin'] == 'rejected_model_statement' and content['value_validated'] is False
    assert content['lexical_exact'] is True and content['quantity_source'] == 'bounded_monetary_literal'
    assert content['issue']['numeric_reason'] == 'filing_period_unrecognized'
    assert not {'stdout', 'stderr', 'analysis', 'prompt'} & content.keys()
    audit = runner.store.rows("SELECT payload_json FROM audit_events WHERE event_type='analysis_validation'")
    assert all('Lucro de US$1254' not in row['payload_json'] for row in audit)
    with pytest.raises(CLIError, match='qa_prior_run_invalid'):
        runner.run(bundle, repair_limit=0, reviewed_retry=permission)
    assert len(calls) == 2 and len(runner.store.rows('SELECT * FROM quota_ledger')) == 2
    capture_hash = permission.capture_outcome['capture_sha256']
    qa.cleanup_capture(capture_hash)
    assert not (permission.base/'rejected-claim.json').exists()
    assert json.loads((permission.base/'cleanup-receipt.json').read_text(encoding='utf-8'))['capture_sha256'] == capture_hash


def test_global_attempt_two_returns_one_call_when_validated(prepared):
    runner, bundle, permission, calls, prior = prepared
    def accepted(*args, **kwargs):
        envelope = json.loads(kwargs['stdin'].decode().rsplit('\n\n', 1)[1])
        value = analysis_for(envelope['evidence_bundle'])
        value['analysis_id'] = envelope['analysis_id']
        calls.append(kwargs['stdin'])
        return encode_events(value)
    runner.executor = accepted
    result = runner.run(bundle, repair_limit=0, reviewed_retry=permission)
    assert result['calls'] == 1 and len(calls) == 2
    assert runner.store.rows('SELECT attempt FROM analysis_runs ORDER BY attempt') == [{'attempt': 1}, {'attempt': 2}]
    assert not (permission.base/'rejected-claim.json').exists()
    with pytest.raises(CLIError, match='qa_prior_run_invalid'):
        runner.run(bundle, reviewed_retry=permission)


@pytest.mark.parametrize('field', ['asset_id', 'bundle_id', 'bundle_sha256', 'cache_key', 'prompt_hash', 'schema_hash', 'executable_sha256', 'prior_run_id'])
def test_wrong_binding_never_reserves_again(prepared, field):
    runner, bundle, permission, calls, _ = prepared
    document = copy.deepcopy(permission.document)
    document[field] = '0'*64 if field in qa.HASH_FIELDS else 'wrong'
    permission = load_permission(permission.base, document)
    with pytest.raises(CLIError, match='qa_binding_mismatch|qa_prior_run_invalid'):
        runner.run(bundle, reviewed_retry=permission)
    assert len(calls) == len(runner.store.rows('SELECT * FROM quota_ledger')) == 1


@pytest.mark.parametrize('mutation,code', [('expired', 'qa_permission_expired'), ('code', 'qa_code_pin_mismatch'), ('used', 'qa_permission_already_used')])
def test_expiry_code_and_exclusive_claim_are_checked_before_quota(prepared, mutation, code):
    runner, bundle, permission, calls, _ = prepared
    document = copy.deepcopy(permission.document)
    if mutation == 'expired':
        now = datetime.now(timezone.utc)
        document.update(issued_at=iso(now-timedelta(hours=1)), expires_at=iso(now-timedelta(minutes=1)))
    elif mutation == 'code':
        document['code_pins']['backend/numeric_support.py'] = '0'*64
    else:
        (permission.base/'authorization-used.json').write_text('{}')
    permission = load_permission(permission.base, document)
    with pytest.raises(CLIError, match=code):runner.run(bundle, reviewed_retry=permission)
    assert len(calls) == len(runner.store.rows('SELECT * FROM quota_ledger')) == 1


@pytest.mark.parametrize('status', ['reserved', 'released', 'consumed', 'uncertain'])
def test_orphan_next_reservation_is_never_reused(prepared, status):
    runner, bundle, permission, calls, prior = prepared
    old = runner.store.rows('SELECT * FROM quota_ledger')[0]
    reservation = runner.store.reserve_quota('codex_cli', old['period'], 10, f"analysis:{prior['cache_key']}:2")
    if status != 'reserved':runner.store.settle_quota(reservation, status)
    with pytest.raises(CLIError, match='qa_next_reservation_exists'):
        runner.run(bundle, reviewed_retry=permission)
    assert len(calls) == 1 and len(runner.store.rows('SELECT * FROM quota_ledger')) == 2
    assert not (permission.base/'authorization-used.json').exists()


@pytest.mark.parametrize('status', ['reserved', 'released', 'uncertain'])
def test_prior_must_have_consumed_accounting(prepared, status):
    runner, bundle, permission, calls, prior = prepared
    with runner.store.transaction() as con:
        con.execute('UPDATE quota_ledger SET status=? WHERE reservation_id=?', (status, prior['reservation_id']))
    with pytest.raises(CLIError, match='qa_prior_consumption_invalid'):
        runner.run(bundle, reviewed_retry=permission)
    assert len(calls) == 1


def test_quota_exhaustion_burns_claim_without_call_or_new_reservation(prepared):
    runner, bundle, permission, calls, _ = prepared
    runner.store.set_setting('max_analyses_per_day', 1)
    with pytest.raises(QuotaExceeded):runner.run(bundle, reviewed_retry=permission)
    assert len(calls) == len(runner.store.rows('SELECT * FROM quota_ledger')) == 1
    assert (permission.base/'authorization-used.json').exists()
    with pytest.raises(CLIError, match='qa_permission_already_used'):
        runner.run(bundle, reviewed_retry=permission)


@pytest.mark.parametrize('mode,job', [('analysis-enabled', None), ('manual-analysis', 'job_from_worker')])
def test_no_nonmanual_or_worker_entry(prepared, monkeypatch, mode, job):
    runner, bundle, permission, calls, _ = prepared
    monkeypatch.setenv('RADAR_EXECUTION_MODE', mode)
    with pytest.raises(CLIError, match='qa_manual_scope_required'):
        runner.run(bundle, reviewed_retry=permission, job_id=job)
    assert len(calls) == 1


@pytest.mark.parametrize('statement,reason', [
    ('Lucro trimestral de US$1254 milhões.', 'quantity_not_found'),
    ('Lucro de US$1254 milhões.', 'filing_period_unrecognized'),
])
def test_unknown_short_monetary_literal_is_exact_but_not_validated(filing, statement, reason):
    from backend.numeric_support import numbers_supported, numeric_rejection_reason
    assert qa.sanitized_sentence(statement) == (statement, 'bounded_monetary_literal')
    assert not numbers_supported(statement, [filing], modern=True)
    assert numeric_rejection_reason(statement, [filing], modern=True) == reason


@pytest.mark.parametrize('statement', [
    'Lucro de US$119876.5432 milhões.', 'Lucro de US$000001.0000 milhões.',
    'Lucro de US$12345678 milhões.', 'Lucro de US$1e3 milhões.',
    'Lucro de US$1.255,0 milhões.', 'Lucro de 1254 milhões.',
    'Lucro de US$1254 milhões e 7.', 'Lucro de US$1254 milhões e US$1255 milhões.',
    'Lucro de US$１２５４ milhões.', 'Lucro de US$١٢٥٤ milhões.',
    'Lucro de US$1254\u200b milhões.', 'Lucro de US$1254\n milhões.',
    'Lucro de US$1254 milhões para Maria.', 'Lucro de US$1254 milhões CPF 12345678901.',
    'Lucro de US$1254 milhões telefone 11987654321.',
    'Lucro de US$1254 milhões email pessoa@example.test.',
    'Lucro de US$1254 milhões token sk-private-secret.',
    'Lucro de US$1254 milhões https://example.test.',
    'Lucro de US$1254 milhões C:\\Users\\private.',
    'Lucro de US$1254 milhões. ' * 40,
])
def test_sensitive_ambiguous_or_long_text_is_never_partially_retained(statement):
    with pytest.raises(qa.QAError):qa.sanitized_sentence(statement)


@pytest.mark.parametrize('failure', ['write', 'partial', 'receipt', 'partial_receipt'])
def test_capture_failures_keep_terminal_consumption_and_pending_cleanup(prepared, monkeypatch, failure):
    runner, bundle, permission, calls, _ = prepared
    original = qa.create_record
    def fail(base, name, value):
        if name == 'rejected-claim.json' and failure in {'write', 'partial'}:
            if failure == 'partial':(base/name).write_text('{"statement":"Lucro de US$1254 milhões."')
            raise qa.QAError('qa_write_failed')
        if name == 'capture-receipt.json' and failure in {'receipt', 'partial_receipt'}:
            if failure == 'partial_receipt':(base/name).write_text('{')
            raise OSError('PRIVATE_SENTINEL')
        return original(base, name, value)
    monkeypatch.setattr(qa, 'create_record', fail)
    with pytest.raises(CLIError, match='cli_analysis_validation_rejected'):
        runner.run(bundle, reviewed_retry=permission)
    assert runner.store.rows('SELECT status FROM analysis_runs WHERE attempt=2') == [{'status': 'quarantined'}]
    assert all(row['status'] == 'consumed' for row in runner.store.rows('SELECT * FROM quota_ledger'))
    assert permission.capture_outcome['retention_pending'] == (failure != 'write')
    assert 'PRIVATE_SENTINEL' not in dumps(permission.capture_outcome)
    with pytest.raises(CLIError):runner.run(bundle, reviewed_retry=permission)
    assert len(calls) == 2
    if failure != 'write':
        sha = hashlib.sha256((permission.base/'rejected-claim.json').read_bytes()).hexdigest()
        with pytest.raises(qa.QAError, match='qa_cleanup_hash_mismatch'):qa.cleanup_capture('0'*64)
        qa.cleanup_capture(sha)
        assert not (permission.base/'rejected-claim.json').exists()


def test_errors_are_fixed_enums_and_capture_file_is_ignored():
    assert str(qa.QAError('PRIVATE_SENTINEL')) == 'qa_failed'
    assert '/rejected-claim.json' in (qa.ROOT/'docs/qa-20261101/bounded-rejection-qa/.gitignore').read_text().splitlines()


def test_denied_sensitive_claim_is_not_retained_anywhere(prepared):
    runner, bundle, permission, calls, _ = prepared
    def unsafe(*args, **kwargs):
        envelope = json.loads(kwargs['stdin'].decode().rsplit('\n\n', 1)[1])
        value = analysis_with_claim(envelope['evidence_bundle'], 'Lucro de US$1254 milhões PRIVATE_SENTINEL.')
        value['analysis_id'] = envelope['analysis_id']
        calls.append(kwargs['stdin'])
        return encode_events(value)
    runner.executor = unsafe
    with pytest.raises(CLIError, match='cli_analysis_validation_rejected'):
        runner.run(bundle, reviewed_retry=permission)
    assert permission.capture_outcome['reason'] == 'qa_capture_character_blocked'
    assert permission.capture_outcome['retention_pending'] is False
    assert not (permission.base/'rejected-claim.json').exists()
    assert 'PRIVATE_SENTINEL' not in (permission.base/'capture-receipt.json').read_text(encoding='utf-8')
    assert 'PRIVATE_SENTINEL' not in dumps(runner.store.rows('SELECT * FROM audit_events'))


def test_concurrent_reviewed_calls_cannot_double_launch(prepared):
    from concurrent.futures import ThreadPoolExecutor
    runner, bundle, permission, calls, _ = prepared
    def invoke():
        try:runner.run(bundle, reviewed_retry=permission)
        except CLIError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke) for _ in range(2)]
        codes = [future.result(timeout=15) for future in futures]
    assert codes.count('cli_analysis_validation_rejected') == 1
    assert len(calls) == len(runner.store.rows('SELECT * FROM quota_ledger')) == 2
    assert len(runner.store.rows('SELECT * FROM analysis_runs')) == 2


def test_operator_cli_refuses_a_copied_database_without_opening_live(tmp_path, monkeypatch):
    path = qa.ROOT / 'docs/qa-20261101/bounded-rejection-qa/run_reviewed_retry.py'
    spec = importlib.util.spec_from_file_location('bounded_operator', path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    target = tmp_path/'designated.sqlite3'
    target.write_bytes(b'synthetic')
    copy_path = tmp_path/'copied.sqlite3'
    copy_path.write_bytes(b'synthetic')
    monkeypatch.setattr(helper, 'LIVE_DB', target)
    assert helper.checked_live_db(target) == target.absolute()
    with pytest.raises(qa.QAError, match='qa_binding_mismatch'):helper.checked_live_db(copy_path)


@pytest.mark.parametrize('change', ['same_authorization', 'unbounded_hours', 'extra_field'])
def test_permission_shape_is_not_a_generic_force_flag(prepared, change):
    _, _, permission, _, _ = prepared
    document = copy.deepcopy(permission.document)
    if change == 'same_authorization':document['execution_authorization_ref'] = document['retention_authorization_ref']
    elif change == 'unbounded_hours':document['expires_at'] = iso(datetime.now(timezone.utc)+timedelta(days=1))
    else:document['force'] = True
    with pytest.raises(qa.QAError, match='qa_permission_invalid'):load_permission(permission.base, document)


@pytest.mark.parametrize('currency', ['R$', 'BRL', 'US$', 'USD'])
@pytest.mark.parametrize('scale', ['milhões', 'bilhões'])
def test_explicit_currency_is_supported_without_claiming_source_value(currency, scale):
    # The archived filing coefficient is USD/millions. Equal digits under a
    # different currency or scale do not turn model text into a sourced fact.
    statement = f'Lucro de {currency}1255 {scale}.'
    assert qa.sanitized_sentence(statement) == (statement, 'bounded_monetary_literal')
    assert not hasattr(qa, 'public_financial_values')


def test_currency_prefix_does_not_add_arbitrary_r_word():
    with pytest.raises(qa.QAError, match='qa_capture_vocabulary_blocked'):
        qa.sanitized_sentence('R lucro de US$1254 milhões.')


@pytest.mark.parametrize('mutation,code', [
    ('binary', 'cli_binary_changed_requires_revalidation'),
    ('binary_and_report', 'qa_configuration_changed'),
    ('runtime', 'qa_configuration_changed'),
    ('report', 'qa_configuration_changed'),
])
def test_actual_configuration_is_revalidated_after_mutex_acquisition(prepared, mutation, code):
    from contextlib import contextmanager
    runner, bundle, permission, calls, prior = prepared
    before_ledger = runner.store.rows('SELECT * FROM quota_ledger')
    @contextmanager
    def changed_while_waiting():
        report = json.loads(runner.report_path.read_text(encoding='utf-8'))
        binary = qa.Path(report['executable'])
        if mutation in {'binary', 'binary_and_report'}:
            binary.write_bytes(b'synthetic changed binary; never executed')
        if mutation == 'binary_and_report':
            report['executable_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
            runner.report_path.write_text(dumps(report), encoding='utf-8')
        elif mutation == 'report':
            report['version'] = 'synthetic changed version'
            runner.report_path.write_text(dumps(report), encoding='utf-8')
        elif mutation == 'runtime':
            runtime = json.loads(runner.config_path.read_text(encoding='utf-8'))
            runtime['timeout_seconds'] = 25
            runner.config_path.write_text(dumps(runtime), encoding='utf-8')
        yield
    runner.mutex_factory = changed_while_waiting
    with pytest.raises(CLIError, match=code):
        runner.run(bundle, reviewed_retry=permission)
    assert len(calls) == 1
    assert runner.store.rows('SELECT * FROM quota_ledger') == before_ledger
    assert runner.store.rows('SELECT * FROM analysis_runs') == [prior]
    assert not (permission.base/'authorization-used.json').exists()
