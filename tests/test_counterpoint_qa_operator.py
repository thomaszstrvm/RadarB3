"""Inactive operator composition: temporary stores, fake process, no live access."""
import asyncio  # Before conftest replaces subprocess.Popen on Windows.
import copy
import hashlib
import importlib.util
import json
import shutil
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest

from backend.cli_process import CLIError, ProcessResult
from backend.core import digest, dumps
from backend.ingest import upsert_catalog
from backend.timeutil import instant, iso, SAO_PAULO
from test_cli_runner import encode_events, setup_runner
from test_numeric_context_diagnostics import public_bundle, analysis_with_claim
from test_vintage_bundle import analysis_for

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT/'docs/qa-20261101/bounded-rejection-qa/counterpoint-contract/run_bounded_counterpoint.py'


@pytest.fixture
def case(setup_runner, public_bundle, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('counterpoint_operator_test', HELPER)
    op = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(op)
    runner, _, calls, config, report, exe = setup_runner
    sandbox = tmp_path/'operator'
    for name in op.PINS:
        target = sandbox/name
        target.parent.mkdir(parents=True, exist_ok=True)
        source = ROOT/name
        if source.exists():
            shutil.copyfile(source, target)
        else:
            assert name == op.REL+'/.gitignore'
            target.write_text('/next-attempt/rejected-candidate.json\n', encoding='utf-8')
    for source in (ROOT/'schemas').glob('*.json'):
        shutil.copyfile(source, sandbox/'schemas'/source.name)
    shutil.copyfile(config, sandbox/'config/ai-runtime.json')
    shutil.copyfile(report, sandbox/'docs/cli-validation.json')
    (sandbox/'prompts/analysis-signal-v1.2.txt').write_text(
        (ROOT/'prompts/analysis-signal-v1.2.txt').read_text(encoding='utf-8')+
        '\nTeste sintético isolado: identifique contrapontos como hipóteses condicionais.\n', encoding='utf-8')
    monkeypatch.setattr(op, 'ROOT', sandbox)
    monkeypatch.setattr(op, 'BASE', sandbox/op.REL/'next-attempt')
    monkeypatch.setattr(op, 'LIVE_DB', runner.store.path)
    op.BASE.mkdir()
    runner.prompt_dir, runner.schema_dir = sandbox/'prompts', sandbox/'schemas'
    runner.config_path, runner.report_path = sandbox/'config/ai-runtime.json', sandbox/'docs/cli-validation.json'
    monkeypatch.setenv('RADAR_EXECUTION_MODE', 'manual-analysis')
    upsert_catalog(runner.store, [{'symbol':'VALE3','name':'Public fixture','assetType':'stock'}])
    with runner.store.transaction() as con:
        con.execute('INSERT INTO evidence_bundles VALUES(?,?,?,?,?,?)',
                    (public_bundle['bundle_id'], 'brapi:VALE3', public_bundle['as_of'], digest(public_bundle), dumps(public_bundle), iso()))
    runner._initialize_runs()
    approved = op.identity(runner, public_bundle)
    period = instant(iso()).astimezone(SAO_PAULO).date().isoformat()
    reservation = runner.store.reserve_quota('codex_cli', period, 10, 'analysis:'+op.PREDECESSOR_KEY+':1')
    runner.store.settle_quota(reservation, 'consumed')
    with runner.store.transaction() as con:
        con.execute('INSERT INTO analysis_runs(run_id,cache_key,analysis_id,bundle_id,model,prompt_hash,schema_hash,cli_version,executable_hash,attempt,reservation_id,started_at,finished_at,status,error_code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (op.PREDECESSOR, op.PREDECESSOR_KEY, 'analysis_predecessor', op.BUNDLE_ID, op.MODEL,
             '0'*64, approved['schema_hash'], 'test', approved['executable_sha256'], 1, reservation,
             iso(), iso(), 'quarantined', 'cli_analysis_validation_rejected'))
    state = {'held': False, 'enters': 0, 'exits': 0, 'calls': 0, 'field': 'counterpoint'}

    @contextmanager
    def mutex():
        assert not state['held']
        state.update(held=True, enters=state['enters']+1)
        try:
            if state['enters'] == 1:
                assert len(runner.store.rows('SELECT * FROM quota_ledger')) == 1
            yield
        finally:
            state.update(held=False, exits=state['exits']+1)

    def executor(args, **kwargs):
        assert state['held'] and (op.BASE/'authorization-used.json').is_file()
        assert len(runner.store.rows('SELECT * FROM quota_ledger')) == 2
        state['calls'] += 1
        envelope = json.loads(kwargs['stdin'].decode().rsplit('\n\n', 1)[1])
        statement = state.get('statement', 'Lucro trimestral de US$ 999999 milhões.')
        value = analysis_with_claim(public_bundle, statement) if state['field'] == 'claim' else analysis_for(public_bundle)
        value['analysis_id'] = envelope['analysis_id']
        if state['field'] == 'counterpoint':
            refs = [next(item['evidence_id'] for item in public_bundle['evidence'] if item['record_type'] == 'filing')]
            value['signal']['counterpoints'] = [statement]
            value['signal']['evidence_ids'] = value['evidence_ids'] = refs
        if state['field'] == 'accepted':
            value = analysis_for(public_bundle)
            value['analysis_id'] = envelope['analysis_id']
        return encode_events(value)

    runner.mutex_factory, runner.executor = mutex, executor
    grant = {'version': 1, 'scope': 'counterpoint_public_first_attempt',
        'retention_authorization_ref': 'synthetic_retention', 'execution_authorization_ref': 'synthetic_execution',
        'asset_id': 'brapi:VALE3', 'bundle_id': op.BUNDLE_ID, 'bundle_sha256': op.BUNDLE_SHA,
        'predecessor_run_id': op.PREDECESSOR, 'model': op.MODEL, 'reasoning': op.REASONING_EFFORT,
        'issued_at': iso(instant(iso())-timedelta(minutes=1)), 'expires_at': iso(instant(iso())+timedelta(minutes=30)),
        **{key: approved[key] for key in ('cache_key','prompt_hash','schema_hash','executable_sha256')},
        'code_pins': {name: hashlib.sha256((sandbox/name).read_bytes()).hexdigest() for name in op.PINS}}

    def write_grant():
        body = dumps(grant).encode()
        (op.BASE/'authorization.json').write_bytes(body)
        return hashlib.sha256(body).hexdigest()

    return op, runner, public_bundle, state, grant, write_grant


