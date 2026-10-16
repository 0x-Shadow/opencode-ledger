#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 extract.py
echo
echo "Live (auto-refreshing): python3 serve.py  →  http://localhost:8765/"
echo "Static:                 python3 -m http.server 8765  (then re-run this to update)"
