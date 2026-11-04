import importlib.util
import json
from pathlib import Path

import pytest
from backend.cli_process import CLIError
from backend.core import digest
from test_cli_runner import setup_runner


PATH = Path(__file__).resolve().parents[1]/'docs/qa-20261031/manual-ui-operable/cache_guard.py'
spec = importlib.util.spec_from_file_location('qa_manual_cache_guard',PATH)
guard = importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)


def review(runner,bundle,analysis=None):
    return {**guard.identity(runner,bundle),'analysis_hash':digest(analysis) if analysis else 'not-approved'}


def test_guard_uses_real_cache_path_without_process_or_quota(setup_runner):
    runner,bundle,calls,*_ = setup_runner
    first = runner.run(bundle)
    approved = review(runner,bundle,first['analysis'])
    ledger = runner.store.rows('SELECT * FROM quota_ledger')
    result = guard.cached_only(runner,bundle,approved,runner.run,repair_limit=0)
    assert result['cache_reused'] and result['calls'] == 0
    assert len(calls) == 1 and runner.store.rows('SELECT * FROM quota_ledger') == ledger


@pytest.mark.parametrize('change',['missing_cache','bundle_hash','analysis_hash'])
def test_missing_or_unreviewed_cache_stops_before_reservation(setup_runner,change):
    runner,bundle,calls,*_ = setup_runner
    first = runner.run(bundle) if change != 'missing_cache' else None
    approved = review(runner,bundle,first['analysis'] if first else None)
    if change != 'missing_cache': approved[change] = 'different'
    before = runner.store.rows('SELECT * FROM quota_ledger')
    count = len(calls)
    with pytest.raises(CLIError,match='qa_cache_only_required'):
        guard.cached_only(runner,bundle,approved,runner.run)
    assert runner.store.rows('SELECT * FROM quota_ledger') == before and len(calls) == count


def test_late_cache_miss_cannot_reserve_even_after_initial_review(setup_runner):
    runner,bundle,calls,*_ = setup_runner
    first = runner.run(bundle)
    approved = review(runner,bundle,first['analysis'])
    before = runner.store.rows('SELECT * FROM quota_ledger')
    reserve, executor = runner.store.reserve_quota, runner.executor
    def unexpected_miss(*args,**kwargs):
        runner.store.reserve_quota('codex_cli','2026-10-31',10,'must-never-exist')
        pytest.fail('Reservation guard did not stop an unexpected miss')
    with pytest.raises(CLIError,match='qa_cache_reservation_forbidden'):
        guard.cached_only(runner,bundle,approved,unexpected_miss)
    assert runner.store.rows('SELECT * FROM quota_ledger') == before and len(calls) == 1
    assert runner.store.reserve_quota == reserve and runner.executor == executor


def launcher():
    path = PATH.parent/'serve_cache_qa.py'
    spec = importlib.util.spec_from_file_location('qa_manual_cache_launcher',path)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_qa_launcher_rewrites_only_exact_manual_worker():
    module = launcher()
    args = [module.sys.executable,'-m','backend.worker','--db',str(module.ROOT/'data/real/radar.sqlite3'),
            '--profile','real','--execution-mode','manual-analysis']
    calls = []
    module.guarded_spawn(lambda *a,**k:calls.append((a,k)),args,cwd=module.ROOT)
    assert len(calls) == 1
    assert calls[0][0][0] == [args[0],str(PATH.parent/'cache_only_worker.py'),*args[3:]]
    assert calls[0][1] == {'cwd':module.ROOT}


def test_qa_launcher_preserves_exact_local_api_command():
    module = launcher()
    args = [module.sys.executable,'-m','uvicorn','backend.api:create_app','--factory',
            '--host','127.0.0.1','--port','8765','--no-access-log']
    calls = []
    module.guarded_spawn(lambda *a,**k:calls.append(a),args)
    assert calls == [(args,)]


@pytest.mark.parametrize('args',[
    ['-m','backend.worker','--execution-mode','analysis-enabled'],
    ['-m','another.worker'], ['-m','uvicorn','backend.api:create_app','--host','0.0.0.0']])
def test_qa_launcher_rejects_unknown_or_changed_child_before_popen(args):
    module = launcher()
    calls = []
    with pytest.raises(RuntimeError,match='qa_unexpected_child_command'):
        module.guarded_spawn(lambda *a,**k:calls.append(a),[module.sys.executable,*args])
    assert not calls
