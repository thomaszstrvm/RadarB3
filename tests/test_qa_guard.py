"""Exercise the installed suite guard with a nonexistent CLI sentinel."""
import subprocess
import pytest


@pytest.mark.parametrize('args,kwargs', [
    (['C:/QA-NOT-A-REAL-PATH/codex.exe', '--version'], {}),
    (['ignored'], {'executable': 'C:/QA-NOT-A-REAL-PATH/codex.exe'}),
])
def test_real_codex_launcher_is_denied_by_suite_guard(args, kwargs):
    assert subprocess.Popen.__name__ == 'guarded'
    with pytest.raises(AssertionError, match='Real Codex subprocess forbidden'):
        subprocess.Popen(args, **kwargs)


def test_captured_transport_default_is_also_guarded(tmp_path):
    from backend.cli_process import execute_bounded
    from backend.cli_runner import AnalysisRunner
    assert AnalysisRunner.__init__.__kwdefaults__["executor"] is execute_bounded
    assert execute_bounded.__kwdefaults__["popen_factory"] is subprocess.Popen
    with pytest.raises(AssertionError, match="Real Codex subprocess forbidden"):
        execute_bounded(["C:/QA-NOT-A-REAL-PATH/codex.exe"], stdin=b"", cwd=tmp_path, env={})
