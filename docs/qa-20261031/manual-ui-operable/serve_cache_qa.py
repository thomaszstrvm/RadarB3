"""Reviewed QA launcher: normal run.py/API, guarded worker entrypoint only."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import run


def guarded_spawn(original, args, **kwargs):
    api = [sys.executable, '-m', 'uvicorn', 'backend.api:create_app', '--factory',
           '--host', '127.0.0.1', '--port', '8765', '--no-access-log']
    worker = [sys.executable, '-m', 'backend.worker', '--db', str(ROOT/'data/real/radar.sqlite3'),
              '--profile', 'real', '--execution-mode', 'manual-analysis']
    if args == api:
        return original(args, **kwargs)
    if args == worker:
        guarded = [args[0], str(Path(__file__).parent/'cache_only_worker.py'), *args[3:]]
        return original(guarded, **kwargs)
    raise RuntimeError('qa_unexpected_child_command')


if __name__ == '__main__':
    if sys.argv[1:] != ['--serve-reviewed-cache-only']:
        raise SystemExit('Explicit QA flag required; no services started.')
    if not (Path(__file__).parent/'cache-reviewed.json').is_file():
        raise SystemExit('Reviewed cache manifest missing; no services started.')
    original = run.subprocess.Popen
    def spawn(args,**kwargs):
        return guarded_spawn(original, args, **kwargs)
    run.subprocess.Popen = spawn
    sys.argv = ['run.py','--mode','real','--execution-mode','manual-analysis']
    try:
        run.main()
    finally:
        run.subprocess.Popen = original
