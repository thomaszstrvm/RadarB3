"""Import a local calendar into an inactive database; real requires the pinned package."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend  # Preload the verified SQLite runtime before importing sqlite3.
import sqlite3
from backend.market_calendar import import_calendar, import_calendar_package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--profile', choices=('demo','real'), default='demo')
    parser.add_argument('--sources-dir', type=Path)
    args = parser.parse_args()
    try:
        if args.input.stat().st_size > 2_000_000:
            raise ValueError('calendar_file_too_large')
        document = json.loads(args.input.read_text(encoding='utf-8-sig'))
        # mode=rw refuses missing destinations; this utility never initializes Store.
        with sqlite3.connect(args.db.resolve().as_uri() + '?mode=rw', uri=True) as connection:
            if document.get('schema_version') == 2:
                if args.sources_dir is None:
                    raise ValueError('calendar_sources_directory_required')
                result = import_calendar_package(connection, document, args.sources_dir, expected_profile=args.profile)
            else:
                if args.profile != 'demo':
                    raise ValueError('calendar_reviewed_package_required_for_real')
                result = import_calendar(connection, document)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error) as exc:
        code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('calendar_') else 'calendar_import_failed'
        parser.exit(2, f'Calendar import refused: {code}\n')


if __name__ == '__main__':
    main()
