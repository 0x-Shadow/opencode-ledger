#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 extract.py
echo
echo "Serve: python3 -m http.server 8765  →  http://localhost:8765/"
