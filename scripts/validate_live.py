"""Read-only local observation: no enqueue, worker, inference or collector calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend  # Load the verified SQLite DLL before sqlite3.
import sqlite3
from backend.timeutil import iso

READ_PATHS = ('/health/live', '/operations')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_get(base, path):
    if path not in READ_PATHS:
        raise ValueError('read_endpoint_not_allowed')
    parts = urlsplit(base)
    if (parts.scheme != 'http' or parts.hostname != '127.0.0.1' or parts.username
            or parts.password or parts.path != '/api/v1' or parts.query or parts.fragment):
        raise ValueError('local_api_required')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(base + path, method='GET')
    with opener.open(request, timeout=10) as response:
        return json.load(response)


def database_snapshot(path):
    """Short consistent snapshot, using no Store initialization or migration."""
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        con.execute('PRAGMA query_only=ON')
        con.execute('BEGIN')
        ledger = con.execute("SELECT reservation_id,period,units,status FROM quota_ledger WHERE provider='codex_cli' ORDER BY reservation_id").fetchall()
        has_runs = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='analysis_runs'").fetchone()
        runs = con.execute('SELECT run_id,status,started_at,finished_at,reservation_id FROM analysis_runs ORDER BY run_id').fetchall() if has_runs else []
        fingerprint = lambda rows: hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        return {'observed_at': iso(), 'analysis_count': con.execute('SELECT COUNT(*) FROM analyses').fetchone()[0],
                'codex_ledger': {'rows': len(ledger), 'units_not_released': sum(row[2] for row in ledger if row[3] != 'released'), 'sha256': fingerprint(ledger)},
                'analysis_runs': {'table_present': bool(has_runs), 'rows': len(runs), 'sha256': fingerprint(runs)}}
    finally:
        con.close()


def validate_live(database, *, base='http://127.0.0.1:8765/api/v1', get=local_get):
    before = database_snapshot(database)
    api = {}
    for path in READ_PATHS:
        try:
            api[path] = {'response': get(base, path)}
        except (OSError, ValueError) as exc:
            # Do not copy arbitrary response/error bodies into reports.
            api[path] = {'error': type(exc).__name__}
    after = database_snapshot(database)
    ledger_changed = before['codex_ledger'] != after['codex_ledger']
    runs_changed = before['analysis_runs'] != after['analysis_runs']
    return {'as_of': iso(), 'check_kind': 'read_only_observation', 'api': api,
            'before': before, 'after': after,
            'inference_observation': {
                'codex_ledger_changed': ledger_changed, 'analysis_runs_changed': runs_changed,
                'result': 'activity_or_state_change_observed' if ledger_changed or runs_changed else 'no_change_observed',
                'limitation': 'Somente registros locais no intervalo observado; não prova ausência de inferência fora dele nem atribui mudanças a este processo.'}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--base', default='http://127.0.0.1:8765/api/v1')
    args = parser.parse_args(argv)
    result = validate_live(args.db, base=args.base)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if all('error' not in item for item in result['api'].values()) else 2


if __name__ == '__main__':
    raise SystemExit(main())
