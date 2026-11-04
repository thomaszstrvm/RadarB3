"""Import a pinned local release/map into an existing inactive DB (default demo)."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend  # Verified SQLite runtime; this does not initialize Store.
import sqlite3
from backend.fundamentals import import_fundamentals, MAX_MANIFEST_BYTES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--profile", choices=("demo", "real"), default="demo")
    args = parser.parse_args()
    try:
        with args.manifest.open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("fundamentals_manifest_too_large")
        document = json.loads(raw)
        # mode=rw refuses missing destinations. No Store initialization/migration.
        with sqlite3.connect(args.db.resolve().as_uri() + "?mode=rw", uri=True) as connection:
            result = import_fundamentals(connection, document, args.pdf, expected_profile=args.profile)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError, RecursionError, sqlite3.Error) as exc:
        # Do not expose input content, paths, or raw parser/SQLite exceptions.
        code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("fundamentals_") else "fundamentals_import_failed"
        parser.exit(2, f"Fundamentals import refused: {code}\n")


if __name__ == "__main__":
    main()
