"""Unit/integration tests must never launch an actual Codex executable."""
from pathlib import Path
import subprocess
import pytest


@pytest.fixture(autouse=True)
def prohibit_real_codex_process(monkeypatch):
    original = subprocess.Popen

    def guarded(args, *positional, **kwargs):
        executable = kwargs.get('executable') or (args[0] if isinstance(args, (list, tuple)) else args)
        if 'codex' in Path(str(executable)).name.casefold():
            raise AssertionError('Real Codex subprocess forbidden in automated tests; inject a fake executor')
        return original(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', guarded)
    # execute_bounded captures Popen at import time; protect that reference too.
    from backend.cli_process import execute_bounded
    monkeypatch.setitem(execute_bounded.__kwdefaults__, 'popen_factory', guarded)