def run(case, **kwargs):
    op, runner, bundle, _, _, write = case
    return op.run_bounded(runner, bundle, write(), **kwargs)


def assert_no_attempt(case):
    op, runner, _, state, *_ = case
    assert state['calls'] == 0
    assert len(runner.store.rows('SELECT * FROM quota_ledger')) == 1
    assert len(runner.store.rows('SELECT * FROM analysis_runs')) == 1
    assert not (op.BASE/'authorization-used.json').exists()


@pytest.mark.parametrize('field,path', [('claim','/claims/0/statement'), ('counterpoint','/signal/counterpoints/0')])
def test_normal_runner_terminalizes_before_literal_capture(case, field, path):
    op, runner, bundle, state, *_ = case
    state['field'] = field
    original = runner.executor, runner.mutex_factory
    sanitizer = op.load_sanitizer(case[4])
    def checked(text, candidate):
        row = runner.store.rows('SELECT * FROM analysis_runs WHERE run_id<>?', (op.PREDECESSOR,))[0]
        assert row['status'] == 'quarantined' and row['finished_at']
        assert runner.store.rows('SELECT status FROM quota_ledger WHERE reservation_id=?', (row['reservation_id'],))[0]['status'] == 'consumed'
        assert not state['held']
        return sanitizer(text, candidate)
    result = run(case, sanitizer=checked)
    assert result['status'] == 'rejected' and result['capture']['status'] == 'captured', result
    assert result['calls'] == state['calls'] == state['enters'] == state['exits'] == 1
    assert (runner.executor, runner.mutex_factory) == original
    captured = json.loads((op.BASE/'rejected-candidate.json').read_text(encoding='utf-8'))
    assert captured['statement'] == 'Lucro trimestral de US$ 999999 milhões.'
    assert captured['issue']['path'] == path and captured['value_validated'] is False
    assert 'statement' not in dumps(json.loads((op.BASE/'operator-receipt.json').read_text()))
    second = run(case)
    assert second['status'] == 'blocked' and second['code'] == 'qa_key_used' and state['calls'] == 1
    assert len(runner.store.rows('SELECT * FROM analyses')) == 0


