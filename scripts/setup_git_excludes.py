"""Install repository-local exclusions without changing the original specification."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BEGIN = b'# BEGIN RadarB3 managed local exclusions\n'
END = b'# END RadarB3 managed local exclusions\n'


def setup(root=ROOT):
    root = Path(root).resolve()
    def git_path(*args):
        output = subprocess.check_output(['git', '-C', str(root), 'rev-parse', *args], text=True).strip()
        return (root / output).resolve()
    if git_path('--show-toplevel') != root:
        raise ValueError('Run from a complete RadarB3 Git checkout')
    policy = (root / 'config/git-excludes').read_bytes().replace(b'\r\n', b'\n').rstrip(b'\n')
    block = BEGIN + policy + b'\n' + END
    target = git_path('--git-path', 'info/exclude')
    previous = target.read_bytes() if target.exists() else b''
    if BEGIN in previous or END in previous:
        if previous.count(BEGIN) != 1 or previous.count(END) != 1 or block not in previous:
            raise ValueError('Existing managed block differs; review it before updating')
        return False
    separator = b'\n' if previous and not previous.endswith(b'\n') else b''
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('ab') as stream:
        stream.write(separator + block)
    if target.read_bytes() != previous + separator + block:
        raise RuntimeError('Exclusion file readback differs; preserve it for review')
    return True


if __name__ == '__main__':
    print('Local exclusions installed.' if setup() else 'Local exclusions already match.')
