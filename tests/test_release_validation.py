"""Boundary probes do not launch commands or contact networks."""
import importlib.util
import os
from pathlib import Path
import sys
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location('release_validation', Path(__file__).resolve().parents[1] / 'scripts/validate_release.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_output_never_reused(tmp_path):
    output = runner.create_output(tmp_path / 'new')
    marker = output / 'evidence.txt'
    marker.write_text('preserve', encoding='utf-8')
    with pytest.raises(ValueError, match='NEW'):
        runner.create_output(output)
    assert marker.read_text() == 'preserve'


def test_output_requires_existing_parent(tmp_path):
    with pytest.raises(ValueError, match='parent'):
        runner.create_output(tmp_path / 'missing' / 'new')
    assert not (tmp_path / 'missing').exists()


@pytest.mark.parametrize('event,args', [
    ('subprocess.Popen', ('codex.exe', [], None, None)),
    ('subprocess.Popen', (None, 'codex exec', None, None)),
    ('subprocess.Popen', ('cmd.exe', [], None, None)),
    ('os.system', ('echo unsafe',)),
    ('socket.connect', (None, ('203.0.113.1', 443))),
    ('socket.connect_ex', (None, ('example.org', 443))),
    ('socket.sendto', (None, b'x', ('203.0.113.1', 443))),
    ('socket.getaddrinfo', ('example.org', 443, 0, 0, 0)),
    ('sqlite3.connect', ('file://remote/db.sqlite',)),
])
def test_unsafe_events_blocked(tmp_path, event, args):
    boundary = runner.Boundary(tmp_path)
    with pytest.raises(PermissionError):
        boundary.audit(event, args)
    assert boundary.counts['blocked'] == 1


@pytest.mark.parametrize('kind', ['open', 'sqlite', 'sqlite_uri', 'mkdir', 'remove', 'rename', 'symlink'])
def test_outside_output_blocked(tmp_path, kind):
    boundary = runner.Boundary(tmp_path / 'output')
    outside = tmp_path / 'outside.sqlite'
    events = {
        'open': ('open', (str(outside), 'w', os.O_WRONLY | os.O_CREAT)),
        'sqlite': ('sqlite3.connect', (str(outside),)),
        'sqlite_uri': ('sqlite3.connect', (outside.as_uri() + '?mode=ro',)),
        'mkdir': ('os.mkdir', (str(outside), 0o700, -1)),
        'remove': ('os.remove', (str(outside), -1)),
        'rename': ('os.rename', (str(boundary.output / 'x'), str(outside), -1, -1)),
        'symlink': ('os.symlink', (str(outside), str(boundary.output / 'x'), -1)),
    }
    with pytest.raises(PermissionError):
        boundary.audit(*events[kind])
    assert not outside.exists()


def test_allowed_operations_and_two_current_interpreters(tmp_path):
    boundary = runner.Boundary(tmp_path)
    boundary.audit('open', (str(tmp_path / 'x'), 'w', os.O_WRONLY))
    boundary.audit('sqlite3.connect', (str(tmp_path / 'db.sqlite'),))
    boundary.audit('sqlite3.connect', ((tmp_path / 'db.sqlite').as_uri() + '?mode=rw',))
    boundary.audit('sqlite3.connect', (':memory:',))
    boundary.audit('socket.connect', (None, ('127.0.0.1', 1234)))
    for executable in {sys.executable, sys._base_executable}:
        boundary.audit('subprocess.Popen', (executable, [], None, None))
        boundary.audit('subprocess.Popen', (None, subprocess.list2cmdline([executable, '-B', 'benign.py']), None, None))
    assert boundary.counts['sqlite_connections'] == 3
    assert boundary.counts['blocked'] == 0


def test_selection_fixed_and_only_existing(tmp_path):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_release_validation.py').touch()
    (tests / 'test_arbitrary.py').touch()
    assert runner.select_tests('operational', tmp_path) == [str(tests / 'test_release_validation.py')]
    assert runner.select_tests('full', tmp_path) == [str(tests)]


def test_import_does_not_install_hook_or_create_store():
    source = Path(runner.__file__).read_text(encoding='utf-8')
    assert 'from backend' not in source
    assert runner.ROOT == Path(__file__).resolve().parents[1]
