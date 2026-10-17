#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 extract.py
echo
echo "Live (auto-refreshing): python3 serve.py         →  http://localhost:8765/"
echo "Demo (no database):     python3 serve.py --demo  →  http://localhost:8765/"