def test_accepted_output_never_captured(case):
    op, runner, _, state, *_ = case
    state['field'] = 'accepted'
    def forbidden(*args):
        pytest.fail('sanitizer must not inspect accepted output')
    result = run(case, sanitizer=forbidden)
    assert result['status'] == 'validated' and result['retention_pending'] is False
    assert len(runner.store.rows('SELECT * FROM analyses')) == 1
    assert not (op.BASE/'rejected-candidate.json').exists()


def test_label_only_unsupported_counterpoint_is_captured_without_quantity_gate(case):
    op, _, _, state, *_ = case
    state['statement'] = 'R63 ausente.'  # Synthetic probe; not a historical phrase.
    result = run(case)
    assert result['status'] == 'rejected' and result['capture']['status'] == 'captured', result
    captured = json.loads((op.BASE/'rejected-candidate.json').read_text(encoding='utf-8'))
    assert captured['issue']['numeric_reason'] == 'unsupported_format'
    assert captured['statement'] == 'R63 ausente.' and captured['literal_count'] == 0
    assert captured['fixed_label_count'] == 1 and captured['value_validated'] is False


@pytest.mark.parametrize('field,value', [
    ('model','other'), ('reasoning','high'), ('asset_id','brapi:OTHER3'), ('bundle_sha256','f'*64),
    ('predecessor_run_id','other_run'), ('cache_key','f'*64), ('prompt_hash','f'*64),
    ('schema_hash','f'*64), ('executable_sha256','f'*64), ('execution_authorization_ref','synthetic_retention'),
])
def test_bindings_fail_before_quota(case, field, value):
    op, runner, bundle, _, grant, write = case
    grant[field] = value
    try:
        result = op.run_bounded(runner, bundle, write())
        assert result['status'] == 'blocked'
    except op.OperatorError:
        pass
    assert_no_attempt(case)


@pytest.mark.parametrize('kind', ['expired','future','overlong'])
def test_time_bounds(case, kind):
    op, runner, bundle, _, grant, write = case
    now = instant(iso())
    grant['issued_at'] = iso(now + timedelta(minutes=5) if kind == 'future' else now-timedelta(hours=1))
    grant['expires_at'] = iso(now-timedelta(minutes=1) if kind == 'expired' else now+timedelta(hours=2))
    try:
        assert op.run_bounded(runner, bundle, write())['status'] == 'blocked'
    except op.OperatorError:
        pass
    assert_no_attempt(case)


