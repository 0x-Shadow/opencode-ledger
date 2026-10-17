"""Allow ``python3 -m opencode_ledger`` — same as the ``opencode-ledger`` CLI."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
