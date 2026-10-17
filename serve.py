#!/usr/bin/env python3
"""Compatibility shim — v0 layout kept working.

Prefer the installed CLI: ``opencode-ledger serve [--port N]``.
Usage: python3 serve.py [port]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opencode_ledger.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["serve"] + sys.argv[1:]))