@pytest.mark.parametrize('kind', ['jobs','profile','mode','copy','quota_limit','float_limit','prior_status','prior_consumption','existing_marker','existing_reservation','prior_key','cache'])
def test_preconditions_are_not_repaired(case, monkeypatch, tmp_path, kind):
    op, runner, _, _, grant, _ = case
    if kind == 'jobs':
        runner.store.enqueue('analyse', {'asset_id':'brapi:VALE3'})
    elif kind == 'profile':
        runner.store.profile = 'demo'
    elif kind == 'mode':
        monkeypatch.setenv('RADAR_EXECUTION_MODE', 'analysis-enabled')
    elif kind == 'copy':
        monkeypatch.setattr(op, 'LIVE_DB', tmp_path/'copy.sqlite3')
    elif kind == 'quota_limit':
        runner.store.set_setting('max_analyses_per_day', 9)
    elif kind == 'float_limit':
        runner.store.set_setting('max_analyses_per_day', 10.0)
    elif kind == 'prior_status':
        with runner.store.transaction() as con:
            con.execute("UPDATE analysis_runs SET status='started'")
    elif kind == 'prior_consumption':
        with runner.store.transaction() as con:
            con.execute("UPDATE quota_ledger SET status='uncertain'")
    elif kind == 'existing_marker':
        (op.BASE/'authorization-used.json').write_text('{}')
    elif kind == 'existing_reservation':
        runner.store.reserve_quota('codex_cli', '2026-11-01', 10, 'analysis:'+grant['cache_key']+':1')
    elif kind == 'prior_key':
        with runner.store.transaction() as con:
            columns = [item[1] for item in con.execute('PRAGMA table_info(analysis_runs)')]
            row = dict(runner.store.rows('SELECT * FROM analysis_runs')[0])
            row.update(run_id='synthetic_prior_same_key', cache_key=grant['cache_key'])
            con.execute('INSERT INTO analysis_runs('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')', tuple(row[name] for name in columns))
    else:
        with runner.store.transaction() as con:
            con.execute('INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?,?,?)', ('synthetic_cache','brapi:VALE3',op.BUNDLE_ID,
                iso(),op.MODEL,grant['prompt_hash'],grant['schema_hash'],'insufficient_data','{}',iso()))
    before = runner.store.rows('SELECT * FROM quota_ledger')
    result = run(case)
    assert result['status'] == 'blocked'
    assert runner.store.rows('SELECT * FROM quota_ledger') == before
    assert len(runner.store.rows('SELECT * FROM analysis_runs')) == (2 if kind == 'prior_key' else 1)


def test_expired_or_exhausted_grant_never_rearms(case):
    op, runner, _, state, *_ = case
    period = instant(iso()).astimezone(SAO_PAULO).date().isoformat()
    for number in range(9):
        runner.store.settle_quota(runner.store.reserve_quota('codex_cli', period, 10, 'synthetic-'+str(number)), 'consumed')
    # The instrumented fixture lock expected one reservation; replace only the
    # temporary test lock, not the production AnalysisMutex composition.
    @contextmanager
    def mutex():
        yield
    runner.mutex_factory = mutex
    result = run(case)
    assert result['code'] == 'qa_quota_exhausted' and state['calls'] == 0
    assert (op.BASE/'authorization-used.json').exists()
    assert run(case)['code'] == 'qa_already_used'
    assert len(runner.store.rows('SELECT * FROM quota_ledger')) == 10


@pytest.mark.parametrize('target', ['prompt','config','executable','grant'])
def test_change_while_waiting_for_lock_blocks_before_claim(case, target):
    op, runner, _, _, _, _ = case
    @contextmanager
    def mutex():
        if target == 'prompt':
            path = runner.prompt_dir/'analysis-signal-v1.2.txt'
        elif target == 'config':
            path = runner.config_path
        elif target == 'executable':
            path = Path(json.loads(runner.report_path.read_text())['executable'])
        else:
            path = op.BASE/'authorization.json'
        path.write_bytes(path.read_bytes()+b' ')
        yield
    runner.mutex_factory = mutex
    assert run(case)['status'] == 'blocked'
    assert_no_attempt(case)


def test_busy_mutex_never_consumes_grant(case):
    op, runner, *_ = case
    @contextmanager
    def mutex():
        raise CLIError('cli_analysis_already_running')
        yield
    runner.mutex_factory = mutex
    assert run(case)['status'] == 'blocked'
    assert_no_attempt(case)


def test_exclusive_marker_creation_race_does_not_reserve_quota(case, monkeypatch):
    op, runner, _, state, *_ = case
    original = op.record
    def competing_writer(name, payload):
        assert name == 'authorization-used.json'
        (op.BASE/name).write_text('{}')
        return original(name, payload)
    monkeypatch.setattr(op, 'record', competing_writer)
    assert run(case)['code'] == 'qa_already_used'
    assert state['calls'] == 0 and len(runner.store.rows('SELECT * FROM quota_ledger')) == 1
    assert (op.BASE/'authorization-used.json').read_text() == '{}'


