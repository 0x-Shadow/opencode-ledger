#!/usr/bin/env python3
"""Compatibility shim — v0 layout kept working.

Prefer the installed CLI: ``opencode-ledger extract [db] [out]``.
Writes stats.json next to this script when no output path is given.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opencode_ledger import extract as _extract  # noqa: E402

DEFAULT_DB = _extract.DEFAULT_DB
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "stats.json")

# Re-export for any existing importers.
parse_message = _extract.parse_message
aggregate = _extract.aggregate
load_session_meta = _extract.load_session_meta
read_rows = _extract.read_rows
run = _extract.run


def main(db_path=None, out_path=None):
    if not db_path:
        db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    if not out_path:
        out_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
    _extract.main(db_path, out_path)


if __name__ == "__main__":
    main()
