"""Offline release tests, not an OS sandbox. Run only reviewed test code.

Allowed Python children do not inherit the audit hook. No real AI is permitted.
"""
from __future__ import annotations
import argparse
import ipaddress
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
OPERATIONAL = (
    'test_release_validation.py', 'test_operational_diagnostics.py',
    'test_backup.py', 'test_backup_integrity.py', 'test_backup_recovery_cycle.py',
    'test_manual_windows_recovery.py',
    'test_manual_cache_guard.py', 'test_collection_mode.py',
    'test_manual_analysis_mode.py', 'test_cli_schema_diagnostics.py',
    'test_validation_diagnostics.py', 'test_legacy_scripts.py', 'test_qa_guard.py',
)


def create_output(value):
    """Reserve a NEW directory atomically, never reuse previous results."""
    path = Path(value).expanduser().absolute()
    if path.exists() or path.is_symlink():
        raise ValueError('output-dir must be a NEW directory')
    if not path.parent.is_dir():
        raise ValueError('output-dir parent must already exist')
    path.mkdir(exist_ok=False)
    return path.resolve()


class Boundary:
    def __init__(self, output):
        self.output = Path(output).resolve()
        self.pythons = {Path(sys.executable).resolve(), Path(sys._base_executable).resolve()}
        self.counts = dict(subprocesses=0, sqlite_connections=0, blocked=0)
        self.processes = set()

    def within(self, path):
        try:
            return Path(os.fsdecode(path)).resolve().is_relative_to(self.output)
        except (TypeError, ValueError, OSError):
            return False

    def deny(self, reason):
        self.counts['blocked'] += 1
        raise PermissionError('Release validation boundary: ' + reason)

    def writable(self, path):
        if not self.within(path):
            self.deny('mutation outside output-dir')

    def audit(self, event, args):
        if event == 'subprocess.Popen':
            executable = args[0]
            # Windows audits the already quoted command line with executable=None.
            if executable is None:
                command = args[1]
                parts = shlex.split(command, posix=False) if isinstance(command, str) else command
                executable = parts[0].strip('"') if parts else ''
            if Path(os.fsdecode(executable)).resolve() not in self.pythons:
                self.deny('only installed Python test interpreters allowed; no shell or AI')
            self.counts['subprocesses'] += 1
            self.processes.add(str(Path(executable).resolve()))
        elif event in {'os.system', 'os.exec', 'os.posix_spawn', 'os.spawn', 'os.startfile', 'os.startfile/2'}:
            self.deny('alternate process launch forbidden')
        elif event == 'sqlite3.connect':
            name = os.fsdecode(args[0])
            if name.startswith('file:'):
                parsed = urlsplit(name)
                if parsed.netloc not in {'', 'localhost'}:
                    self.deny('remote SQLite URI')
                name = unquote(parsed.path)
                if os.name == 'nt' and len(name) > 2 and name[0] == '/' and name[2] == ':':
                    name = name[1:]
            if name != ':memory:':
                self.writable(name)
            self.counts['sqlite_connections'] += 1
        elif event in {'socket.connect', 'socket.connect_ex', 'socket.bind', 'socket.sendto'}:
            address = args[-1]
            try:
                local = isinstance(address, tuple) and ipaddress.ip_address(address[0]).is_loopback
            except ValueError:
                local = False
            if not local:
                self.deny('external networking forbidden')
        elif event == 'socket.getaddrinfo':
            host = args[0]
            try:
                local = host in {'localhost', b'localhost', None} or ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = False
            if not local:
                self.deny('external DNS forbidden')
        elif event == 'open':
            path, mode, flags = args
            if isinstance(path, (str, bytes, os.PathLike)) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                self.writable(path)
        elif event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.utime', 'os.truncate'}:
            self.writable(args[0])
            if event in {'os.remove', 'os.rmdir'} and len(args) > 1 and args[1] not in {-1, None}:
                self.deny('dir_fd mutation unsupported')
        elif event in {'os.rename', 'os.replace', 'os.link', 'os.symlink'}:
            self.writable(args[0])
            self.writable(args[1])
            if event in {'os.link', 'os.symlink'}:
                self.deny('links unnecessary for release tests')
            if any(fd not in {-1, None} for fd in args[2:]):
                self.deny('dir_fd mutation unsupported')


def select_tests(mode, root=ROOT):
    if mode == 'full':
        return [str(root / 'tests')]
    return [str(root / 'tests' / name) for name in OPERATIONAL if (root / 'tests' / name).is_file()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('full', 'operational'), default='operational')
    parser.add_argument('--output-dir', required=True, help='New directory with existing parent')
    args = parser.parse_args(argv)
    try:
        output = create_output(args.output_dir)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    os.environ.pop('PYTEST_ADDOPTS', None)
    os.environ.pop('PYTEST_PLUGINS', None)
    os.environ['RADAR_EXECUTION_MODE'] = 'analysis-enabled'  # simulations only
    temp = output / 'temp'
    temp.mkdir()
    os.environ['TEMP'] = os.environ['TMP'] = os.environ['TMPDIR'] = str(temp)
    tempfile.tempdir = str(temp)
    boundary = Boundary(output)
    sys.addaudithook(boundary.audit)
    selected = select_tests(args.mode)
    status = 3
    try:
        import pytest
        status = int(pytest.main([
            *selected, '-q', '-p', 'no:cacheprovider', '--tb=short',
            '--basetemp=' + str(output / 'pytest-temp'),
            '--junitxml=' + str(output / 'junit.xml'),
            '--log-file=' + str(output / 'pytest.log'),
        ]))
        return status
    finally:
        report = {
            'mode': args.mode, 'exit_code': status, 'repository': str(ROOT),
            'selected_tests': selected, 'counts': boundary.counts,
            'missing_optional_tests': [name for name in OPERATIONAL if not (ROOT / 'tests' / name).is_file()] if args.mode == 'operational' else [],
            'python_version': sys.version,
            'allowed_python_executables': sorted(map(str, boundary.pythons)),
            'observed_python_executables': sorted(boundary.processes),
            'real_ai_allowed': False, 'operational_config_changed': False,
            'limits': 'Audit hook, not OS sandbox. Reviewed Python children do not inherit it. Loopback allowed for test clients. No external network or real AI authorized.',
        }
        with (output / 'validation.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)


if __name__ == '__main__':
    raise SystemExit(main())