@pytest.mark.parametrize('change', ['unknown_field','duplicate_field','missing_pin'])
def test_grant_shape_rejects_extra_duplicate_or_unpinned_fields(case, change):
    op, runner, bundle, _, grant, write = case
    if change == 'unknown_field': grant['capture_anything'] = True
    if change == 'missing_pin': grant['code_pins'].pop(next(iter(grant['code_pins'])))
    sha = write()
    if change == 'duplicate_field':
        path = op.BASE/'authorization.json'
        raw = path.read_bytes().replace(b'{', b'{"version":1,', 1)
        path.write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
    with pytest.raises(op.OperatorError, match='grant_invalid'):
        op.run_bounded(runner, bundle, sha)
    assert_no_attempt(case)


@pytest.mark.parametrize('tamper', ['stdin','args','schema','env','timeout','new_job'])
def test_actual_send_tampering_is_blocked_before_transport(case, monkeypatch, tamper):
    op, runner, _, state, *_ = case
    original_check = op.check_send
    def inspect(*args):
        argv, kwargs = args[-2:]
        if tamper == 'stdin': kwargs['stdin'] += b'PRIVATE_SENTINEL'
        if tamper == 'args': argv.append('--dangerous')
        if tamper == 'schema': (kwargs['cwd']/'analysis.schema.json').write_text('{}')
        if tamper == 'env': kwargs['env']['OPENAI_API_KEY'] = 'PRIVATE_SENTINEL'
        if tamper == 'timeout': kwargs['timeout'] += 1
        if tamper == 'new_job': runner.store.enqueue('analyse', {})
        original_check(*args)
    monkeypatch.setattr(op, 'check_send', inspect)
    result = run(case)
    assert result['status'] == 'blocked' and result['calls'] == state['calls'] == 0
    assert (op.BASE/'authorization-used.json').exists()
    row = runner.store.rows('SELECT * FROM analysis_runs WHERE run_id<>?', (op.PREDECESSOR,))[0]
    assert row['status'] == 'interrupted'
    assert runner.store.rows('SELECT status FROM quota_ledger WHERE reservation_id=?', (row['reservation_id'],))[0]['status'] == 'uncertain'
    assert 'PRIVATE_SENTINEL' not in dumps(result)


def test_crash_after_launch_keeps_usage_uncertain_and_restores_composition(case):
    op, runner, _, state, *_ = case
    def crash(*args, **kwargs):
        raise KeyboardInterrupt()
    runner.executor = crash
    original_mutex = runner.mutex_factory
    with pytest.raises(KeyboardInterrupt):
        run(case)
    assert not state['held'] and runner.mutex_factory == original_mutex and runner.executor == crash
    assert (op.BASE/'authorization-used.json').exists()
    assert json.loads((op.BASE/'operator-receipt.json').read_text())['code'] == 'qa_interrupted'
    row = runner.store.rows('SELECT * FROM analysis_runs WHERE run_id<>?', (op.PREDECESSOR,))[0]
    assert row['status'] == 'interrupted'
    assert runner.store.rows('SELECT status FROM quota_ledger WHERE reservation_id=?', (row['reservation_id'],))[0]['status'] == 'uncertain'


@pytest.mark.parametrize('kind', ['nonzero','protocol','finish_failure'])
def test_nonsemantic_or_nonterminal_result_cannot_capture(case, monkeypatch, kind):
    op, runner, *_ = case
    if kind == 'nonzero':
        runner.executor = lambda *a, **kw: ProcessResult(1, b'PRIVATE_SENTINEL', b'PRIVATE_SENTINEL')
    elif kind == 'protocol':
        runner.executor = lambda *a, **kw: ProcessResult(0, b'PRIVATE_SENTINEL', b'')
    else:
        monkeypatch.setattr(runner, '_finish_run', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('PRIVATE_SENTINEL')))
    result = run(case, sanitizer=lambda *a: pytest.fail('not eligible for capture'))
    assert result['status'] == 'blocked' and result['retention_pending'] is False
    assert 'PRIVATE_SENTINEL' not in dumps(result)


