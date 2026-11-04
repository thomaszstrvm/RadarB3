"""Real Windows containment, using only owned benign Python processes, no AI."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import time

import pytest

from backend.cli_process import CLIError
from backend.cli_runner import sanitized_environment
from backend.core import Store
from backend.worker import Worker


def alive(pid):
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000,False,pid)  # SYNCHRONIZE only.
    if not handle:
        error = ctypes.get_last_error()
        if error == 87: return False  # No process with that PID.
        raise OSError(error,'Cannot verify owned process exit')
    try: return kernel.WaitForSingleObject(handle,0) == 258
    finally: kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != 'nt',reason='Windows Job Object and named mutex')
def test_abrupt_holder_exit_kills_benign_tree_before_manual_recovery(tmp_path):
    root = Path(__file__).resolve().parents[1]
    python = str(Path(sys._base_executable))
    child = tmp_path/'benign_child.py'
    child.write_text('''import json,os,subprocess,sys,time
from pathlib import Path
sys.stdin.buffer.read()
grandchild = subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(60)'],close_fds=True)
Path(sys.argv[1]).write_text(json.dumps({'child':os.getpid(),'grandchild':grandchild.pid}))
time.sleep(60)
''',encoding='utf-8')
    holder = tmp_path/'benign_holder.py'
    holder.write_text('''import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2])
from backend.cli_process import AnalysisMutex,execute_bounded
with AnalysisMutex():
    execute_bounded([sys.executable,'-B',sys.argv[3],sys.argv[4]],stdin=b'start',cwd=Path(sys.argv[3]).parent,env=dict(os.environ),timeout=45)
''',encoding='utf-8')
    marker = tmp_path/'owned-processes.json'
    process = subprocess.Popen([python,'-B',str(holder),str(root),sysconfig.get_path('purelib'),str(child),str(marker)],
                               cwd=tmp_path,env=sanitized_environment(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
    store = Store(tmp_path/'recovery.sqlite3')
    store.acquire_worker('old-manual',execution_mode='manual-analysis')
    job = store.enqueue('analyse',{'asset_id':'synthetic','manual_owner':'old-manual'})
    store.claim('old-manual',job_ids=[job['job_id']])
    store.reserve_quota('codex_cli','2026-10-31',10,'synthetic-reserved')
    with store.transaction() as con:
        con.execute("UPDATE worker_lease SET expires_at='2000-01-01T00:00:00Z'")
    before_job = store.get_job(job['job_id'])
    before_quota = store.rows('SELECT * FROM quota_ledger')
    fresh = Worker(Store(store.path),execution_mode='manual-analysis')
    try:
        deadline = time.monotonic()+10
        while not marker.exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.05)
        assert marker.exists(), 'Benign contained subtree did not become ready'
        pids = json.loads(marker.read_text())
        assert all(alive(pid) for pid in pids.values())
        with pytest.raises(CLIError,match='cli_analysis_already_running'):
            fresh.start()
        assert store.get_job(job['job_id']) == before_job
        # Kill only the Popen handle we created. Kernel closes its Job Object;
        # no global PID search or taskkill is used to clean up descendants.
        process.kill();process.wait(timeout=5)
        deadline = time.monotonic()+5
        while any(alive(pid) for pid in pids.values()) and time.monotonic()<deadline:
            time.sleep(.05)
        assert all(not alive(pid) for pid in pids.values())
        fresh.start()
        assert store.get_job(job['job_id'])['error_code'] == 'manual_session_interrupted'
        assert store.get_job(job['job_id'])['status'] == 'blocked'
        assert store.rows('SELECT * FROM quota_ledger') == before_quota
        assert not fresh.run_once(job_ids=[job['job_id']])
    finally:
        if process.poll() is None: process.kill()
        process.wait(timeout=5)
        process.stdout.close();process.stderr.close()
        fresh.close()