@pytest.mark.parametrize('kind', ['output_hash','audit','quota','refs'])
def test_terminal_proof_tampering_prevents_capture(case, monkeypatch, kind):
    op, runner, *_ = case
    original = op.capture
    def capture(*args):
        with runner.store.transaction() as con:
            if kind == 'output_hash': con.execute("UPDATE analysis_runs SET output_hash='bad' WHERE run_id<>?", (op.PREDECESSOR,))
            if kind == 'quota': con.execute("UPDATE quota_ledger SET status='uncertain' WHERE reservation_id NOT LIKE ?", ('analysis:'+op.PREDECESSOR_KEY+':%',))
            if kind in {'audit','refs'}:
                row = con.execute("SELECT rowid,payload_json FROM audit_events WHERE event_type='analysis_validation'").fetchone()
                payload = json.loads(row[1])
                if kind == 'audit': payload['job_id'] = 'unexpected'
                else: payload['diagnostic']['issues'][0]['evidence_ids'] = ['PRIVATE_SENTINEL']
                con.execute('UPDATE audit_events SET payload_json=? WHERE rowid=?', (dumps(payload), row[0]))
        return original(*args)
    monkeypatch.setattr(op, 'capture', capture)
    result = run(case)
    assert result['status'] == 'rejected' and result['capture']['status'] == 'blocked'
    assert not result['retention_pending'] and 'PRIVATE_SENTINEL' not in dumps(result)


@pytest.mark.parametrize('kind', ['raises','changes_text','extra_metadata','private_metadata'])
def test_sanitizer_fail_closed_no_raw_exception(case, kind):
    op, runner, *_ = case
    original = op.load_sanitizer(case[4])
    def sanitizer(text, bundle):
        if kind == 'raises': raise ValueError('PRIVATE_SENTINEL')
        literal, metadata = original(text, bundle)
        if kind == 'changes_text': literal += ' '
        if kind == 'extra_metadata': metadata['raw'] = 'PRIVATE_SENTINEL'
        if kind == 'private_metadata': metadata['grammar'] = 'PRIVATE_SENTINEL'
        return literal, metadata
    result = run(case, sanitizer=sanitizer)
    assert result['status'] == 'rejected' and result['capture']['status'] == 'blocked'
    assert not result['retention_pending'] and 'PRIVATE_SENTINEL' not in dumps(result)
    assert len(runner.store.rows('SELECT * FROM analysis_runs')) == 2


@pytest.mark.parametrize('code', ['candidate_number_uncovered','candidate_vocabulary_blocked','candidate_size_blocked','PRIVATE_SENTINEL'])
def test_only_static_candidate_exception_code_is_retained(case, code):
    op, *_ = case
    class Refusal(ValueError):
        pass
    def sanitizer(*args):
        error = Refusal('PRIVATE_MESSAGE_12345')
        error.code = code
        raise error
    result = run(case, sanitizer=sanitizer)
    assert result['status'] == 'rejected' and result['capture']['status'] == 'blocked'
    assert result['capture']['code'] == (code if code in op.CANDIDATE_CODES else 'qa_capture_failed')
    assert not (op.BASE/'rejected-candidate.json').exists()
    receipt = (op.BASE/'operator-receipt.json').read_text(encoding='utf-8')
    assert 'PRIVATE_' not in receipt


def test_capture_and_receipt_write_failure_does_not_change_terminal_run(case, monkeypatch):
    op, runner, *_ = case
    original = op.record
    def write(name, payload):
        if name == 'rejected-candidate.json':
            (op.BASE/name).write_bytes(b'partial-synthetic')
            raise OSError('PRIVATE_SENTINEL')
        if name == 'operator-receipt.json': raise OSError('PRIVATE_SENTINEL')
        return original(name, payload)
    monkeypatch.setattr(op, 'record', write)
    result = run(case)
    assert result['status'] == 'rejected' and result['retention_pending']
    assert result['receipt_code'] == 'qa_capture_receipt_failed'
    row = runner.store.rows('SELECT * FROM analysis_runs WHERE run_id<>?', (op.PREDECESSOR,))[0]
    assert row['status'] == 'quarantined'
    assert runner.store.rows('SELECT status FROM quota_ledger WHERE reservation_id=?', (row['reservation_id'],))[0]['status'] == 'consumed'
    assert (op.BASE/'authorization-used.json').exists()


def test_candidate_changed_during_inference_has_no_import_effect(case):
    op, runner, _, state, *_ = case
    original = runner.executor
    sentinel = op.BASE/'import-effect.txt'
    candidate = op.ROOT/op.REL/'capture_candidate.py'
    def executor(*args, **kwargs):
        result = original(*args, **kwargs)
        # Synthetic local adversary after successful send validation. The
        # mutated module must never execute, even though normal rejection ends.
        candidate.write_text('from pathlib import Path\nPath('+repr(str(sentinel))+').write_text("effect")\n', encoding='utf-8')
        return result
    runner.executor = executor
    result = run(case)
    assert result['status']=='rejected' and result['capture']=={'status':'blocked','code':'qa_pin_mismatch'}
    assert state['calls']==1 and not sentinel.exists() and not (op.BASE/'rejected-candidate.json').exists()
    row = runner.store.rows('SELECT * FROM analysis_runs WHERE run_id<>?', (op.PREDECESSOR,))[0]
    assert row['status']=='quarantined' and row['finished_at']
    assert runner.store.rows('SELECT status FROM quota_ledger WHERE reservation_id=?', (row['reservation_id'],))[0]['status']=='consumed'
    assert (op.BASE/'authorization-used.json').exists()


def test_sanitizer_compiles_exact_bytes_hashed_even_if_file_changes_after_read(case, monkeypatch):
    op, _, bundle, _, grant, _ = case
    candidate = op.ROOT/op.REL/'capture_candidate.py'
    sentinel = op.BASE/'import-effect.txt'
    original_read = op.read
    def read_then_change(path, *args, **kwargs):
        body = original_read(path, *args, **kwargs)
        if path == candidate:
            candidate.write_text('from pathlib import Path\nPath('+repr(str(sentinel))+').write_text("effect")\n', encoding='utf-8')
        return body
    monkeypatch.setattr(op, 'read', read_then_change)
    sanitizer = op.load_sanitizer(grant)
    assert sanitizer('R63 ausente.', bundle)[0]=='R63 ausente.'
    assert not sentinel.exists()


def test_exact_cleanup_preserves_used_marker_and_ignores_expiry(case, monkeypatch):
    op, *_ = case
    result = run(case)
    sha = result['capture']['capture_sha256']
    marker = (op.BASE/'authorization-used.json').read_bytes()
    with pytest.raises(op.OperatorError, match='cleanup_mismatch'):
        op.cleanup('f'*64)
    monkeypatch.setattr(op, 'iso', lambda: '2030-01-01T00:00:00Z')
    assert op.cleanup(sha)['status'] == 'deleted'
    assert not (op.BASE/'rejected-candidate.json').exists()
    assert (op.BASE/'authorization-used.json').read_bytes() == marker


def test_cli_inactive_missing_grant_and_copy_refusal(case, capsys, monkeypatch, tmp_path):
    op, _, _, state, _, write = case
    op.main(['run-approved-attempt', '--db', str(tmp_path/'copy.sqlite3'), '--authorization-sha256', 'f'*64])
    assert json.loads(capsys.readouterr().out)['status'] == 'blocked'
    sha = write()
    monkeypatch.setattr(op, 'Store', lambda *a, **kw: pytest.fail('must not open copy'))
    op.main(['run-approved-attempt', '--db', str(tmp_path/'copy.sqlite3'), '--authorization-sha256', sha])
    assert json.loads(capsys.readouterr().out)['code'] == 'qa_binding_mismatch'
    assert state['calls'] == 0 and not (op.BASE/'authorization-used.json').exists()


def test_reparse_parent_is_refused_without_following_it(case, monkeypatch):
    op, *_ = case
    actual = Path.lstat
    class Reparse:
        st_file_attributes = 0x400
        st_mode = 0o040755
    monkeypatch.setattr(Path, 'lstat', lambda path: Reparse() if path == op.BASE else actual(path))
    with pytest.raises(op.OperatorError, match='path_invalid'):
        op.regular_path(op.BASE, directory=True)
